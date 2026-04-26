<#
.SYNOPSIS
    First-time bootstrap of a fresh Contabo (or any Ubuntu) VPS for
    Cricket Statistician AI.  Run once after the server is up.

.DESCRIPTION
    SSHes into the server using sshpass (via plink from PuTTY, which ships
    with Windows) OR via the OpenSSH -o options, installs all system deps,
    creates the cricket user, installs systemd units, and configures nginx as
    a reverse proxy on port 80/443 (Certbot TLS can be added later once DNS
    is pointed).

    After this script succeeds, run deploy_to_contabo.ps1 to push the code.

.PARAMETER ServerIp
    Public IP of the Contabo VPS.

.PARAMETER RootPassword
    Root (or sudo user) password for initial SSH access.

.PARAMETER SshUser
    Login user on the remote server. Default: root.

.PARAMETER ApiDomain
    The subdomain that will serve the API, e.g. cricket-api.devarajan.in
    Used to pre-configure nginx. Can be just the IP until DNS is ready.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ServerIp,
    [Parameter(Mandatory)][string]$RootPassword,
    [string]$SshUser   = "root",
    [string]$ApiDomain = ""
)

$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------
# We use the Windows built-in ssh via a temp expect-like approach:
# Write a setup script to the server then run it.
# --------------------------------------------------------------------------

$nginxServerName = if ($ApiDomain) { $ApiDomain } else { $ServerIp }

$remoteScript = @"
#!/usr/bin/env bash
set -euo pipefail

echo "=== [1/7] System packages ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y software-properties-common curl ca-certificates ufw git \
    build-essential rsync jq unzip sqlite3 nginx certbot python3-certbot-nginx

echo "=== [2/7] Python 3.11 ==="
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -y
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip

echo "=== [3/7] cricket user + directories ==="
id -u cricket >/dev/null 2>&1 || useradd -m -s /bin/bash cricket
mkdir -p /opt/cricket-statistician-ai/data/db \
         /opt/cricket-statistician-ai/data/logs \
         /home/cricket/.ssh
# Allow key-based SSH for cricket user (inherit root authorized_keys if present)
if [ -f /root/.ssh/authorized_keys ]; then
    cp /root/.ssh/authorized_keys /home/cricket/.ssh/authorized_keys
    chmod 600 /home/cricket/.ssh/authorized_keys
fi
chmod 700 /home/cricket/.ssh
chown -R cricket:cricket /opt/cricket-statistician-ai /home/cricket

echo "=== [4/7] systemd unit: cricket-api ==="
cat > /etc/systemd/system/cricket-api.service <<'UNIT'
[Unit]
Description=Cricket Statistician AI FastAPI
After=network.target

[Service]
Type=simple
User=cricket
Group=cricket
WorkingDirectory=/opt/cricket-statistician-ai
EnvironmentFile=-/opt/cricket-statistician-ai/.env
ExecStart=/opt/cricket-statistician-ai/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable cricket-api.service

echo "=== [5/7] nginx reverse proxy ==="
cat > /etc/nginx/sites-available/cricket-api <<NGINX
server {
    listen 80;
    server_name $nginxServerName;

    client_max_body_size 10M;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host `$host;
        proxy_set_header   X-Real-IP `$remote_addr;
        proxy_set_header   X-Forwarded-For `$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto `$scheme;
        proxy_read_timeout 120s;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/cricket-api /etc/nginx/sites-enabled/cricket-api
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable --now nginx && systemctl reload nginx

echo "=== [6/7] Firewall ==="
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "=== [7/7] Done ==="
echo "Server ready. Now run deploy_to_contabo.ps1 to push the app code."
"@

# Write the bootstrap script to a temp file
$tmpScript = [System.IO.Path]::GetTempFileName() + ".sh"
# Write without BOM and with Unix LF line endings (bash rejects CRLF and BOM)
$scriptUnix = $remoteScript -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($tmpScript, $scriptUnix, [System.Text.UTF8Encoding]::new($false))

Write-Host "=== Contabo VPS Bootstrap ===" -ForegroundColor Cyan
Write-Host "Server : $SshUser@$ServerIp"
Write-Host "Domain : $nginxServerName"
Write-Host ""

# Check for sshpass (needed for password auth in non-interactive mode)
$haveSshpass = Get-Command sshpass -ErrorAction SilentlyContinue
$havePlink   = Get-Command plink -ErrorAction SilentlyContinue

if (-not $haveSshpass -and -not $havePlink) {
    Write-Host @"
Neither 'sshpass' nor 'plink' found. Install one of:
  - PuTTY (includes plink): winget install PuTTY.PuTTY
  - sshpass via WSL: available in WSL Ubuntu with: apt install sshpass
  - Git Bash: bundled sshpass available via package managers

Alternatively, manually copy-paste the commands below into an SSH session:
  ssh $SshUser@$ServerIp
"@ -ForegroundColor Yellow
    Write-Host "`nScript content saved to: $tmpScript" -ForegroundColor Yellow
    exit 1
}

# Cache host key in PuTTY registry by piping "y" once (non-batch).
Write-Host "Caching server host key..."
"y" | & plink -ssh -pw $RootPassword "$SshUser@$ServerIp" "echo host-key-cached" 2>&1 | Out-Null

function Invoke-RemoteCmd {
    param([string]$Cmd)
    if ($haveSshpass) {
        & sshpass -p $RootPassword ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL "$SshUser@$ServerIp" $Cmd
    } else {
        & plink -ssh -pw $RootPassword -batch "$SshUser@$ServerIp" $Cmd
    }
    if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE): $Cmd" }
}

function Copy-ScpPassword {
    param([string]$Local, [string]$Remote)
    if ($haveSshpass) {
        & sshpass -p $RootPassword scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL $Local "${SshUser}@${ServerIp}:$Remote"
    } else {
        & pscp -pw $RootPassword -batch $Local "${SshUser}@${ServerIp}:$Remote"
    }
    if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE)" }
}

Write-Host "[1/3] Uploading bootstrap script..."
Copy-ScpPassword -Local $tmpScript -Remote "/tmp/bootstrap.sh"

Write-Host "[2/3] Running bootstrap (this takes 3-5 minutes)..."
Invoke-RemoteCmd "chmod +x /tmp/bootstrap.sh && /tmp/bootstrap.sh && rm /tmp/bootstrap.sh"

Write-Host "[3/3] Done." -ForegroundColor Green
Write-Host ""
Write-Host "Next step: run deploy_to_contabo.ps1 to push the app." -ForegroundColor Cyan

Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue
