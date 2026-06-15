[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "status", "restart")]
    [string]$Action = "status",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$scriptMap = @{
    "up" = "start_dev.ps1"
    "down" = "stop_dev.ps1"
    "status" = "status_dev.ps1"
    "restart" = "restart_dev.ps1"
}

$targetScript = Join-Path $PSScriptRoot ("scripts\" + $scriptMap[$Action])
& $targetScript -Port $Port
