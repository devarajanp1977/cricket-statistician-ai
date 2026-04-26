<#
.SYNOPSIS
    Provisions an Oracle Cloud Always-Free ARM Ampere VM for Cricket Statistician AI.

.DESCRIPTION
    Idempotent OCI CLI driver that creates (or reuses, by display name):
      - VCN (10.0.0.0/16)
      - Internet Gateway
      - Route Table with default route to IGW
      - Security List allowing SSH from anywhere (FastAPI is bound to 127.0.0.1
        and reached only via cloudflared, so no other ports are exposed)
      - Public Subnet (10.0.1.0/24)
      - VM.Standard.A1.Flex instance (4 OCPU, 24 GB RAM, 100 GB boot volume)
        running Ubuntu 22.04 ARM with cloud-init userdata.

    Reads OCI auth from the default ~/.oci/config profile.

    Re-running the script:
      - Does NOT recreate resources that already exist with the matching display name.
      - Does NOT terminate the VM. Use -ForceRecreateVm to replace it.
      - Always prints the final public IP and SSH command.

.PARAMETER CompartmentOcid
    Compartment to create resources in. Defaults to your tenancy root.

.PARAMETER AvailabilityDomain
    Full AD name, e.g. "Ymrr:US-SANJOSE-1-AD-1".

.PARAMETER ImageOcid
    Ubuntu 22.04 ARM image OCID for your region.

.PARAMETER SshPublicKeyPath
    Path to the SSH public key to inject into the VM.

.PARAMETER ForceRecreateVm
    Terminate any existing VM with the target display name and create a fresh one.

.EXAMPLE
    .\scripts\provision_oracle_vm.ps1
#>

[CmdletBinding()]
param(
    [string]$CompartmentOcid     = "ocid1.tenancy.oc1..aaaaaaaa76w3sl2rc4cpye7r55bl3b2l5m44sgx3nzbr6fqf6xf2yabrgdfq",
    [string]$AvailabilityDomain  = "Ymrr:US-SANJOSE-1-AD-1",
    [string]$ImageOcid           = "ocid1.image.oc1.us-sanjose-1.aaaaaaaa4gqbmqonbshhka76ohayugbwljb7k5asb4jiclywtdejsya5rhlq",
    [string]$VcnDisplayName      = "cricket-vcn",
    [string]$SubnetDisplayName   = "cricket-subnet",
    [string]$IgDisplayName       = "cricket-igw",
    [string]$RtDisplayName       = "cricket-rt",
    [string]$SlDisplayName       = "cricket-sl",
    [string]$VmDisplayName       = "cricket-statistician-vm",
    [string]$VcnCidr             = "10.0.0.0/16",
    [string]$SubnetCidr          = "10.0.1.0/24",
    [int]   $OcpuCount           = 4,
    [int]   $MemoryGb            = 24,
    [int]   $BootVolumeGb        = 100,
    [string]$SshPublicKeyPath    = (Join-Path $HOME ".ssh\oracle_cricket.pub"),
    [string]$CloudInitPath       = "",
    [int]   $MaxLaunchAttempts   = 60,
    [int]   $LaunchRetryDelaySec = 45,
    [switch]$AllowSmallerShape,
    [switch]$ForceRecreateVm,
    [string]$OciConfigFile       = "",
    [string]$OciProfile          = "DEFAULT"
)

$ErrorActionPreference = "Stop"

# $PSScriptRoot is not available in default param values, so resolve it here.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($CloudInitPath)) {
    $CloudInitPath = Join-Path $ScriptDir "cloud-init-cricket.yaml"
}

$script:OciGlobalArgs = @()
if (-not [string]::IsNullOrWhiteSpace($OciConfigFile)) {
    $script:OciGlobalArgs += @("--config-file", $OciConfigFile)
}
if (-not [string]::IsNullOrWhiteSpace($OciProfile) -and $OciProfile -ne "DEFAULT") {
    $script:OciGlobalArgs += @("--profile", $OciProfile)
}

