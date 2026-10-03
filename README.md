# Kotak Neo Personal Trading Terminal

A local React + FastAPI trading dashboard for a single Kotak Neo account. It includes watchlists and live quotes, option chains, positions, a Holdings panel in the left sidebar with a sell workflow, an order book, and a swing-trade screener. Buy, sell, and position-exit actions use an order ticket popup. The repository also contains the Kotak Neo Python SDK and its endpoint references under [docs](docs/).

## Requirements

- Windows 10/11 with PowerShell and Git for Windows
- Python 3.10-3.13 (Python 3.12 recommended)
- Node.js 18 or 20+ with npm
- A Kotak Neo account with an active Trade API application

## First-time setup on Windows

Run these commands from the repository root after cloning it.

### 1. Configure Kotak Neo

Create your local environment file:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Set these values in the root `.env`:

| Variable | Value |
| --- | --- |
| `KOTAK_CONSUMER_KEY` | Consumer key from your active Kotak Neo Trade API application |
| `KOTAK_MOBILE` | Mobile number registered with Kotak, including country code, such as `+91...` |
| `KOTAK_UCC` | Your Kotak Neo UCC |
| `KOTAK_ENVIRONMENT` | Leave as `prod` for the live Kotak service |

The login page asks for a fresh six-digit TOTP and your MPIN. Do not save a TOTP code in `.env`; it expires quickly. The backend reads this root `.env` file. It does not read `backend/.env`.

In the Kotak API application settings, whitelist the public outbound IP address of the computer or server running the backend. If the backend runs on another machine or in the cloud, whitelist that host's egress IP, not the browser computer's address. Use a static egress IP if the address changes.

Keep `.env` private. It is excluded from Git.

### 2. Install backend dependencies

Use a supported Python version from 3.10 through 3.13. Replace 3.12 in the command below if needed.

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

### 3. Install frontend dependencies

```powershell
Set-Location frontend
npm ci
Set-Location ..
```

### 4. Start the app

```powershell
powershell.exe -ExecutionPolicy Bypass -File ./run_servers.ps1 -Action start
```

Open [http://localhost:5173](http://localhost:5173). The backend is at [http://localhost:8001](http://localhost:8001), its health check is [http://localhost:8001/health](http://localhost:8001/health), and interactive API docs are at [http://localhost:8001/docs](http://localhost:8001/docs).

The start script runs both servers in the background and writes logs to the repository root. Use [SERVER_COMMANDS.md](SERVER_COMMANDS.md) for start, stop, restart, and status commands.

## Updating after a Git pull

From the repository root:

```powershell
git pull
.venv/Scripts/python.exe -m pip install -r requirements.txt
Set-Location frontend
npm ci
Set-Location ..
powershell.exe -ExecutionPolicy Bypass -File ./run_servers.ps1 -Action restart
```

The root `.env` is ignored by Git and stays on your machine. The guarded copy command above leaves an existing `.env` untouched. Repeating the dependency installation commands after a pull keeps the local Python and Node packages aligned with the repository.

## Manual development startup

For visible foreground servers, open two PowerShell windows at the repository root.

Backend:

```powershell
.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8001 --reload
```

Frontend:

```powershell
Set-Location frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

## What runs in this repository

- `frontend/`: React, TypeScript, and Vite dashboard
- `backend/`: FastAPI routes and the Kotak client/session wrapper
- `neo_api_client/`: local Kotak Neo Python SDK
- `screener/`: swing-trade scanner
- `backend/data/nifty50_constituents.csv`: fallback list of Nifty 50 constituents
- `docs/`: Kotak SDK endpoint references
- `run_servers.ps1`: Windows background server helper

The backend keeps the Kotak login session in memory. Log in again after restarting the backend or after Kotak expires the session. Live prices and option data depend on the broker session, broker market data, and available instrument tokens.

AMO orders are currently rejected by the backend with a service-unavailable response. Submit AMO orders through Kotak Neo directly until this route is re-enabled.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Login says consumer key is missing | Confirm you edited the root `.env`, then restart the backend |
| Kotak rejects login or order requests | Check the consumer key, current TOTP, UCC/mobile number, and the backend host's whitelisted public IP |
| Page says session expired after restart | This is expected; the backend session is in memory. Log in again |
| Start script says Python environment not found | Create `.venv` and install `requirements.txt` as shown above |
| `npm ci` or start script cannot find npm | Install Node.js 18 or 20+ and reopen PowerShell |
| Port 5173 or 8001 is already in use | Stop the other process or use [SERVER_COMMANDS.md](SERVER_COMMANDS.md) to stop this app |
| Symbols appear without live prices | Confirm login and check the backend log for Kotak instrument-master or token errors; the constituent list alone does not provide live prices |

## Further documentation

- [Windows server commands](SERVER_COMMANDS.md)
- [Architecture and API notes](IMPLEMENTATION_GUIDE.md)
- [Kotak API endpoint references](docs/)
## One-click desktop controls

The desktop shortcuts Kotak Neo Terminal - Start and Kotak Neo Terminal - Stop start or stop both servers. The server processes run in the background; the launcher window shows the result and waits for a key before closing. Start leaves both occupied app ports untouched, so clicking it again will not restart the session. Use the Restart command in SERVER_COMMANDS.md when you intend to restart the app.

You can also double-click Start Trading Terminal.cmd or Stop Trading Terminal.cmd in the repository folder. After pulling the repository onto another Windows computer, create its desktop shortcuts once by running this from the repository root:

    powershell.exe -ExecutionPolicy Bypass -File ./create_desktop_shortcuts.ps1

If you move the repository folder, rerun that command to update the shortcut paths.
