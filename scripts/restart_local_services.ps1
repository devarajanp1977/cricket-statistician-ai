[CmdletBinding()]
param(
    [string]$TargetHost = "127.0.0.1",
    [int]$Port = 8080,
    [string]$AppBasePath = "",
    [switch]$NoBrowser,
    [switch]$Inline,
    [int]$StartupTimeoutSeconds = 20,
    [switch]$ForceStop
)

$ErrorActionPreference = "Stop"

$stopScript = Join-Path $PSScriptRoot "stop_local_services.ps1"
$startScript = Join-Path $PSScriptRoot "start_local_services.ps1"

Write-Host "Restarting Cricket Statistician AI on port $Port..." -ForegroundColor Cyan

& $stopScript -TargetHost $TargetHost -Port $Port -Force:$ForceStop -ErrorAction Stop
& $startScript -TargetHost $TargetHost -Port $Port -AppBasePath $AppBasePath -NoBrowser:$NoBrowser -Inline:$Inline -StartupTimeoutSeconds $StartupTimeoutSeconds