# Ensure the OCI CLI is on PATH (Start-Process -NoProfile children may miss it).
if (-not (Get-Command oci -ErrorAction SilentlyContinue)) {
    foreach ($candidate in @("C:\oci\Scripts", "$env:USERPROFILE\bin", "$env:LOCALAPPDATA\Programs\oci\Scripts")) {
        if (Test-Path (Join-Path $candidate "oci.exe")) {
            $env:Path = "$candidate;" + $env:Path
            break
        }
    }
}

function Invoke-Oci {
    param([string[]]$OciArgs)
    $errFile = [System.IO.Path]::GetTempFileName()
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $combined = $script:OciGlobalArgs + $OciArgs
        $output = & oci @combined 2>$errFile
        $exit = $LASTEXITCODE
        $stderrText = (Get-Content -Path $errFile -Raw -ErrorAction SilentlyContinue)
        if ($exit -ne 0) {
            throw "oci $($OciArgs -join ' ') failed (exit $exit):`n$stderrText"
        }
        if ($output -is [array]) { return ($output -join "`n") }
        return [string]$output
    }
    finally {
        $ErrorActionPreference = $prevEAP
        Remove-Item -Path $errFile -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-OciJson {
    param([string[]]$OciArgs)
    $raw = Invoke-Oci -OciArgs $OciArgs
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    return ($raw | ConvertFrom-Json)
}

function ConvertTo-JsonArray {
    param([object[]]$Items, [int]$Depth = 6)
    # PS5 unwraps single-element arrays; build the array form explicitly.
    $parts = foreach ($item in $Items) { $item | ConvertTo-Json -Depth $Depth -Compress }
    return "[" + ($parts -join ",") + "]"
}

function Find-ByDisplayName {
    param(
        [string[]]$ListArgs,
        [string]  $DisplayName,
        [string]  $LifecycleStateExclude = "TERMINATED"
    )
    $resp = Invoke-OciJson -OciArgs $ListArgs
    if ($null -eq $resp -or $null -eq $resp.data) { return $null }
    $items = $resp.data | Where-Object {
        $_.'display-name' -eq $DisplayName -and $_.'lifecycle-state' -ne $LifecycleStateExclude
    }
    return ($items | Select-Object -First 1)
}

function Wait-ForState {
    param(
        [string[]]$GetArgs,
        [string]  $TargetState,
        [int]     $TimeoutSeconds = 600
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $resp = Invoke-OciJson -OciArgs $GetArgs
        $state = $resp.data.'lifecycle-state'
        Write-Host ("    state: {0}" -f $state)
        if ($state -eq $TargetState) { return $resp }
        if ($state -in @("FAILED","TERMINATED","TERMINATING")) {
            throw "Resource entered terminal state: $state"
        }
        Start-Sleep -Seconds 8
    }
    throw "Timeout waiting for state $TargetState"
}

# --- Sanity ---------------------------------------------------------------
if (-not (Test-Path $SshPublicKeyPath)) {
    throw "SSH public key not found at $SshPublicKeyPath. Run: ssh-keygen -t ed25519 -f `$HOME\.ssh\oracle_cricket -C cricketstats -N '`"`"'"
}
if (-not (Test-Path $CloudInitPath)) {
    throw "cloud-init file not found at $CloudInitPath"
}

Write-Host "=== Cricket Statistician AI: Oracle ARM VM provisioning ===" -ForegroundColor Cyan
Write-Host "Compartment        : $CompartmentOcid"
Write-Host "Availability Domain: $AvailabilityDomain"
Write-Host "Image              : $ImageOcid"
Write-Host "Shape              : VM.Standard.A1.Flex ($OcpuCount OCPU, $MemoryGb GB)"
Write-Host "Boot volume        : $BootVolumeGb GB"
Write-Host "SSH key            : $SshPublicKeyPath"
Write-Host "Cloud-init         : $CloudInitPath"
Write-Host ""

$sshPubKey = (Get-Content $SshPublicKeyPath -Raw).Trim()

# Encode cloud-init as base64 for --user-data
$ciBytes = [System.IO.File]::ReadAllBytes($CloudInitPath)
$ciB64   = [Convert]::ToBase64String($ciBytes)

# --- 1. VCN ---------------------------------------------------------------
Write-Host "[1/7] VCN"
$vcn = Find-ByDisplayName -ListArgs @("network","vcn","list","--compartment-id",$CompartmentOcid) -DisplayName $VcnDisplayName
if ($vcn) {
    Write-Host "    reusing $($vcn.id)"
} else {
    $vcn = (Invoke-OciJson @(
        "network","vcn","create",
        "--compartment-id",$CompartmentOcid,
        "--cidr-block",$VcnCidr,
        "--display-name",$VcnDisplayName,
        "--dns-label","cricketvcn",
        "--wait-for-state","AVAILABLE"
    )).data
    Write-Host "    created $($vcn.id)"
}
$vcnId = $vcn.id

# --- 2. Internet Gateway --------------------------------------------------
Write-Host "[2/7] Internet Gateway"
$ig = Find-ByDisplayName -ListArgs @("network","internet-gateway","list","--compartment-id",$CompartmentOcid,"--vcn-id",$vcnId) -DisplayName $IgDisplayName
if ($ig) {
    Write-Host "    reusing $($ig.id)"
} else {
    $ig = (Invoke-OciJson @(
        "network","internet-gateway","create",
        "--compartment-id",$CompartmentOcid,
        "--vcn-id",$vcnId,
        "--is-enabled","true",
        "--display-name",$IgDisplayName,
        "--wait-for-state","AVAILABLE"
    )).data
    Write-Host "    created $($ig.id)"
}
$igId = $ig.id

# --- 3. Route Table -------------------------------------------------------
Write-Host "[3/7] Route Table"
$rt = Find-ByDisplayName -ListArgs @("network","route-table","list","--compartment-id",$CompartmentOcid,"--vcn-id",$vcnId) -DisplayName $RtDisplayName
$routeRulesJson = ConvertTo-JsonArray -Items @(@{
    destination       = "0.0.0.0/0"
    destinationType   = "CIDR_BLOCK"
    networkEntityId   = $igId
})
# OCI CLI on Windows requires the JSON to be escaped; write to a temp file and pass file://
$rrFile = Join-Path $env:TEMP "oci-rr-$([guid]::NewGuid()).json"
Set-Content -Path $rrFile -Value $routeRulesJson -Encoding ASCII

if ($rt) {
    Write-Host "    reusing $($rt.id)"
    Invoke-Oci @("network","route-table","update","--rt-id",$rt.id,"--route-rules","file://$rrFile","--force") | Out-Null
} else {
    $rt = (Invoke-OciJson @(
        "network","route-table","create",
        "--compartment-id",$CompartmentOcid,
        "--vcn-id",$vcnId,
        "--display-name",$RtDisplayName,
        "--route-rules","file://$rrFile",
        "--wait-for-state","AVAILABLE"
    )).data
    Write-Host "    created $($rt.id)"
}
$rtId = $rt.id
Remove-Item $rrFile -Force -ErrorAction SilentlyContinue

# --- 4. Security List -----------------------------------------------------
Write-Host "[4/7] Security List"
$sl = Find-ByDisplayName -ListArgs @("network","security-list","list","--compartment-id",$CompartmentOcid,"--vcn-id",$vcnId) -DisplayName $SlDisplayName

# Egress: allow all
$egressJson = ConvertTo-JsonArray -Items @(@{
    destination     = "0.0.0.0/0"
    destinationType = "CIDR_BLOCK"
    protocol        = "all"
    isStateless     = $false
})

# Ingress: SSH from anywhere
$ingressJson = ConvertTo-JsonArray -Items @(@{
    source        = "0.0.0.0/0"
    sourceType    = "CIDR_BLOCK"
    protocol      = "6"
    isStateless   = $false
    tcpOptions    = @{
        destinationPortRange = @{ min = 22; max = 22 }
    }
})

$egFile = Join-Path $env:TEMP "oci-eg-$([guid]::NewGuid()).json"
$inFile = Join-Path $env:TEMP "oci-in-$([guid]::NewGuid()).json"
Set-Content -Path $egFile -Value $egressJson -Encoding ASCII
Set-Content -Path $inFile -Value $ingressJson -Encoding ASCII

if ($sl) {
    Write-Host "    reusing $($sl.id)"
    Invoke-Oci @("network","security-list","update","--security-list-id",$sl.id,"--egress-security-rules","file://$egFile","--ingress-security-rules","file://$inFile","--force") | Out-Null
} else {
    $sl = (Invoke-OciJson @(
        "network","security-list","create",
        "--compartment-id",$CompartmentOcid,
        "--vcn-id",$vcnId,
        "--display-name",$SlDisplayName,
        "--egress-security-rules","file://$egFile",
        "--ingress-security-rules","file://$inFile",
        "--wait-for-state","AVAILABLE"
    )).data
    Write-Host "    created $($sl.id)"
}
$slId = $sl.id
Remove-Item $egFile,$inFile -Force -ErrorAction SilentlyContinue

# --- 5. Subnet ------------------------------------------------------------
Write-Host "[5/7] Subnet"
$subnet = Find-ByDisplayName -ListArgs @("network","subnet","list","--compartment-id",$CompartmentOcid,"--vcn-id",$vcnId) -DisplayName $SubnetDisplayName
if ($subnet) {
    Write-Host "    reusing $($subnet.id)"
} else {
    $slIdsJson = ConvertTo-JsonArray -Items @($slId)
    $slFile = Join-Path $env:TEMP "oci-slids-$([guid]::NewGuid()).json"
    Set-Content -Path $slFile -Value $slIdsJson -Encoding ASCII
    $subnet = (Invoke-OciJson -OciArgs @(
        "network","subnet","create",
        "--compartment-id",$CompartmentOcid,
        "--vcn-id",$vcnId,
        "--cidr-block",$SubnetCidr,
        "--display-name",$SubnetDisplayName,
        "--dns-label","cricketsub",
        "--route-table-id",$rtId,
        "--security-list-ids","file://$slFile",
        "--prohibit-public-ip-on-vnic","false",
        "--wait-for-state","AVAILABLE"
    )).data
    Remove-Item $slFile -Force -ErrorAction SilentlyContinue
    Write-Host "    created $($subnet.id)"
}
$subnetId = $subnet.id

# --- 6. VM Instance -------------------------------------------------------
Write-Host "[6/7] VM Instance"
$existingVm = Find-ByDisplayName -ListArgs @("compute","instance","list","--compartment-id",$CompartmentOcid) -DisplayName $VmDisplayName

if ($existingVm -and $ForceRecreateVm) {
    Write-Host "    -ForceRecreateVm set; terminating $($existingVm.id)"
    Invoke-Oci @("compute","instance","terminate","--instance-id",$existingVm.id,"--force","--preserve-boot-volume","false","--wait-for-state","TERMINATED") | Out-Null
    $existingVm = $null
}

if ($existingVm) {
    Write-Host "    reusing $($existingVm.id) (use -ForceRecreateVm to replace)"
    $vm = $existingVm
} else {
    $metadataJson = (@{
        ssh_authorized_keys = $sshPubKey
        user_data           = $ciB64
    } | ConvertTo-Json -Compress)
    $mdFile = Join-Path $env:TEMP "oci-md-$([guid]::NewGuid()).json"
    Set-Content -Path $mdFile -Value $metadataJson -Encoding ASCII

    # Shape ladder: try the requested size first; if -AllowSmallerShape, fall back to halves.
    $shapeLadder = @(@{ ocpu = $OcpuCount; mem = $MemoryGb })
    if ($AllowSmallerShape) {
        $shapeLadder += @{ ocpu = 2; mem = 12 }
        $shapeLadder += @{ ocpu = 1; mem = 6 }
    }

    $vm = $null
    $attempt = 0
    :launch while ($attempt -lt $MaxLaunchAttempts -and -not $vm) {
        $attempt++
        foreach ($s in $shapeLadder) {
            $shapeConfigJson = (@{ ocpus = $s.ocpu; memoryInGBs = $s.mem } | ConvertTo-Json -Compress)
            $scFile = Join-Path $env:TEMP "oci-sc-$([guid]::NewGuid()).json"
            Set-Content -Path $scFile -Value $shapeConfigJson -Encoding ASCII

            Write-Host ("    attempt {0}/{1} - shape {2} OCPU / {3} GB ..." -f $attempt, $MaxLaunchAttempts, $s.ocpu, $s.mem)
            try {
                $launchResp = Invoke-OciJson -OciArgs @(
                    "compute","instance","launch",
                    "--availability-domain",$AvailabilityDomain,
                    "--compartment-id",$CompartmentOcid,
                    "--shape","VM.Standard.A1.Flex",
                    "--shape-config","file://$scFile",
                    "--image-id",$ImageOcid,
                    "--subnet-id",$subnetId,
                    "--display-name",$VmDisplayName,
                    "--assign-public-ip","true",
                    "--boot-volume-size-in-gbs",$BootVolumeGb.ToString(),
                    "--metadata","file://$mdFile"
                )
                $vm = $launchResp.data
                Remove-Item $scFile -Force -ErrorAction SilentlyContinue
                Write-Host "    instance id: $($vm.id) (shape: $($s.ocpu) OCPU / $($s.mem) GB)"
                break launch
            }
            catch {
                Remove-Item $scFile -Force -ErrorAction SilentlyContinue
                $msg = $_.Exception.Message
                if ($msg -match "Out of host capacity|TooManyRequests|InternalError") {
                    Write-Host "      capacity not available; trying next shape or retrying."
                    continue
                }
                Remove-Item $mdFile -Force -ErrorAction SilentlyContinue
                throw
            }
        }
        if (-not $vm) {
            Write-Host "      sleeping $LaunchRetryDelaySec s before next attempt..."
            Start-Sleep -Seconds $LaunchRetryDelaySec
        }
    }
    Remove-Item $mdFile -Force -ErrorAction SilentlyContinue
    if (-not $vm) {
        throw "Could not get ARM Ampere capacity in $AvailabilityDomain after $MaxLaunchAttempts attempts. Try a different region or rerun later. Re-run this script with -AllowSmallerShape to also try 2-OCPU and 1-OCPU fallbacks."
    }

    Write-Host "    waiting for RUNNING..."
    Wait-ForState -GetArgs @("compute","instance","get","--instance-id",$vm.id) -TargetState "RUNNING" -TimeoutSeconds 600 | Out-Null
}
$instanceId = $vm.id

# --- 7. Public IP ---------------------------------------------------------
Write-Host "[7/7] Public IP"
$vnicAttachments = (Invoke-OciJson @(
    "compute","vnic-attachment","list",
    "--compartment-id",$CompartmentOcid,
    "--instance-id",$instanceId
)).data
if (-not $vnicAttachments) { throw "No VNIC attachments found for instance $instanceId" }
$vnicId = $vnicAttachments[0].'vnic-id'
$vnic   = (Invoke-OciJson @("network","vnic","get","--vnic-id",$vnicId)).data
$publicIp = $vnic.'public-ip'

Write-Host ""
Write-Host "=== Provisioning complete ===" -ForegroundColor Green
Write-Host "Instance ID : $instanceId"
Write-Host "Public IP   : $publicIp"
Write-Host ""
Write-Host "SSH (as ubuntu, until cloud-init finishes):" -ForegroundColor Yellow
Write-Host "  ssh -i $SshPublicKeyPath.Replace('.pub','') ubuntu@$publicIp"
Write-Host ""
Write-Host "After cloud-init completes (~3-5 min), the cricket user is also reachable:"
Write-Host "  ssh -i $($SshPublicKeyPath -replace '\.pub$','') cricket@$publicIp"
Write-Host ""
Write-Host "Watch cloud-init progress:"
Write-Host "  ssh -i $($SshPublicKeyPath -replace '\.pub$','') ubuntu@$publicIp 'tail -f /var/log/cloud-init-output.log'"
Write-Host ""

# Persist the IP for the deploy script
$stateFile = Join-Path $ScriptDir ".oracle_vm_state.json"
@{
    instanceId = $instanceId
    publicIp   = $publicIp
    vcnId      = $vcnId
    subnetId   = $subnetId
    sshKeyPath = ($SshPublicKeyPath -replace '\.pub$','')
    timestamp  = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Path $stateFile -Encoding ASCII
Write-Host "State written to $stateFile"
