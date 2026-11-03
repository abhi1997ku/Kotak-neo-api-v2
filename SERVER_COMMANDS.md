# Windows server commands

For normal use, double-click **Kotak Neo Terminal - Start** or **Kotak Neo Terminal - Stop** on the desktop. These control both live and paper workspaces. The matching `.cmd` files work directly from the project folder.

Run the following commands from the project root in PowerShell. `-Workspace all` matches the desktop trading shortcuts; omitting `-Workspace` selects only `live`.

## Setup and repair

Double-click `Setup.cmd`, or run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

To force dependency installation again:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Force
```

`-NoShortcuts` skips desktop shortcut creation. `-NonInteractive` runs setup without interactive prompts. Neither option supplies broker credentials or logs in. Setup leaves an existing root `.env` intact. See [README.md](README.md) for the installation flow.

## Start, status, restart and stop

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_servers.ps1 -Action start -Workspace all
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_servers.ps1 -Action status -Workspace all
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_servers.ps1 -Action restart -Workspace all
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_servers.ps1 -Action stop -Workspace all
```

Run one command at a time. Replace `all` with `live` or `paper` to manage just that workspace. Add `-NoBrowser` to Start or Restart to leave browser tabs unopened.

Start checks the local installation automatically, builds the frontend, and runs the servers in the background. It leaves an already running workspace in place. Stop and Restart use the launcher's process records and verified project processes; a port occupied by an unrelated process is reported as a conflict. Process records are local files in `.runtime/`.

Restart clears the in-memory Kotak login. Stop also stops the paper workers when `paper` or `all` is selected. Closing a browser tab does not stop a server. The separate indicator observer has its own console and is stopped with Ctrl+C.

| Workspace | Dashboard | Backend | App module |
| --- | --- | --- | --- |
| Live | http://localhost:5173/ | http://127.0.0.1:8001 | `backend.live_app:app` |
| Paper | http://localhost:5174/?workspace=paper | http://127.0.0.1:8003 | `backend.indstocks_app:app` |

Append `/health` to either backend URL for a health check, or `/docs` for API documentation. The frontend uses separate `frontend/dist-live` and `frontend/dist-paper` builds. These outputs and installed dependencies are regenerated locally.

## Logs

Logs are in the project root:

| Process | Standard output | Errors |
| --- | --- | --- |
| Live backend | `backend_server.log` | `backend_server_error.log` |
| Live frontend | `frontend_server.log` | `frontend_server_error.log` |
| Paper backend | `paper_backend_server.log` | `paper_backend_server_error.log` |
| Paper frontend | `paper_frontend_server.log` | `paper_frontend_server_error.log` |
| Intraday paper worker | `intraday_stock_worker.log` | `intraday_stock_worker_error.log` |
| Option paper worker | `option_paper.log` | `option_paper_error.log` |

For example:

```powershell
Get-Content .\backend_server_error.log -Tail 50
```

Logs can contain account or order information; keep them private. They are excluded from Git.

## Refresh desktop shortcuts

Run `Setup.cmd`, or just refresh the shortcut targets:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\create_desktop_shortcuts.ps1
```

Rerun this after moving the folder or cloning onto another PC. See [Windows recovery](docs/WINDOWS_RECOVERY.md) before resetting the machine.

## Manual development

After setup, use separate PowerShell windows for foreground servers. Start only the workspace you need and first stop any background instance using its ports.

Live backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.live_app:app --host 127.0.0.1 --port 8001 --reload
```

Paper backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.indstocks_app:app --host 127.0.0.1 --port 8003 --reload
```

Frontend, from another window:

```powershell
cd frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Use port `5174` and open `http://localhost:5174/?workspace=paper` for paper development. The foreground paper backend alone does not launch the separate paper worker processes; use the normal launcher for the full paper workspace. Press Ctrl+C in each console to stop foreground processes.
