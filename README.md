# Kotak Neo Personal Trading Terminal

A Windows desktop-launched trading dashboard built with React, TypeScript and FastAPI. The live terminal connects to Kotak Neo; the separate paper workspace uses INDstocks market data. This repository also contains the Kotak Neo Python SDK, research tools and endpoint references.

**Resetting your PC? Complete the [backup checklist](docs/WINDOWS_RECOVERY.md#before-resetting-windows) before deleting or resetting anything.** Git restores the code and setup scripts. Your private `.env`, saved trading data and browser logins need a separate backup.

## Restore on a fresh Windows PC

1. Install [Git for Windows](https://git-scm.com/downloads/win), then clone your repository:

   ```powershell
   git clone https://github.com/abhi1997ku/Kotak-neo-api-v2.git
   cd Kotak-neo-api-v2
   ```

   You can also use GitHub's **Code > Download ZIP**, then extract the entire folder before continuing. Keep the folder somewhere permanent; desktop shortcuts point to it.

2. Double-click **[Setup.cmd](Setup.cmd)** in the project folder. Keep the window open until setup reports success. The first run needs an internet connection. Native Windows installer or security prompts may appear when missing software is installed.

3. Restore your private root `.env` from backup, or edit the `.env` that setup created. Restore any saved data using the [recovery guide](docs/WINDOWS_RECOVERY.md#after-resetting-windows), while the app is stopped.

4. Double-click **Kotak Neo Terminal - Start** on your desktop. The live and paper dashboards open in your browser. Log in to Kotak with a fresh TOTP and your MPIN. Use **Kotak Neo Terminal - Stop** when you want to stop the app's background processes.

After the one-time setup and account configuration, the desktop Start and Stop shortcuts are the normal controls. Closing a browser tab does not stop the backend or its workers.

### What setup installs

`Setup.cmd` runs `setup.ps1`. It checks for Python 3.14 and Node.js 22 or newer with npm, installs missing runtimes through Windows Package Manager (`winget`), creates an isolated `.venv`, installs the pinned Python packages from `requirements-windows.lock.txt`, runs `npm ci` in `frontend`, and creates desktop shortcuts. It copies `.env.example` to `.env` only when `.env` is missing.

The Python lockfile captures this project's working environment, including research, test and indicator-observer packages. Python 3.14 and Node.js 24 are the versions used by the existing working installation. If Node is missing or too old, setup installs the LTS version supplied by the `OpenJS.NodeJS.LTS` package.

Start also checks setup automatically. Dependencies are installed again when the dependency files or runtime change, or when the local installation is missing. An ordinary launch with a current installation skips package installation. The launcher builds the frontend and runs the servers in the background.

Use Windows 10/11 with Windows PowerShell 5.1 or later. If `winget` is unavailable, install or update Microsoft's **App Installer**, or install [Python 3.14](https://www.python.org/downloads/windows/) and [Node.js](https://nodejs.org/en/download) manually, reopen the setup window, and run `Setup.cmd` again. Git is needed for cloning and pulling; Download ZIP works without it.

Setup cannot restore your broker credentials, generate account tokens or recover deleted local trading history. It does not log in or place orders.

## Account configuration

Edit **`.env` in the project root**. The backend does not use `backend/.env`.

| Variable | What to enter |
| --- | --- |
| `KOTAK_CONSUMER_KEY` | Consumer key from your active Kotak Neo Trade API application |
| `KOTAK_MOBILE` | Registered mobile number including country code, such as `+91...` |
| `KOTAK_UCC` | Your Kotak Neo UCC |
| `KOTAK_ENVIRONMENT` | `prod` for the live Kotak service |
| `INDSTOCKS_ACCESS_TOKEN` | Optional market-data token for the paper workspace; generate it through your INDstocks account |

Enter a fresh TOTP and your MPIN in the login form. Do not save an expiring TOTP code in `.env`. Restart the app after changing `.env`.

Keep `.env` private; Git ignores it. In Kotak's API application settings, check the backend computer's whitelisted public outbound IP after reinstalling Windows or changing your connection. A fresh installation also requires a new browser login. Restarting the backend clears its in-memory Kotak session.

## Daily controls

| Control | Result |
| --- | --- |
| **Kotak Neo Terminal - Start** / `Start Trading Terminal.cmd` | Starts live and paper workspaces, including the paper workers |
| **Kotak Neo Terminal - Stop** / `Stop Trading Terminal.cmd` | Stops both workspaces and their launcher-managed workers |
| **Kotak Neo Paper - Start** / `Start Paper Research.cmd` | Starts only paper research |
| **Kotak Neo Paper - Stop** / `Stop Paper Research.cmd` | Stops only paper research |
| `Setup.cmd` | Checks installation and refreshes desktop shortcuts |

| Workspace | Dashboard | Backend and health check |
| --- | --- | --- |
| Live trading | [localhost:5173](http://localhost:5173/) | [127.0.0.1:8001/health](http://127.0.0.1:8001/health), `backend.live_app:app` |
| Paper research | [localhost:5174](http://localhost:5174/?workspace=paper) | [127.0.0.1:8003/health](http://127.0.0.1:8003/health), `backend.indstocks_app:app` |

Start leaves an already running workspace in place. To deliberately restart it, use the commands in [SERVER_COMMANDS.md](SERVER_COMMANDS.md). Restarting clears the broker login. Review [managed-trade recovery](docs/MANAGED_LIVE.md) before stopping or resetting a machine with locally managed positions.

If you move the project folder, run `Setup.cmd` again to refresh the shortcut paths. A Python virtual environment cannot be restored by copying it from another PC; setup recreates the installation.

## Update from Git

Stop the app, then run this from the project folder:

```powershell
git pull --ff-only
```

Double-click the desktop Start shortcut afterward. It checks for dependency changes automatically; there is no need to run pip or npm commands for a normal update. `git pull` preserves ignored local files such as `.env` and databases. A new clone does not contain those files.

If Git reports local changes or a conflict, keep the files and resolve the conflict before continuing. See [Git upload and reset recovery](docs/WINDOWS_RECOVERY.md).

## Files and documentation

| Path | Purpose |
| --- | --- |
| `setup.ps1`, `Setup.cmd` | First-run installation and dependency checks |
| `run_servers.ps1` | Start, stop, restart and status commands |
| `create_desktop_shortcuts.ps1` | Creates shortcuts for this folder's location |
| `requirements-windows.lock.txt` | Pinned Python installation used by Windows setup |
| `requirements.txt`, `requirements-research.txt` | Python dependency lists for manual development and research |
| `frontend/package-lock.json` | Frontend dependency versions used by `npm ci` |
| `frontend/` | React dashboard |
| `backend/`, `neo_api_client/` | API services and Kotak SDK |
| `screener/`, `indicators/` | Research and indicator tools |
| `backend/data/`, local `*.db` / `*.sqlite*` files | Saved application state and research data; see the backup guide |
| `.runtime/` | Local setup and process records; recreated on this PC |

Further reading:

- [Start, stop, status and troubleshooting](SERVER_COMMANDS.md)
- [PC reset, private backups and Git upload](docs/WINDOWS_RECOVERY.md)
- [Live trading workspace](docs/LIVE_TRADING.md) and [managed target/stop orders](docs/MANAGED_LIVE.md)
- [INDstocks paper data](docs/indstocks-paper-data.md), [intraday stocks](docs/INTRADAY_STOCKS.md) and [swing research](docs/SWING_EXPECTANCY.md)
- [Architecture notes](IMPLEMENTATION_GUIDE.md) and [Kotak endpoint references](docs/)
- [Indicator research](indicators/README.md)

### Optional indicator observer

The indicator observer is separate from the desktop trading Start/Stop controls. Its Python package is included in the Windows lockfile. It uses installed Microsoft Edge and needs its own website login and chart configuration; no separate browser download is required for the current observer.

Use the observer launchers and read [MULTI_OBSERVER.md](indicators/research/MULTI_OBSERVER.md) for chart setup. Close its own console with Ctrl+C to stop capture. Its private browser profile is stored outside the repository in `%LOCALAPPDATA%\INDstocksIndicatorObserver`.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Setup cannot find Python, Node or npm | Run `Setup.cmd` again after installation completes; reopen the window to refresh PATH. See the manual installation links above. |
| Setup or package download fails | Read the error in the open window, check the internet connection and rerun `Setup.cmd`. Existing `.env` is preserved. |
| Desktop shortcut opens an old or missing folder | Run `Setup.cmd` from the current project folder. |
| Login says consumer key is missing | Edit the root `.env`, then restart the live workspace. |
| Login expires after restart | Log in again with a fresh TOTP and MPIN. |
| Broker login or requests fail | Check your key, UCC/mobile, token validity and the broker's whitelisted public IP. |
| A required port is occupied | Use status in [SERVER_COMMANDS.md](SERVER_COMMANDS.md). The launcher reports unrelated processes rather than stopping them. |
| Paper data is missing after a fresh clone | Restore your private data backup and configure a valid INDstocks token. Git alone does not restore local history. |
| Dashboard fails to load | Check the backend and frontend error logs listed in [SERVER_COMMANDS.md](SERVER_COMMANDS.md). |
