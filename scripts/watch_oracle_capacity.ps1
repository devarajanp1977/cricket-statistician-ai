<#
.SYNOPSIS
    Polls Oracle Cloud for ARM Ampere capacity and launches the cricket-statistician-vm
    the moment a slot opens up.

.DESCRIPTION
    Designed to be run in a dedicated PowerShell window and left alone. Logs every attempt
    to data\logs\oracle-launch-watch-<timestamp>.log. Exits 0 once the VM is RUNNING and
    its public IP has been written to scripts\.oracle_vm_state.json.

    Assumes the VCN/subnet/etc were already created by provision_oracle_vm.ps1.

.PARAMETER IntervalSeconds
    Delay between launch attempts. Default 60.

.PARAMETER MaxMinutes
    Hard cap on total runtime. Default 720 (12 hours). Set to 0 for no limit.

.PARAMETER AllowSmallerShape
    If set, falls back from 4/24 to 2/12 to 1/6 within each attempt cycle.
#>

[CmdletBinding()]
param(
    [int]   $IntervalSeconds   = 60,
    [int]   $MaxMinutes        = 720,
    [switch]$AllowSmallerShape,
    [string]$OciConfigFile     = "",
    [string]$OciProfile        = "DEFAULT",
    [string]$CompartmentOcid   = "",
    [string]$AvailabilityDomain= "",
    [string]$ImageOcid         = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Split-Path -Parent $ScriptDir
$LogDir      = Join-Path $RepoRoot "data\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile     = Join-Path $LogDir ("oracle-launch-watch-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Write-Log "=== Oracle ARM capacity watcher starting ==="
Write-Log "Interval: $IntervalSeconds s | Max minutes: $MaxMinutes | Smaller-shape fallback: $($AllowSmallerShape.IsPresent)"
Write-Log "Log: $LogFile"

$started = Get-Date
$attempt = 0

while ($true) {
    $attempt++

    $params = @(
        "-NoProfile",
        "-ExecutionPolicy","Bypass",
        "-File", (Join-Path $ScriptDir "provision_oracle_vm.ps1"),
        "-MaxLaunchAttempts","1"
    )
    if ($AllowSmallerShape) { $params += "-AllowSmallerShape" }
    if (-not [string]::IsNullOrWhiteSpace($OciConfigFile))      { $params += @("-OciConfigFile", $OciConfigFile) }
    if (-not [string]::IsNullOrWhiteSpace($OciProfile) -and $OciProfile -ne "DEFAULT") { $params += @("-OciProfile", $OciProfile) }
    if (-not [string]::IsNullOrWhiteSpace($CompartmentOcid))    { $params += @("-CompartmentOcid", $CompartmentOcid) }
    if (-not [string]::IsNullOrWhiteSpace($AvailabilityDomain)) { $params += @("-AvailabilityDomain", $AvailabilityDomain) }
    if (-not [string]::IsNullOrWhiteSpace($ImageOcid))          { $params += @("-ImageOcid", $ImageOcid) }

    Write-Log "Attempt #$attempt"
    $tmpOut = [System.IO.Path]::GetTempFileName()
    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $params -NoNewWindow -PassThru -RedirectStandardOutput $tmpOut -RedirectStandardError "$tmpOut.err" -Wait
    $stdout = (Get-Content $tmpOut -Raw -ErrorAction SilentlyContinue)
    $stderr = (Get-Content "$tmpOut.err" -Raw -ErrorAction SilentlyContinue)
    Remove-Item $tmpOut,"$tmpOut.err" -Force -ErrorAction SilentlyContinue

    if ($proc.ExitCode -eq 0) {
        Write-Log "Launch succeeded."
        Write-Log $stdout
        $statePath = Join-Path $ScriptDir ".oracle_vm_state.json"
        if (Test-Path $statePath) {
            $state = Get-Content $statePath -Raw | ConvertFrom-Json
            Write-Log "VM Public IP: $($state.publicIp)"
            Write-Log "Instance ID : $($state.instanceId)"
        }
        Write-Log "=== DONE ==="
        exit 0
    }

    # Look for the specific 'Out of host capacity' signal vs other errors
    $combined = "$stdout`n$stderr"
    if ($combined -match "Out of host capacity|TooManyRequests|InternalError") {
        Write-Log "  capacity unavailable (exit $($proc.ExitCode)). Sleeping ${IntervalSeconds}s."
    } else {
        Write-Log "  unexpected error (exit $($proc.ExitCode)):"
        Write-Log $combined
        Write-Log "  sleeping ${IntervalSeconds}s anyway."
    }

    if ($MaxMinutes -gt 0) {
        $elapsed = (Get-Date) - $started
        if ($elapsed.TotalMinutes -ge $MaxMinutes) {
            Write-Log "Max runtime reached ($MaxMinutes min). Giving up."
            exit 2
        }
    }

    Start-Sleep -Seconds $IntervalSeconds
}
