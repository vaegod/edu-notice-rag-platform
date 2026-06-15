[CmdletBinding()]
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "stop_dev.ps1") -Port $Port
& (Join-Path $PSScriptRoot "start_dev.ps1") -Port $Port
