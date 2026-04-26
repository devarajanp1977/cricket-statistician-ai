[CmdletBinding()]
param(
    [string]$TargetHost = "127.0.0.1",
    [int]$Port = 8080,
    [string]$AppBasePath = "",
    [switch]$NoBrowser,
    [switch]$Inline,
    [int]$StartupTimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"

function Resolve-PythonLauncher {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            Command = $python.Source
            Args = @()
            Label = $python.Source
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @{
            Command = $py.Source
            Args = @("-3")
            Label = "$($py.Source) -3"
        }
    }

    throw "Could not find 'python' or 'py' on PATH. Install Python 3 or activate your environment first."
}

function ConvertTo-AppBasePath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }

    $normalized = $Value.Trim()
    if (-not $normalized.StartsWith("/")) {
        $normalized = "/$normalized"
    }

    return $normalized.TrimEnd("/")
}

function Get-ConfiguredEnvValue([string]$FilePath, [string]$Name) {
    if (-not (Test-Path -LiteralPath $FilePath)) {
        return ""
    }

    foreach ($line in Get-Content -LiteralPath $FilePath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }

        $key = $parts[0].Trim()
        if ($key.StartsWith("export ")) {
            $key = $key.Substring(7).Trim()
        }

        if ($key -ne $Name) {
            continue
        }

        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        return $value
    }

    return ""
}

function Get-GithubToken([string]$RepoRoot) {
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_TOKEN)) {
        return $env:GITHUB_TOKEN
    }

    $envFile = Join-Path $RepoRoot ".env"
    return Get-ConfiguredEnvValue -FilePath $envFile -Name "GITHUB_TOKEN"
}

function Get-PortListener([int]$LocalPort) {
    $listener = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $listener) {
        return $null
    }

    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        OwningProcess = $listener.OwningProcess
        LocalAddress = $listener.LocalAddress
        LocalPort = $listener.LocalPort
        ProcessName = if ($process) { $process.ProcessName } else { "unknown" }
    }
}

function Test-RequiredFile([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Description not found at $Path"
    }
}

