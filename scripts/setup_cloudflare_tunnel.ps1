<#
.SYNOPSIS
    Create a Cloudflare Tunnel for the cricket-statistician backend, route
    a hostname to it, and stage the config + credentials onto the Oracle VM.

.DESCRIPTION
    Prerequisites (one-time, interactive):
      1. cloudflared tunnel login    -- opens browser, click your zone,
                                        writes ~/.cloudflared/cert.pem
      2. A zone (domain) on Cloudflare you control. Pass via -Hostname.
         Free options if you don't own a domain:
           - Get a free .is-a.dev or .js.org subdomain (PR-based)
           - Or just use Cloudflare's Trycloudflare quick tunnel (no DNS)
             -- but those URLs change on every restart; not suitable here.

    What this script does (idempotent):
      1. Creates a named tunnel `cricket` (reuses if it exists).
      2. Creates a CNAME DNS record  $Hostname  ->  <tunnel-id>.cfargotunnel.com
      3. Writes ~/.cloudflared/config.yml referencing the tunnel + creds.
      4. Uploads the credentials JSON + config.yml to the VM under
         /home/cricket/.cloudflared/.
      5. Enables and starts cricket-tunnel.service on the VM.

.PARAMETER Hostname
    Public hostname to expose, e.g. cricketstats.example.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Hostname,

    [string]$TunnelName = "cricket"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- 0. Sanity checks ------------------------------------------------------
$cert = Join-Path $env:USERPROFILE ".cloudflared\cert.pem"
if (-not (Test-Path $cert)) {
    throw "Missing $cert. Run 'cloudflared tunnel login' first (opens browser)."
}
$statePath = Join-Path $ScriptDir ".oracle_vm_state.json"
if (-not (Test-Path $statePath)) {
    throw "VM state file not found: $statePath. Run provision_oracle_vm.ps1 + deploy_to_oracle_vm.ps1 first."
}
$state = Get-Content $statePath -Raw | ConvertFrom-Json
$vmIp = $state.publicIp
$key  = $state.sshKeyPath

Write-Host "=== Cloudflare Tunnel setup ===" -ForegroundColor Cyan
Write-Host "Tunnel  : $TunnelName"
Write-Host "Hostname: $Hostname"
Write-Host "VM      : $vmIp"

# --- 1. Create tunnel (or reuse) ------------------------------------------
Write-Host "`n[1/5] Ensuring tunnel exists..."
$existing = & cloudflared tunnel list --output json 2>$null | ConvertFrom-Json
$tunnel = $existing | Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1
if ($tunnel) {
    Write-Host "  reusing tunnel $($tunnel.id)"
    $tunnelId = $tunnel.id
} else {
    $createOut = & cloudflared tunnel create $TunnelName 2>&1 | Out-String
    Write-Host $createOut
    if ($createOut -match "([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})") {
        $tunnelId = $matches[1]
    } else {
        throw "Could not parse tunnel ID from output."
    }
}

$credsFile = Join-Path $env:USERPROFILE ".cloudflared\$tunnelId.json"
if (-not (Test-Path $credsFile)) {
    throw "Tunnel credentials file missing: $credsFile"
}
Write-Host "  credentials: $credsFile" -ForegroundColor DarkGray

# --- 2. DNS route ---------------------------------------------------------
Write-Host "`n[2/5] Routing DNS $Hostname -> tunnel..."
$dnsOut = & cloudflared tunnel route dns $TunnelName $Hostname 2>&1 | Out-String
Write-Host $dnsOut

# --- 3. Build config.yml --------------------------------------------------
Write-Host "`n[3/5] Writing config.yml..."
$cfg = @"
tunnel: $tunnelId
credentials-file: /home/cricket/.cloudflared/$tunnelId.json
no-autoupdate: true

ingress:
  - hostname: $Hostname
    service: http://127.0.0.1:8080
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s
  - service: http_status:404
"@
$cfgLocal = Join-Path $env:TEMP "cricket-cf-config.yml"
Set-Content -Path $cfgLocal -Value $cfg -NoNewline -Encoding ASCII

# --- 4. Upload to VM ------------------------------------------------------
Write-Host "`n[4/5] Uploading to VM..."
$opts = @("-i", $key, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL")
& ssh @opts "ubuntu@$vmIp" "sudo install -d -o cricket -g cricket -m 700 /home/cricket/.cloudflared"
if ($LASTEXITCODE -ne 0) { throw "ssh mkdir failed" }
& scp @opts $credsFile "ubuntu@${vmIp}:/tmp/$tunnelId.json"
if ($LASTEXITCODE -ne 0) { throw "scp creds failed" }
& scp @opts $cfgLocal   "ubuntu@${vmIp}:/tmp/config.yml"
if ($LASTEXITCODE -ne 0) { throw "scp config failed" }
& ssh @opts "ubuntu@$vmIp" "sudo mv /tmp/$tunnelId.json /home/cricket/.cloudflared/ && sudo mv /tmp/config.yml /home/cricket/.cloudflared/config.yml && sudo chown -R cricket:cricket /home/cricket/.cloudflared && sudo chmod 600 /home/cricket/.cloudflared/$tunnelId.json"
if ($LASTEXITCODE -ne 0) { throw "ssh stage failed" }

# --- 5. Start the service -------------------------------------------------
Write-Host "`n[5/5] Starting cricket-tunnel.service on VM..."
& ssh @opts "ubuntu@$vmIp" "sudo systemctl daemon-reload && sudo systemctl enable --now cricket-tunnel.service && sleep 2 && sudo systemctl status cricket-tunnel.service --no-pager | head -n 20"
if ($LASTEXITCODE -ne 0) { throw "ssh start service failed" }

Remove-Item $cfgLocal -Force -ErrorAction SilentlyContinue

Write-Host "`n=== Cloudflare tunnel up ===" -ForegroundColor Green
Write-Host "Public URL: https://$Hostname"
Write-Host "Tunnel ID : $tunnelId"
Write-Host ""
Write-Host "DNS may take 30-60 seconds to propagate. Test with:"
Write-Host "  curl https://$Hostname/health"
