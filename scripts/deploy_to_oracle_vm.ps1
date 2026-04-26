<#
.SYNOPSIS
    Push the cricket-statistician-ai repo + DuckDB to the freshly-provisioned
    Oracle ARM VM, install Python deps, and start the systemd services.

.DESCRIPTION
    Reads the VM's public IP from scripts\.oracle_vm_state.json (written by
    provision_oracle_vm.ps1). Uses scp + ssh (Windows OpenSSH built-in) over
    the ed25519 keypair. Idempotent: re-running just rsyncs changes and
    restarts services.

    The cloud-init userdata already created the cricket user, the venv
    target dir /opt/cricket-statistician-ai, and the systemd unit files,
    so this script only:
      1. uploads the repo (excluding .git, .venv, transient logs, secrets)
      2. uploads the DuckDB files
      3. installs/updates the Python venv
      4. (re)starts cricket-api.service

.PARAMETER SkipDb
    Skip uploading data\db\*.duckdb (useful for code-only redeploys).

.PARAMETER SkipVenv
    Skip pip install (useful when nothing in requirements.txt changed).
#>
[CmdletBinding()]
param(
    [switch]$SkipDb,
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir

$statePath = Join-Path $ScriptDir ".oracle_vm_state.json"
if (-not (Test-Path $statePath)) {
    throw "VM state file not found: $statePath. Run provision_oracle_vm.ps1 first."
}
$state = Get-Content $statePath -Raw | ConvertFrom-Json
$vmIp  = $state.publicIp
$key   = $state.sshKeyPath
if (-not $vmIp) { throw "publicIp missing from state file." }
if (-not (Test-Path $key)) { throw "SSH key not found: $key" }

Write-Host "=== Deploying to cricket VM ===" -ForegroundColor Cyan
Write-Host "Target : ubuntu@$vmIp"
Write-Host "SSH key: $key"

# --- Helpers ----------------------------------------------------------------
function Invoke-RemoteSsh {
    param([string]$Cmd, [switch]$AsCricket)
    $user = if ($AsCricket) { "cricket" } else { "ubuntu" }
    Write-Host "  [$user] $Cmd" -ForegroundColor DarkGray
    & ssh -i $key -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL "$user@$vmIp" $Cmd
    if ($LASTEXITCODE -ne 0) { throw "ssh ($user) failed (exit $LASTEXITCODE): $Cmd" }
}

function Copy-Scp {
    param([string]$Local, [string]$Remote, [string]$User = "ubuntu", [switch]$Recurse)
    $opts = @("-i", $key, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL")
    if ($Recurse) { $opts += "-r" }
    Write-Host "  scp $Local -> ${User}@${vmIp}:$Remote" -ForegroundColor DarkGray
    & scp @opts $Local "${User}@${vmIp}:$Remote"
    if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE)" }
}

# --- 1. Wait for SSH --------------------------------------------------------
Write-Host "`n[1/6] Verifying SSH connectivity..."
$sshOk = $false
for ($i = 1; $i -le 30; $i++) {
    & ssh -i $key -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -o ConnectTimeout=5 -o BatchMode=yes "ubuntu@$vmIp" "echo ok" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $sshOk = $true; break }
    Write-Host "  attempt $i/30 - waiting 10s for SSH..."
    Start-Sleep -Seconds 10
}
if (-not $sshOk) { throw "SSH never came up on $vmIp" }
Write-Host "  SSH up." -ForegroundColor Green

# --- 2. Wait for cloud-init to finish --------------------------------------
Write-Host "`n[2/6] Waiting for cloud-init..."
Invoke-RemoteSsh "sudo cloud-init status --wait || true"

# --- 3. Stage tarball locally and upload -----------------------------------
Write-Host "`n[3/6] Building source tarball..."
$tmp     = Join-Path $env:TEMP "cricket-deploy"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp | Out-Null

