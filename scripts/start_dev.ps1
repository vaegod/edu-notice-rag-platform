[CmdletBinding()]
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "dev_common.ps1")

$projectRoot = Get-DevProjectRoot -ScriptRootPath $PSScriptRoot
$python = Get-DevPythonPath -ProjectRoot $projectRoot

try {
    Assert-DevEnvironment -ProjectRoot $projectRoot
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}

$service = Resolve-DevServiceForPort -ProjectRoot $projectRoot -Port $Port
if ($service) {
    if ($service.IsProjectService) {
        Write-Host "Project service is already running."
        Write-Host (Format-DevServiceSummary -ServiceInfo $service)
        Write-Host "Home: http://127.0.0.1:$Port/"
        Write-Host "Docs: http://127.0.0.1:$Port/docs"
        exit 0
    }

    Write-Warning "Port $Port is already occupied by another process."
    Write-Host (Format-DevServiceSummary -ServiceInfo $service)
    if ($service.ListenerProcess.CommandLine) {
        Write-Host "Command: $($service.ListenerProcess.CommandLine)"
    }
    Write-Host "Stop that process first, or start on another port:"
    Write-Host "powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1 -Port 8001"
    exit 1
}

Set-Location $projectRoot
Write-Host "Starting project service on http://127.0.0.1:$Port"
Write-Host "Docs: http://127.0.0.1:$Port/docs"
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