function Test-ServiceReady([string]$HealthUrl, [string]$StatsUrl, [string]$RootUrl, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = "Service did not become ready."

    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
            if ($health.status -ne "ok") {
                $lastError = "Health endpoint returned an unexpected payload."
                Start-Sleep -Milliseconds 500
                continue
            }

            $root = Invoke-WebRequest -UseBasicParsing -Uri $RootUrl -TimeoutSec 2
            if ($root.StatusCode -lt 200 -or $root.StatusCode -ge 400) {
                $lastError = "Frontend route returned HTTP $($root.StatusCode)."
                Start-Sleep -Milliseconds 500
                continue
            }

            $stats = Invoke-RestMethod -Uri $StatsUrl -TimeoutSec 5
            return [pscustomobject]@{
                Ready = $true
                LastError = $null
                Stats = $stats
            }
        } catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Milliseconds 500
        }
    }

    return [pscustomobject]@{
        Ready = $false
        LastError = $lastError
        Stats = $null
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$launcher = Resolve-PythonLauncher
$normalizedBasePath = ConvertTo-AppBasePath $AppBasePath
$appMainPath = Join-Path $repoRoot "app\main.py"
$frontendIndexPath = Join-Path $repoRoot "frontend\index.html"
$dbPath = Join-Path $repoRoot "data\db\cricket.duckdb"
$logDir = Join-Path $repoRoot "data\logs"
$localFrontendUrl = "http://${TargetHost}:$Port/"
$healthUrl = "http://${TargetHost}:$Port/health"
$statsUrl = "http://${TargetHost}:$Port/api/stats"
$reverseProxyUrl = if ($normalizedBasePath) { "http://${TargetHost}:$Port$normalizedBasePath/" } else { $null }

Test-RequiredFile -Path $appMainPath -Description "FastAPI app entrypoint"
Test-RequiredFile -Path $frontendIndexPath -Description "Frontend index"
Test-RequiredFile -Path $dbPath -Description "DuckDB database"

$dbFile = Get-Item -LiteralPath $dbPath
if ($dbFile.Length -le 0) {
    throw "DuckDB database file exists but is empty: $dbPath"
}

$githubToken = Get-GithubToken -RepoRoot $repoRoot
if ([string]::IsNullOrWhiteSpace($githubToken)) {
    throw "GITHUB_TOKEN is not configured in the environment or .env. The statistics tool cannot use the LLM until that token is set."
}

$listener = Get-PortListener -LocalPort $Port
if ($listener) {
    $existingService = Test-ServiceReady -HealthUrl $healthUrl -StatsUrl $statsUrl -RootUrl $localFrontendUrl -TimeoutSeconds 2
    if ($existingService.Ready) {
        Write-Host "Cricket Statistician AI is already running on port $Port (PID $($listener.OwningProcess), process $($listener.ProcessName))." -ForegroundColor Green
        Write-Host "Local UI: $localFrontendUrl" -ForegroundColor Green
        if ($normalizedBasePath) {
            Write-Host "APP_BASE_PATH is set to $normalizedBasePath for reverse-proxy deployments. Direct local access remains $localFrontendUrl" -ForegroundColor Yellow
            Write-Host "Reverse-proxy URL shape: $reverseProxyUrl" -ForegroundColor Yellow
        }
        if (-not $NoBrowser) {
            Start-Process $localFrontendUrl | Out-Null
        }
        return
    }

    throw "Port $Port is already in use by PID $($listener.OwningProcess) ($($listener.ProcessName)), and the Cricket Statistician app is not healthy on that port. Stop the conflicting process or choose a different -Port."
}

Write-Host "Starting Cricket Statistician AI..." -ForegroundColor Cyan
Write-Host "Repo root: $repoRoot"
Write-Host "Backend API: http://${TargetHost}:$Port"
Write-Host "Frontend UI: $localFrontendUrl"
Write-Host "Python: $($launcher.Label)"
Write-Host "DuckDB: $dbPath"

if ($normalizedBasePath) {
    Write-Host "APP_BASE_PATH is set to $normalizedBasePath for reverse-proxy deployments." -ForegroundColor Yellow
    Write-Host "Direct local access remains $localFrontendUrl" -ForegroundColor Yellow
    Write-Host "Reverse-proxy URL shape: $reverseProxyUrl" -ForegroundColor Yellow
}

if ($Inline) {
    Set-Location -LiteralPath $repoRoot
    if ($normalizedBasePath) {
        $env:APP_BASE_PATH = $normalizedBasePath
    } else {
        Remove-Item Env:APP_BASE_PATH -ErrorAction SilentlyContinue
    }

    Write-Host "Running inline. Press Ctrl+C to stop the app." -ForegroundColor Yellow
    & $launcher.Command @($launcher.Args + @("-m", "uvicorn", "app.main:app", "--host", $TargetHost, "--port", "$Port"))
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logFile = Join-Path $logDir "start-local-services-$Port-$timestamp.log"
$escapedRepoRoot = $repoRoot.Replace("'", "''")
$escapedBasePath = $normalizedBasePath.Replace("'", "''")
$escapedPython = $launcher.Command.Replace("'", "''")
$escapedLogFile = $logFile.Replace("'", "''")
$pythonArgsLiteral = ($launcher.Args + @("-m", "uvicorn", "app.main:app", "--host", $TargetHost, "--port", "$Port")) |
    ForEach-Object { "'{0}'" -f ($_.Replace("'", "''")) }
$pythonInvocation = "& '$escapedPython' " + ($pythonArgsLiteral -join " ") + " 2>&1 | Tee-Object -FilePath '$escapedLogFile' -Append"

$startupCommand = @"
Set-Location -LiteralPath '$escapedRepoRoot'
if ('$escapedBasePath') {
    `$env:APP_BASE_PATH = '$escapedBasePath'
} else {
    Remove-Item Env:APP_BASE_PATH -ErrorAction SilentlyContinue
}
$pythonInvocation
"@

$process = Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $startupCommand) `
    -WorkingDirectory $repoRoot `
    -PassThru

$serviceStatus = Test-ServiceReady -HealthUrl $healthUrl -StatsUrl $statsUrl -RootUrl $localFrontendUrl -TimeoutSeconds $StartupTimeoutSeconds

if (-not $serviceStatus.Ready) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    $logTail = if (Test-Path -LiteralPath $logFile) {
        (Get-Content -LiteralPath $logFile -Tail 25) -join [Environment]::NewLine
    } else {
        "No startup log was captured."
    }

    Write-Warning "The backend process was launched (PID $($process.Id)), but the app never became ready."
    Write-Warning "Last readiness error: $($serviceStatus.LastError)"
    Write-Warning "Startup log: $logFile"
    Write-Host $logTail
    exit 1
}

Write-Host "Backend process started in a new PowerShell window (PID $($process.Id))." -ForegroundColor Green
Write-Host "Frontend is served by FastAPI at $localFrontendUrl" -ForegroundColor Green
Write-Host "Startup log: $logFile" -ForegroundColor Green

if (-not $NoBrowser) {
    Start-Process $localFrontendUrl | Out-Null
}

