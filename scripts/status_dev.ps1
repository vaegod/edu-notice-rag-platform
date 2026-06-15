[CmdletBinding()]
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "dev_common.ps1")

$projectRoot = Get-DevProjectRoot -ScriptRootPath $PSScriptRoot
$service = Resolve-DevServiceForPort -ProjectRoot $projectRoot -Port $Port

if (-not $service) {
    Write-Host "No listener found on http://127.0.0.1:$Port."
    exit 0
}

if ($service.IsProjectService) {
    Write-Host "Project service is running."
    Write-Host (Format-DevServiceSummary -ServiceInfo $service)
    Write-Host "Home: http://127.0.0.1:$Port/"
    Write-Host "Docs: http://127.0.0.1:$Port/docs"
    if ($service.ControllerProcess.CommandLine) {
        Write-Host "Command: $($service.ControllerProcess.CommandLine)"
    }
    exit 0
}

Write-Warning "Port $Port is occupied, but not by the detected project service."
Write-Host (Format-DevServiceSummary -ServiceInfo $service)
if ($service.ListenerProcess.CommandLine) {
    Write-Host "Command: $($service.ListenerProcess.CommandLine)"
}
