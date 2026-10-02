[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action = "start"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendRoot = Join-Path $RepoRoot "frontend"
$Uvicorn = Join-Path $RepoRoot ".venv\Scripts\uvicorn.exe"
$BackendLog = Join-Path $RepoRoot "backend_server.log"
$BackendErrorLog = Join-Path $RepoRoot "backend_server_error.log"
$FrontendLog = Join-Path $RepoRoot "frontend_server.log"
$FrontendErrorLog = Join-Path $RepoRoot "frontend_server_error.log"
$BackendPort = 8001
$FrontendPort = 5173

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
    if ($backendProcesses.Count -gt 0 -or $frontendProcesses.Count -gt 0) {
        $busyPorts = @()
        if ($backendProcesses.Count -gt 0) { $busyPorts += $BackendPort }
        if ($frontendProcesses.Count -gt 0) { $busyPorts += $FrontendPort }
        throw "Cannot start because port(s) $($busyPorts -join ', ') are already in use. Check status or free those ports first."
    }

    Start-Process -FilePath $Uvicorn `
        -ArgumentList "backend.main:app --host 127.0.0.1 --port $BackendPort" `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $BackendLog `
        -RedirectStandardError $BackendErrorLog

    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c npm run dev -- --host 0.0.0.0 --port $FrontendPort" `
        -WorkingDirectory $FrontendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $FrontendLog `
        -RedirectStandardError $FrontendErrorLog

    Write-Host "Servers started." -ForegroundColor Green
    Write-Host "Frontend: http://localhost:$FrontendPort"
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
