# Server Commands

Run these commands from `D:\Kotak-neo-api-v2`.

## Start both servers

```powershell
powershell -ExecutionPolicy Bypass -File .\run_servers.ps1 -Action start
```

## Stop both servers

```powershell
powershell -ExecutionPolicy Bypass -File .\run_servers.ps1 -Action stop
```

## Restart both servers

```powershell
powershell -ExecutionPolicy Bypass -File .\run_servers.ps1 -Action restart
```

## Check server status

```powershell
powershell -ExecutionPolicy Bypass -File .\run_servers.ps1 -Action status
```

Frontend: http://localhost:5173
Backend: http://localhost:8001