$tarPath = Join-Path $tmp "cricket-src.tar.gz"
Push-Location $RepoRoot
try {
    # tar (Windows 10+ ships bsdtar) excludes large/secret/transient stuff.
    & tar --exclude='.git' `
          --exclude='.venv' `
          --exclude='__pycache__' `
          --exclude='.pytest_cache' `
          --exclude='node_modules' `
          --exclude='data/db' `
          --exclude='data/raw' `
          --exclude='data/logs' `
          --exclude='data/_*' `
          --exclude='data/*.log' `
          --exclude='data/*.txt' `
          --exclude='scripts/.secrets' `
          --exclude='scripts/.oracle_vm_state.json' `
          --exclude='*.duckdb' `
          --exclude='*.duckdb.wal' `
          --exclude='.env' `
          -czf $tarPath .
    if ($LASTEXITCODE -ne 0) { throw "tar failed (exit $LASTEXITCODE)" }
} finally { Pop-Location }

$sz = (Get-Item $tarPath).Length / 1MB
Write-Host ("  tarball: {0:N2} MB" -f $sz)

Copy-Scp -Local $tarPath -Remote "/tmp/cricket-src.tar.gz"

Write-Host "  unpacking on VM..."
Invoke-RemoteSsh "sudo install -d -o cricket -g cricket /opt/cricket-statistician-ai/data/db && sudo tar -xzf /tmp/cricket-src.tar.gz -C /opt/cricket-statistician-ai && sudo chown -R cricket:cricket /opt/cricket-statistician-ai && rm /tmp/cricket-src.tar.gz"

# --- 4. Upload DuckDB files (only if changed or forced) --------------------
if (-not $SkipDb) {
    Write-Host "`n[4/6] Uploading DuckDB files..."
    foreach ($db in @("cricket.duckdb", "cache.duckdb")) {
        $localDb = Join-Path $RepoRoot "data\db\$db"
        if (-not (Test-Path $localDb)) {
            Write-Host "  skip $db (not present locally)"
            continue
        }
        $sz = [Math]::Round((Get-Item $localDb).Length / 1MB, 1)
        Write-Host "  uploading $db ($sz MB) ..."
        Copy-Scp -Local $localDb -Remote "/tmp/$db"
        Invoke-RemoteSsh "sudo mv /tmp/$db /opt/cricket-statistician-ai/data/db/$db && sudo chown cricket:cricket /opt/cricket-statistician-ai/data/db/$db"
    }
} else {
    Write-Host "`n[4/6] -SkipDb set, leaving DuckDB files untouched."
}

# --- 5. Python venv + dependencies -----------------------------------------
if (-not $SkipVenv) {
    Write-Host "`n[5/6] Installing Python venv + dependencies..."
    $venvCmd = @(
        "set -e",
        "cd /opt/cricket-statistician-ai",
        "test -d .venv || python3.11 -m venv .venv",
        ".venv/bin/pip install --upgrade pip wheel",
        ".venv/bin/pip install -r requirements.txt",
        ".venv/bin/pip install 'pyjwt[crypto]>=2.8.0' 'cryptography>=42.0.0'"
    ) -join " && "
    Invoke-RemoteSsh -AsCricket $venvCmd
} else {
    Write-Host "`n[5/6] -SkipVenv set, skipping pip install."
}

# --- 6. Restart service ----------------------------------------------------
Write-Host "`n[6/6] Restarting cricket-api.service..."
Invoke-RemoteSsh "sudo systemctl daemon-reload && sudo systemctl enable --now cricket-api.service && sudo systemctl restart cricket-api.service && sleep 2 && sudo systemctl status cricket-api.service --no-pager | head -n 15"

Write-Host "`n=== Deploy complete ===" -ForegroundColor Green
Write-Host "API listening on http://127.0.0.1:8080 inside the VM."
Write-Host "Next step: configure the Cloudflare tunnel (scripts\setup_cloudflare_tunnel.ps1)."
