# Server Commands

Run these commands from the repository root in PowerShell. Complete the first-time setup in [README.md](README.md) before starting the servers.

## Start both servers

```powershell
powershell.exe -ExecutionPolicy Bypass -File ./run_servers.ps1 -Action start
```

Frontend: http://localhost:5173  
Backend: http://127.0.0.1:8001  
Health check: http://127.0.0.1:8001/health  
Logs: `backend_server.log`, `backend_server_error.log`, `frontend_server.log`, and `frontend_server_error.log` in the repository root.

The script starts both processes in the background. It uses ports 5173 and 8001. If both ports are occupied, Start leaves the processes untouched. If only one is occupied, it reports the conflict without stopping that process. Use Restart when you intend to replace processes on these ports.

## Check status

```powershell
powershell.exe -ExecutionPolicy Bypass -File ./run_servers.ps1 -Action status
```

## Restart both servers

```powershell
powershell.exe -ExecutionPolicy Bypass -File ./run_servers.ps1 -Action restart
```

## Stop both servers

```powershell
powershell.exe -ExecutionPolicy Bypass -File ./run_servers.ps1 -Action stop
```

The helper stops the processes listening on ports 5173 and 8001. For foreground development commands and setup instructions, see [README.md](README.md).
## One-click desktop controls

Double-click Kotak Neo Terminal - Start or Kotak Neo Terminal - Stop on the Windows desktop. To create or refresh these shortcuts after a pull on another computer, run powershell.exe -ExecutionPolicy Bypass -File ./create_desktop_shortcuts.ps1 from the repository root.