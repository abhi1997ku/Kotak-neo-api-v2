[CmdletBinding()]
param(
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action = 'start',
    [ValidateSet('live', 'paper', 'all')]
    [string]$Workspace = 'live',
    [switch]$NoBrowser,
    # Used by the all-workspaces dispatcher so setup runs once.
    [switch]$SetupCompleted
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot

if ($Action -in @('start', 'restart') -and -not $SetupCompleted) {
    $global:LASTEXITCODE = 0
    & (Join-Path $RepoRoot 'setup.ps1') -NoShortcuts -NonInteractive
    if (-not $? -or $LASTEXITCODE -ne 0) { throw 'Setup failed. Fix the reported error and run Start again.' }
}

if ($Workspace -eq 'all') {
    $failures = @()
    foreach ($item in @('live', 'paper')) {
        try {
            & $PSCommandPath -Action $Action -Workspace $item -SetupCompleted -NoBrowser:$NoBrowser
        } catch {
            $failures += "${item}: $($_.Exception.Message)"
        }
    }
    if ($failures.Count -gt 0) { throw ($failures -join "`n") }
    return
}

$FrontendRoot = Join-Path $RepoRoot 'frontend'
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$RuntimeRoot = Join-Path $RepoRoot '.runtime'
$StatePath = Join-Path $RuntimeRoot "$Workspace-processes.json"
$BackendPort = 8001
$FrontendPort = 5173
$BackendApp = 'backend.live_app:app'
$UiSuffix = '/'
$LogPrefix = ''
if ($Workspace -eq 'paper') {
    $BackendPort = 8003
    $FrontendPort = 5174
    $BackendApp = 'backend.indstocks_app:app'
    $UiSuffix = '/?workspace=paper'
    $LogPrefix = 'paper_'
}
$BackendLog = Join-Path $RepoRoot "${LogPrefix}backend_server.log"
$BackendErrorLog = Join-Path $RepoRoot "${LogPrefix}backend_server_error.log"
$FrontendLog = Join-Path $RepoRoot "${LogPrefix}frontend_server.log"
$FrontendErrorLog = Join-Path $RepoRoot "${LogPrefix}frontend_server_error.log"
$script:Records = @()
if (Test-Path -LiteralPath $StatePath) {
    try { $script:Records = @(Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json) }
    catch { Write-Warning "Ignoring unreadable process state: $StatePath" }
}

function Get-PortProcessIds([int]$Port) {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Get-CreationStamp($Process) {
    $Process.CreationDate.ToUniversalTime().ToString('o')
}

function Test-ProjectProcess($Process, [string]$Role) {
    $commandLine = [string]$Process.CommandLine
    if ($commandLine.IndexOf(($RepoRoot.TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    switch ($Role) {
        'backend' { return $commandLine -match [regex]::Escape($BackendApp) }
        'frontend' {
            return $commandLine -match 'vite[\\/]bin[\\/]vite\.js' -and
                $commandLine -match "--port[ =]+$FrontendPort(?:\s|$)"
        }
        'stock-worker' { return $commandLine -match '(?:^|\s)-m\s+backend\.intraday_paper_worker(?:\s|$)' }
        'option-worker' { return $commandLine -match '(?:^|\s)-m\s+backend\.option_paper(?:\s|$)' }
    }
    return $false
}

function Get-ManagedRoots([string]$Role, [array]$Processes) {
    $result = @{}
    foreach ($process in $Processes) {
        # Command-line discovery also recognizes processes launched before PID tracking existed.
        if (Test-ProjectProcess $process $Role) { $result[[int]$process.ProcessId] = $process }
        foreach ($record in @($script:Records | Where-Object { $_.Role -eq $Role })) {
            if ($record.ProcessId -eq $process.ProcessId -and
                $record.CreatedUtc -eq (Get-CreationStamp $process)) {
                $result[[int]$process.ProcessId] = $process
            }
        }
    }
    @($result.Values)
}

function Get-ProcessTree([int]$RootId, [array]$Processes) {
    $visited = New-Object 'System.Collections.Generic.HashSet[int]'
    $pending = New-Object 'System.Collections.Generic.Queue[int]'
    $pending.Enqueue($RootId)
    while ($pending.Count -gt 0) {
        $nextId = $pending.Dequeue()
        if (-not $visited.Add($nextId)) { continue }
        $nextId
        foreach ($child in @($Processes | Where-Object { $_.ParentProcessId -eq $nextId })) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
}

function Assert-PortAvailableOrManaged([int]$Port, [string]$Role, [array]$Processes) {
    $managedIds = @()
    foreach ($root in @(Get-ManagedRoots $Role $Processes)) {
        $managedIds += @(Get-ProcessTree ([int]$root.ProcessId) $Processes)
    }
    $foreignIds = @(Get-PortProcessIds $Port | Where-Object { $_ -notin $managedIds })
    if ($foreignIds.Count -gt 0) {
        throw "Port $Port is used by another application (PID $($foreignIds -join ', ')). Close that application, then try again."
    }
}

function Save-ProcessRecord([string]$Role, [int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) { throw "$Role exited immediately. Check its error log in $RepoRoot." }
    $script:Records = @($script:Records | Where-Object { $_.Role -ne $Role }) + @([pscustomobject]@{
        Role = $Role
        ProcessId = $ProcessId
        CreatedUtc = Get-CreationStamp $process
    })
    New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
    ConvertTo-Json -InputObject @($script:Records) | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

function Start-ManagedProcess {
    param([string]$Role, [string]$Executable, [string[]]$Arguments,
        [string]$Directory, [string]$OutputLog, [string]$ErrorLog)
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $Directory `
        -WindowStyle Hidden -RedirectStandardOutput $OutputLog -RedirectStandardError $ErrorLog -PassThru
    Save-ProcessRecord $Role $process.Id
}

function Open-Dashboard {
    if (-not $NoBrowser) { Start-Process "http://localhost:$FrontendPort$UiSuffix" | Out-Null }
}

function Show-Status {
    $processes = @(Get-CimInstance Win32_Process)
    foreach ($entry in @(@{ Name = 'Backend'; Role = 'backend'; Port = $BackendPort },
                        @{ Name = 'Frontend'; Role = 'frontend'; Port = $FrontendPort })) {
        $processIds = @(Get-PortProcessIds $entry.Port)
        if ($processIds.Count -eq 0) {
            Write-Host "$Workspace $($entry.Name): stopped" -ForegroundColor Yellow
        } else {
            try {
                Assert-PortAvailableOrManaged $entry.Port $entry.Role $processes
                Write-Host "$Workspace $($entry.Name): running on port $($entry.Port) (PID $($processIds -join ', '))" -ForegroundColor Green
            } catch { Write-Warning $_.Exception.Message }
        }
    }
    if ($Workspace -eq 'paper') {
        foreach ($role in @('stock-worker', 'option-worker')) {
            $workers = @(Get-ManagedRoots $role $processes)
            Write-Host "Paper ${role}: $(if ($workers.Count -gt 0) { 'running' } else { 'stopped' })"
        }
    }
}

function Start-Servers {
    if (-not (Test-Path -LiteralPath $Python)) { throw "Python environment missing. Run Setup.cmd in $RepoRoot." }
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $vite = Join-Path $FrontendRoot 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite)) { throw 'Frontend dependencies missing. Run Setup.cmd.' }
    $processes = @(Get-CimInstance Win32_Process)
    Assert-PortAvailableOrManaged $BackendPort 'backend' $processes
    Assert-PortAvailableOrManaged $FrontendPort 'frontend' $processes

    $backendRoots = @(Get-ManagedRoots 'backend' $processes)
    $frontendRoots = @(Get-ManagedRoots 'frontend' $processes)
    if ($frontendRoots.Count -eq 0) {
        Push-Location $FrontendRoot
        try {
            & npm.cmd run build -- --outDir "dist-$Workspace"
            if ($LASTEXITCODE -ne 0) { throw "$Workspace frontend build failed." }
        } finally { Pop-Location }
    }
    if ($backendRoots.Count -eq 0) {
        Start-ManagedProcess -Role 'backend' -Executable $Python `
            -Arguments @('-m', 'uvicorn', $BackendApp, '--host', '127.0.0.1', '--port', "$BackendPort") `
            -Directory $RepoRoot -OutputLog $BackendLog -ErrorLog $BackendErrorLog
    }
    if ($frontendRoots.Count -eq 0) {
        # Direct node invocation gives Stop a stable PID, without an npm/cmd wrapper.
        Start-ManagedProcess -Role 'frontend' -Executable $node `
            -Arguments @(('"' + $vite + '"'), 'preview', '--host', '127.0.0.1', '--port', "$FrontendPort", '--strictPort', '--outDir', "dist-$Workspace") `
            -Directory $FrontendRoot -OutputLog $FrontendLog -ErrorLog $FrontendErrorLog
    }

    $deadline = (Get-Date).AddSeconds(30)
    do {
        $backendReady = (Get-PortProcessIds $BackendPort).Count -gt 0
        $frontendReady = (Get-PortProcessIds $FrontendPort).Count -gt 0
        if ($backendReady -and $frontendReady) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    if (-not ($backendReady -and $frontendReady)) {
        throw "$Workspace servers did not start. Check $BackendErrorLog and $FrontendErrorLog. Run Stop before retrying."
    }
    $processes = @(Get-CimInstance Win32_Process)
    Assert-PortAvailableOrManaged $BackendPort 'backend' $processes
    Assert-PortAvailableOrManaged $FrontendPort 'frontend' $processes

    if ($Workspace -eq 'paper') {
        $processes = @(Get-CimInstance Win32_Process)
        foreach ($entry in @(
            @{ Role = 'stock-worker'; Module = 'backend.intraday_paper_worker'; Log = 'intraday_stock_worker' },
            @{ Role = 'option-worker'; Module = 'backend.option_paper'; Log = 'option_paper' }
        )) {
            if (@(Get-ManagedRoots $entry.Role $processes).Count -eq 0) {
                Start-ManagedProcess -Role $entry.Role -Executable $Python -Arguments @('-m', $entry.Module) `
                    -Directory $RepoRoot -OutputLog (Join-Path $RepoRoot "$($entry.Log).log") `
                    -ErrorLog (Join-Path $RepoRoot "$($entry.Log)_error.log")
            }
        }
    }
    Write-Host "$Workspace servers ready: http://localhost:$FrontendPort$UiSuffix" -ForegroundColor Green
    Write-Host "Backend: http://127.0.0.1:$BackendPort; logs: $BackendLog and $FrontendLog"
    Open-Dashboard
}

function Stop-Servers {
    $processes = @(Get-CimInstance Win32_Process)
    $roles = @('frontend', 'backend')
    if ($Workspace -eq 'paper') { $roles += @('stock-worker', 'option-worker') }
    $stopIds = @()
    foreach ($role in $roles) {
        foreach ($root in @(Get-ManagedRoots $role $processes)) {
            $stopIds += @(Get-ProcessTree ([int]$root.ProcessId) $processes)
        }
    }
    # Descendants are collected after parents, so reverse the list to stop children first.
    [array]::Reverse($stopIds)
    foreach ($processId in @($stopIds | Select-Object -Unique)) {
        $original = $processes | Where-Object { $_.ProcessId -eq $processId } | Select-Object -First 1
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        if ($current -and $original -and (Get-CreationStamp $current) -eq (Get-CreationStamp $original)) {
            try { Stop-Process -Id $processId -Force -ErrorAction Stop }
            catch {
                # A child can exit naturally while its tree is being stopped.
                if (Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue) { throw }
            }
        }
    }
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    foreach ($port in @($BackendPort, $FrontendPort)) {
        if (@(Get-PortProcessIds $port).Count -gt 0) {
            Write-Warning "Port $port is still in use. Processes outside this project were left running."
        }
    }
    Write-Host "$Workspace project servers and workers stopped." -ForegroundColor Yellow
}

switch ($Action) {
    'start' { Start-Servers }
    'stop' { Stop-Servers }
    'restart' { Stop-Servers; Start-Servers }
    'status' { Show-Status }
}
