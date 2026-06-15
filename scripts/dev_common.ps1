function Get-DevProjectRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptRootPath
    )

    return (Split-Path -Parent $ScriptRootPath)
}


function Get-DevPythonPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    return (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
}


function Get-DevEnvPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    return (Join-Path $ProjectRoot ".env")
}


function Assert-DevEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $python = Get-DevPythonPath -ProjectRoot $ProjectRoot
    $envFile = Get-DevEnvPath -ProjectRoot $ProjectRoot

    if (-not (Test-Path $python)) {
        throw "Virtual environment not found at '$python'."
    }

    if (-not (Test-Path $envFile)) {
        throw ".env not found at '$envFile'."
    }
}


function Get-ProcessMetadata {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }

    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    $commandLine = ""
    $parentProcessId = $null
    $executablePath = $null
    if ($cim) {
        $commandLine = "$($cim.CommandLine)"
        $parentProcessId = $cim.ParentProcessId
        $executablePath = "$($cim.ExecutablePath)"
    }

    return [pscustomobject]@{
        ProcessId      = $process.Id
        ProcessName    = $process.ProcessName
        ParentProcessId = $parentProcessId
        ExecutablePath = $executablePath
        CommandLine    = $commandLine
    }
}


function Test-ProjectServiceProcess {
    param(
        [Parameter(Mandatory = $true)]
        $ProcessInfo,
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    if (-not $ProcessInfo) {
        return $false
    }

    $commandLine = "$($ProcessInfo.CommandLine)"
    $executablePath = "$($ProcessInfo.ExecutablePath)"
    $projectRootLower = $ProjectRoot.ToLowerInvariant()
    $venvPathLower = (Join-Path $ProjectRoot ".venv").ToLowerInvariant()
    $commandLower = $commandLine.ToLowerInvariant()
    $exeLower = $executablePath.ToLowerInvariant()

    $referencesProject = $false
    if ($commandLower.Contains($projectRootLower) -or $commandLower.Contains($venvPathLower)) {
        $referencesProject = $true
    }
    elseif ($exeLower.Contains($venvPathLower)) {
        $referencesProject = $true
    }

    $looksLikeServer = (($commandLine -match 'uvicorn') -and ($commandLine -match 'app\.main:app')) -or ($commandLine -match 'app[\\/]+main\.py')

    return ($referencesProject -and $looksLikeServer)
}


function Get-PortListenerInfo {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $connection) {
        return $null
    }

    $processInfo = Get-ProcessMetadata -ProcessId $connection.OwningProcess
    return [pscustomobject]@{
        Port              = $Port
        LocalAddress      = $connection.LocalAddress
        ListenerProcessId = $connection.OwningProcess
        Listener          = $processInfo
    }
}


function Get-RelatedPythonProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ParentHint
    )

    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like 'python*' -and (
            $_.ParentProcessId -eq $ParentHint -or
            "$($_.CommandLine)" -like "*parent_pid=$ParentHint*"
        )
    }

    return @(
        $processes | ForEach-Object {
            [pscustomobject]@{
                ProcessId       = $_.ProcessId
                ProcessName     = $_.Name
                ParentProcessId = $_.ParentProcessId
                ExecutablePath  = "$($_.ExecutablePath)"
                CommandLine     = "$($_.CommandLine)"
            }
        }
    )
}


function Resolve-DevServiceForPort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $listenerInfo = Get-PortListenerInfo -Port $Port
    if (-not $listenerInfo) {
        return $null
    }

    $listener = $listenerInfo.Listener
    $controller = $null
    $relatedChildren = @()
    $visited = @{}
    $current = $listener

    while ($current -and -not $visited.ContainsKey($current.ProcessId)) {
        $visited[$current.ProcessId] = $true
        if (Test-ProjectServiceProcess -ProcessInfo $current -ProjectRoot $ProjectRoot) {
            $controller = $current
            break
        }

        if (-not $current.ParentProcessId -or $current.ParentProcessId -le 0) {
            break
        }
        $current = Get-ProcessMetadata -ProcessId $current.ParentProcessId
    }

    if (-not $controller -and $listenerInfo.ListenerProcessId) {
        $relatedChildren = Get-RelatedPythonProcesses -ParentHint $listenerInfo.ListenerProcessId
        foreach ($child in $relatedChildren) {
            $looksLikeReloadChild = "$($child.CommandLine)" -like "*spawn_main(parent_pid=$($listenerInfo.ListenerProcessId)*"
            $looksLikeProjectProcess = Test-ProjectServiceProcess -ProcessInfo $child -ProjectRoot $ProjectRoot
            if ($looksLikeReloadChild -or $looksLikeProjectProcess) {
                $controller = $child
                break
            }
        }
    }

    return [pscustomobject]@{
        Port              = $Port
        LocalAddress      = $listenerInfo.LocalAddress
        ListenerProcessId = $listenerInfo.ListenerProcessId
        ListenerProcess   = $listener
        ControllerProcess = $controller
        ChildProcesses    = $relatedChildren
        IsProjectService  = $null -ne $controller
    }
}


function Format-DevServiceSummary {
    param(
        [Parameter(Mandatory = $true)]
        $ServiceInfo
    )

    $lines = @()
    $lines += "Port: http://127.0.0.1:$($ServiceInfo.Port)"
    if ($ServiceInfo.ListenerProcess) {
        $lines += "Listener PID: $($ServiceInfo.ListenerProcess.ProcessId)"
        $lines += "Listener Process: $($ServiceInfo.ListenerProcess.ProcessName)"
    }
    elseif ($ServiceInfo.ListenerProcessId) {
        $lines += "Listener PID: $($ServiceInfo.ListenerProcessId)"
    }
    if ($ServiceInfo.ControllerProcess) {
        $lines += "Controller PID: $($ServiceInfo.ControllerProcess.ProcessId)"
        $lines += "Controller Process: $($ServiceInfo.ControllerProcess.ProcessName)"
    }
    if ($ServiceInfo.ChildProcesses -and $ServiceInfo.ChildProcesses.Count -gt 0) {
        $lines += "Related Python PID(s): $((@($ServiceInfo.ChildProcesses | ForEach-Object { $_.ProcessId }) -join ', '))"
    }
    return ($lines -join [Environment]::NewLine)
}
