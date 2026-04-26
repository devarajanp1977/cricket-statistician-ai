[CmdletBinding()]
param(
    [string]$TargetHost = "127.0.0.1",
    [int]$Port = 8080
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

function Invoke-JsonEndpoint([string]$Url) {
    try {
        return [pscustomobject]@{
            Ok = $true
            Data = Invoke-RestMethod -Uri $Url -TimeoutSec 3
            Error = $null
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            Data = $null
            Error = $_.Exception.Message
        }
    }
}

function Invoke-TextEndpoint([string]$Url) {
    try {
        return [pscustomobject]@{
            Ok = $true
            Data = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            Error = $null
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            Data = $null
            Error = $_.Exception.Message
        }
    }
}

$baseUrl = "http://${TargetHost}:$Port"
$appUrl = "$baseUrl/"
$listener = Get-PortListener -LocalPort $Port

Write-Host "Cricket Statistician AI status" -ForegroundColor Cyan
Write-Host "Port: $Port"
Write-Host "URL: $appUrl"

if (-not $listener) {
    Write-Warning "No listening process found on port $Port."
    exit 1
}

$serviceProcess = Get-ProcessInfo -ProcessId $listener.OwningProcess
$parentProcess = $null
if ($serviceProcess -and $serviceProcess.ParentProcessId -and $serviceProcess.ParentProcessId -gt 0) {
    $parentProcess = Get-ProcessInfo -ProcessId $serviceProcess.ParentProcessId
}

$healthResult = Invoke-JsonEndpoint -Url "$baseUrl/health"
$rootResult = Invoke-TextEndpoint -Url $appUrl
$statsResult = Invoke-JsonEndpoint -Url "$baseUrl/api/stats"

$healthOk = $healthResult.Ok -and $healthResult.Data -and $healthResult.Data.status -eq "ok"
$rootOk = $rootResult.Ok -and $rootResult.Data -and $rootResult.Data.Content -match "Cricket Statistician AI"
$statsPropertyCount = if ($statsResult.Ok -and $statsResult.Data) {
    ($statsResult.Data.PSObject.Properties | Measure-Object).Count
} else {
    0
}
$statsOk = $statsResult.Ok -and $statsPropertyCount -gt 0
$overallHealthy = $healthOk -and $rootOk -and $statsOk

Write-Host "Listener PID: $($listener.OwningProcess)" + $(if ($serviceProcess) { " ($($serviceProcess.Name))" } else { "" })
if ($parentProcess) {
    Write-Host "Launcher PID: $($parentProcess.ProcessId) ($($parentProcess.Name))"
}

Write-Host "Health endpoint: " -NoNewline
if ($healthOk) {
    Write-Host "ok" -ForegroundColor Green
} else {
    Write-Host "failed" -ForegroundColor Red
    if ($healthResult.Error) {
        Write-Host "  $($healthResult.Error)"
    }
}

Write-Host "Frontend route: " -NoNewline
if ($rootOk) {
    Write-Host "ok" -ForegroundColor Green
} else {
    Write-Host "failed" -ForegroundColor Red
    if ($rootResult.Error) {
        Write-Host "  $($rootResult.Error)"
    }
}

Write-Host "Stats endpoint: " -NoNewline
if ($statsOk) {
    Write-Host "ok" -ForegroundColor Green
    Write-Host "  tables reported: $statsPropertyCount"
    if ($statsResult.Data.PSObject.Properties.Name -contains "deliveries") {
        Write-Host "  deliveries rows: $($statsResult.Data.deliveries)"
    }
} else {
    Write-Host "failed" -ForegroundColor Red
    if ($statsResult.Error) {
        Write-Host "  $($statsResult.Error)"
    }
}

Write-Host "Overall status: " -NoNewline
if ($overallHealthy) {
    Write-Host "healthy" -ForegroundColor Green
    exit 0
}

Write-Host "unhealthy" -ForegroundColor Red
exit 1