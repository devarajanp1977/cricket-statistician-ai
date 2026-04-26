[CmdletBinding()]
param(
    [string]$TargetHost = "127.0.0.1",
    [int]$Port = 8080,
    [switch]$Force,
    [int]$ShutdownTimeoutSeconds = 10
)

$ErrorActionPreference = "Stop"

function Get-PortListener([int]$LocalPort) {
    Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-ProcessInfo([int]$ProcessId) {
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $proc -and -not $cim) {
        return $null
    }

    return [pscustomobject]@{
        ProcessId = $ProcessId
        Name = if ($proc) { $proc.ProcessName } elseif ($cim) { $cim.Name } else { "unknown" }
        ParentProcessId = if ($cim) { [int]$cim.ParentProcessId } else { $null }
        CommandLine = if ($cim) { $cim.CommandLine } else { "" }
    }
}

function Test-CricketStatisticianEndpoint([string]$HostName, [int]$LocalPort) {
    $baseUrl = "http://${HostName}:$LocalPort"
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
        if ($health.status -ne "ok") {
            return $false
        }

        $root = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/" -TimeoutSec 2
        return $root.Content -match "Cricket Statistician AI"
    } catch {
        return $false
    }
}

function Test-ManagedLauncherParent($ParentProcess) {
    if (-not $ParentProcess) {
        return $false
    }

    if ($ParentProcess.Name -notmatch "(?i)powershell") {
        return $false
    }

    return $ParentProcess.CommandLine -match "(?i)(start_local_services\.ps1|uvicorn|app\.main:app)"
}

$listener = Get-PortListener -LocalPort $Port
if (-not $listener) {
    Write-Host "No listening process found on port $Port." -ForegroundColor Yellow
    return
}

$serviceProcess = Get-ProcessInfo -ProcessId $listener.OwningProcess
if (-not $serviceProcess) {
    throw "Found a listener on port $Port, but the owning process details could not be read."
}

$parentProcess = $null
if ($serviceProcess.ParentProcessId -and $serviceProcess.ParentProcessId -gt 0) {
    $parentProcess = Get-ProcessInfo -ProcessId $serviceProcess.ParentProcessId
}

$matchesEndpoint = Test-CricketStatisticianEndpoint -HostName $TargetHost -LocalPort $Port
$matchesCommand = $serviceProcess.CommandLine -match "(?i)(uvicorn|app\.main:app)"
$managedParent = Test-ManagedLauncherParent -ParentProcess $parentProcess

if (-not $Force -and -not ($matchesEndpoint -or $matchesCommand -or $managedParent)) {
    throw "Port $Port is owned by PID $($serviceProcess.ProcessId) ($($serviceProcess.Name)), but it does not look like the Cricket Statistician service. Re-run with -Force to stop it anyway."
}

Write-Host "Stopping listener on port $Port (PID $($serviceProcess.ProcessId), process $($serviceProcess.Name))." -ForegroundColor Cyan
Stop-Process -Id $serviceProcess.ProcessId -Force -ErrorAction SilentlyContinue

if ($managedParent -and $parentProcess) {
    Write-Host "Closing launcher PowerShell (PID $($parentProcess.ProcessId))." -ForegroundColor DarkCyan
    Stop-Process -Id $parentProcess.ProcessId -Force -ErrorAction SilentlyContinue
}

$deadline = (Get-Date).AddSeconds($ShutdownTimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if (-not (Get-PortListener -LocalPort $Port)) {
        Write-Host "Cricket Statistician service stopped on port $Port." -ForegroundColor Green
        return
    }
    Start-Sleep -Milliseconds 250
}

throw "Timed out waiting for port $Port to stop listening."