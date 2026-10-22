[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action = "start",
    [ValidateSet("live", "paper", "all")]
    [string]$Workspace = "live"
)

$ErrorActionPreference = "Stop"
if ($Workspace -eq "all") {
    $failures = @()
    foreach ($item in @("live", "paper")) {
        try {
            & $PSCommandPath -Action $Action -Workspace $item
        } catch {
            $failures += "${item}: $($_.Exception.Message)"
        }
    }
    if ($failures.Count -gt 0) { throw ($failures -join "`n") }
    return
}
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendRoot = Join-Path $RepoRoot "frontend"
$Uvicorn = Join-Path $RepoRoot ".venv\Scripts\uvicorn.exe"
$BackendLog = Join-Path $RepoRoot "backend_server.log"
$BackendErrorLog = Join-Path $RepoRoot "backend_server_error.log"
$FrontendLog = Join-Path $RepoRoot "frontend_server.log"
$FrontendErrorLog = Join-Path $RepoRoot "frontend_server_error.log"
$BackendPort = 8001
$FrontendPort = 5173
$BackendApp = "backend.live_app:app"
$UiSuffix = "/"
if ($Workspace -eq "paper") {
    $BackendPort = 8003
    $FrontendPort = 5174
    $BackendApp = "backend.indstocks_app:app"
    $UiSuffix = "/?workspace=paper"
    $BackendLog = Join-Path $RepoRoot "paper_backend_server.log"
    $BackendErrorLog = Join-Path $RepoRoot "paper_backend_server_error.log"
    $FrontendLog = Join-Path $RepoRoot "paper_frontend_server.log"
    $FrontendErrorLog = Join-Path $RepoRoot "paper_frontend_server_error.log"
}

function Get-PortProcessIds([int]$Port) {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Stop-Port([int]$Port) {
    $processIds = Get-PortProcessIds $Port
    $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)

    function Stop-ProcessTree([int]$ProcessId) {
        $children = @($allProcesses | Where-Object { $_.ParentProcessId -eq $ProcessId } | Select-Object -ExpandProperty ProcessId)
        foreach ($childId in $children) {
            Stop-ProcessTree ([int]$childId)
        }
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }

    foreach ($processId in $processIds) {
        Stop-ProcessTree ([int]$processId)
    }
}

function Show-Status {
    foreach ($entry in @(@{ Name = "Backend"; Port = $BackendPort }, @{ Name = "Frontend"; Port = $FrontendPort })) {
        $processIds = Get-PortProcessIds $entry.Port
        if ($processIds.Count -gt 0) {
            Write-Host "$($entry.Name): running on port $($entry.Port) (PID $($processIds -join ', '))" -ForegroundColor Green
        }
        else {
            Write-Host "$($entry.Name): stopped" -ForegroundColor Yellow
        }
    }
}

function Start-Servers {
    if (-not (Test-Path $Uvicorn)) {
        throw "Python environment not found: $Uvicorn"
    }
    if (-not (Test-Path (Join-Path $FrontendRoot "package.json"))) {
        throw "Frontend project not found: $FrontendRoot"
    }

    $backendProcesses = Get-PortProcessIds $BackendPort
    $frontendProcesses = Get-PortProcessIds $FrontendPort
    if ($backendProcesses.Count -gt 0 -and $frontendProcesses.Count -gt 0) {
        Write-Host "Both server ports are already in use. Leaving the running processes untouched." -ForegroundColor Yellow
        Show-Status
        return
    }

    if ($Workspace -eq "paper") {
        $workers = @(Get-CimInstance Win32_Process | Where-Object {
            $_.Name -match '^python' -and $_.CommandLine -match ' -m backend\.option_paper' -and
            $_.CommandLine.Contains($RepoRoot)
        })
        if ($workers.Count -eq 0) {
            Start-Process -FilePath (Join-Path $RepoRoot '.venv\Scripts\python.exe') -ArgumentList '-m','backend.option_paper' -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RepoRoot 'option_paper.log') -RedirectStandardError (Join-Path $RepoRoot 'option_paper_error.log')
        }
    }

    # Serve a production build: no development HMR/page reload during trading.
    # Separate output folders avoid rebuilding paper assets underneath live tabs.
    if ($frontendProcesses.Count -eq 0) {
        Push-Location $FrontendRoot
        try {
            & npm.cmd run build -- --outDir "dist-$Workspace"
            if ($LASTEXITCODE -ne 0) { throw "$Workspace frontend build failed." }
        } finally { Pop-Location }
    }

    # Start-Process cannot run in this environment because its inherited
    # environment has both Path and PATH keys. cmd's start /b avoids that
    # case-insensitive dictionary failure and keeps both servers detached.
    $backendCommand = "cd /d `"$RepoRoot`" && start `"Kotak Backend`" /b `"$Uvicorn`" $BackendApp --host 127.0.0.1 --port $BackendPort > `"$BackendLog`" 2> `"$BackendErrorLog`""
    if ($backendProcesses.Count -eq 0) { cmd.exe /c $backendCommand }
    $frontendCommand = "cd /d `"$FrontendRoot`" && start `"Kotak Frontend`" /b cmd.exe /c npm run preview -- --host 0.0.0.0 --port $FrontendPort --strictPort --outDir dist-$Workspace > `"$FrontendLog`" 2> `"$FrontendErrorLog`""
    if ($frontendProcesses.Count -eq 0) { cmd.exe /c $frontendCommand }

    $deadline = (Get-Date).AddSeconds(20)
    do {
        $backendReady = (Get-PortProcessIds $BackendPort).Count -gt 0
        $frontendReady = (Get-PortProcessIds $FrontendPort).Count -gt 0
        if ($backendReady -and $frontendReady) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    if (-not ($backendReady -and $frontendReady)) {
        throw "$Workspace servers did not start on ports $BackendPort and $FrontendPort. Check $BackendErrorLog and $FrontendErrorLog."
    }

    Write-Host "Servers started." -ForegroundColor Green
    Write-Host "Frontend: http://localhost:$FrontendPort$UiSuffix"
    Write-Host "Backend:  http://127.0.0.1:$BackendPort"
    Write-Host "Logs:     $BackendLog and $FrontendLog"
}

function Stop-Servers {
    Stop-Port $BackendPort
    Stop-Port $FrontendPort
    Write-Host "Servers stopped. Ports $BackendPort and $FrontendPort are free." -ForegroundColor Yellow
}

Set-Location $RepoRoot

switch ($Action) {
    "start" { Start-Servers }
    "stop" { Stop-Servers }
    "restart" { Stop-Servers; Start-Servers }
    "status" { Show-Status }
}
