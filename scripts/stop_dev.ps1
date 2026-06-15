[CmdletBinding()]
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "dev_common.ps1")

$projectRoot = Get-DevProjectRoot -ScriptRootPath $PSScriptRoot
$service = Resolve-DevServiceForPort -ProjectRoot $projectRoot -Port $Port

if (-not $service) {
    Write-Host "No service is listening on http://127.0.0.1:$Port."
    exit 0
}

if (-not $service.IsProjectService) {
    Write-Warning "Port $Port is occupied by a non-project process. Nothing was stopped."
    Write-Host (Format-DevServiceSummary -ServiceInfo $service)
    if ($service.ListenerProcess.CommandLine) {
        Write-Host "Command: $($service.ListenerProcess.CommandLine)"
    }
    exit 0
}

$processIds = @()
if ($service.ControllerProcess) {
    $processIds += $service.ControllerProcess.ProcessId
}
if ($service.ListenerProcess) {
    $processIds += $service.ListenerProcess.ProcessId
}
$processIds += @($service.ChildProcesses | ForEach-Object { $_.ProcessId })
$processIds += $service.ListenerProcessId
$processIds = $processIds | Select-Object -Unique

foreach ($processId in $processIds) {
    if (-not $processId) {
        continue
    }
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    & taskkill /PID $processId /F /T *> $null
}

Start-Sleep -Milliseconds 1200
$remaining = Get-PortListenerInfo -Port $Port
if ($remaining) {
    Write-Warning "A listener is still active on http://127.0.0.1:$Port."
    Write-Host (Format-DevServiceSummary -ServiceInfo (Resolve-DevServiceForPort -ProjectRoot $projectRoot -Port $Port))
    exit 1
}

Write-Host "Stopped the project service on http://127.0.0.1:$Port."
