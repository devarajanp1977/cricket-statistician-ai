<#
.SYNOPSIS
    Deploy/redeploy Cricket Statistician AI code to a Contabo VPS.

.DESCRIPTION
    Packages the repo into a tarball, uploads via scp (password auth),
    installs/updates the Python venv, uploads the .env file, and restarts
    the cricket-api systemd service.

    Run bootstrap_contabo.ps1 once first before using this script.

.PARAMETER ServerIp
    Public IP of the Contabo VPS.

.PARAMETER RootPassword
    SSH password.

.PARAMETER SshUser
    SSH login user. Default: root.

.PARAMETER SkipDb
    Skip uploading data\db\*.duckdb files.

.PARAMETER SkipVenv
    Skip pip install (use when only code changed, not requirements.txt).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ServerIp,
    [Parameter(Mandatory)][string]$RootPassword,
    [string]$SshUser = "root",
    [switch]$SkipDb,
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
$haveSshpass = Get-Command sshpass -ErrorAction SilentlyContinue
$havePlink   = Get-Command plink   -ErrorAction SilentlyContinue

if (-not $haveSshpass -and -not $havePlink) {
    throw "Neither sshpass nor plink found. Install PuTTY (winget install PuTTY.PuTTY) and retry."
}

function Invoke-Remote {
    param([string]$Cmd, [string]$User = $SshUser)
    Write-Host "  [$User] $Cmd" -ForegroundColor DarkGray
    if ($haveSshpass) {
        & sshpass -p $RootPassword ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL "$User@$ServerIp" $Cmd
    } else {
        & plink -ssh -pw $RootPassword -batch "$User@$ServerIp" $Cmd
    }
    if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE)" }
}

function Copy-ToServer {
    param([string]$Local, [string]$Remote, [string]$User = $SshUser, [switch]$Recurse)
    Write-Host "  scp $Local -> ${User}@${ServerIp}:$Remote" -ForegroundColor DarkGray
    if ($haveSshpass) {
        $opts = @("-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=NUL")
        if ($Recurse) { $opts += "-r" }
        & sshpass -p $RootPassword scp @opts $Local "${User}@${ServerIp}:$Remote"
    } else {
        $opts = @("-pw",$RootPassword,"-batch")
        if ($Recurse) { $opts += "-r" }
        & pscp @opts $Local "${User}@${ServerIp}:$Remote"
    }
    if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE)" }
}

# Cache host key in PuTTY registry on first run.
if (-not $haveSshpass -and $havePlink) {
    Write-Host "Caching server host key..."
    @("y", "") | & plink -ssh -pw $RootPassword "$SshUser@$ServerIp" "echo ok" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to cache host key (exit $LASTEXITCODE)" }
}

Write-Host "=== Deploy to Contabo VPS ===" -ForegroundColor Cyan
Write-Host "Target: $SshUser@$ServerIp"

# --------------------------------------------------------------------------
# 1. Verify SSH is reachable
# --------------------------------------------------------------------------
Write-Host "`n[1/5] Checking SSH connectivity..."
Invoke-Remote "echo ok"
Write-Host "  connected." -ForegroundColor Green

# --------------------------------------------------------------------------
# 2. Build and upload source tarball
# --------------------------------------------------------------------------
Write-Host "`n[2/5] Building source tarball..."
$tmp = Join-Path $env:TEMP "cricket-deploy"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp | Out-Null
$tarPath = Join-Path $tmp "cricket-src.tar.gz"

Push-Location $RepoRoot
try {
    & tar --exclude='.git' `
          --exclude='.venv' `
          --exclude='__pycache__' `
          --exclude='.pytest_cache' `
          --exclude='node_modules' `
          --exclude='mobile/node_modules' `
          --exclude='mobile/android/.gradle' `
          --exclude='mobile/android/app/build' `
          --exclude='data/db' `
          --exclude='data/raw' `
          --exclude='data/logs' `
          --exclude='scripts/.secrets' `
          --exclude='*.duckdb' `
          --exclude='*.duckdb.wal' `
          --exclude='.env' `
          -czf $tarPath .
    if ($LASTEXITCODE -ne 0) { throw "tar failed (exit $LASTEXITCODE)" }
} finally { Pop-Location }

$sz = [Math]::Round((Get-Item $tarPath).Length / 1MB, 1)
Write-Host "  tarball: $sz MB"
Copy-ToServer -Local $tarPath -Remote "/tmp/cricket-src.tar.gz"

Write-Host "  unpacking..."
Invoke-Remote "install -d -o cricket -g cricket /opt/cricket-statistician-ai && tar -xzf /tmp/cricket-src.tar.gz -C /opt/cricket-statistician-ai && chown -R cricket:cricket /opt/cricket-statistician-ai && rm /tmp/cricket-src.tar.gz"

# --------------------------------------------------------------------------
# 3. Upload .env
# --------------------------------------------------------------------------
Write-Host "`n[3/5] Uploading .env..."
$localEnv = Join-Path $RepoRoot ".env"
if (Test-Path $localEnv) {
    Copy-ToServer -Local $localEnv -Remote "/tmp/cricket.env"
    Invoke-Remote "mv /tmp/cricket.env /opt/cricket-statistician-ai/.env && chown cricket:cricket /opt/cricket-statistician-ai/.env && chmod 600 /opt/cricket-statistician-ai/.env"
} else {
    Write-Host "  WARNING: no .env found at $localEnv - skipping. Create it on the server manually." -ForegroundColor Yellow
}

# --------------------------------------------------------------------------
# 4. Upload DuckDB files
# --------------------------------------------------------------------------
if (-not $SkipDb) {
    Write-Host "`n[4/5] Uploading DuckDB files..."
    foreach ($db in @("cricket.duckdb", "cache.duckdb")) {
        $localDb = Join-Path $RepoRoot "data\db\$db"
        if (-not (Test-Path $localDb)) {
            Write-Host "  skip $db (not found locally)"
            continue
        }
        $sz = [Math]::Round((Get-Item $localDb).Length / 1MB, 1)
        Write-Host "  $db ($sz MB)..."
        Copy-ToServer -Local $localDb -Remote "/tmp/$db"
        Invoke-Remote "mv /tmp/$db /opt/cricket-statistician-ai/data/db/$db && chown cricket:cricket /opt/cricket-statistician-ai/data/db/$db"
    }
} else {
    Write-Host "`n[4/5] -SkipDb set, skipping DuckDB upload."
}

# --------------------------------------------------------------------------
# 5. Python venv + restart service
# --------------------------------------------------------------------------
if (-not $SkipVenv) {
    Write-Host "`n[5/5] Python venv + pip install..."
    $venvCmd = "cd /opt/cricket-statistician-ai && (test -d .venv || python3.11 -m venv .venv) && .venv/bin/pip install --quiet --upgrade pip wheel && .venv/bin/pip install --quiet -r requirements.txt && .venv/bin/pip install --quiet 'pyjwt[crypto]>=2.8.0' 'cryptography>=42.0.0'"
    Invoke-Remote -Cmd "su -s /bin/bash cricket -c '$venvCmd'"
} else {
    Write-Host "`n[5/5] -SkipVenv set, skipping pip install."
}

Write-Host "`n  Restarting cricket-api.service..."
Invoke-Remote "systemctl daemon-reload && systemctl enable --now cricket-api.service && systemctl restart cricket-api.service && sleep 3 && systemctl status cricket-api.service --no-pager | head -20"

Write-Host "`n=== Deploy complete ===" -ForegroundColor Green
Write-Host "API is live at: http://${ServerIp}:8080/health"
Write-Host ""
Write-Host "When DNS is ready, run:"
Write-Host "  certbot --nginx -d cricket-api.devarajan.in" -ForegroundColor Cyan
