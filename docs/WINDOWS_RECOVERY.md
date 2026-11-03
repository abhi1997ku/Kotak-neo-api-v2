# Back up, reset Windows and restore

The repository is `https://github.com/abhi1997ku/Kotak-neo-api-v2.git`. A complete recovery needs both the latest code on GitHub and a separate private copy of local credentials and saved data.

## Before resetting Windows

1. Finish any local trading management and review [managed-trade recovery](MANAGED_LIVE.md). The desktop Stop control stops local processes; it does not close broker positions or cancel broker orders.
2. Use **Kotak Neo Terminal - Stop** and close any separately started developer servers, data tools and indicator observers. Check status as described in [SERVER_COMMANDS.md](../SERVER_COMMANDS.md).
3. Upload the latest source changes to GitHub. Check the remote repository in a browser and confirm the latest commit contains your source, `Setup.cmd`, `setup.ps1`, the dependency lockfiles and these instructions.
4. Copy the private files below to an external drive or another trusted backup location that will survive the reset. Do not keep the only backup on the drive you will erase.
5. Open the backup and verify that `.env` and the saved database files are present. Keep this backup private and preferably encrypted.

### What needs a private backup

| File or folder | Why it matters |
| --- | --- |
| Root `.env` | Kotak credentials and optional INDstocks token; excluded from Git |
| Entire `backend/data/` | Managed live state, paper journals, cached market data and research reports |
| Root `*.db`, `*.sqlite`, `*.sqlite3` files and their sidecars | Screener results, feed history and other local state |
| `backend/paper_trading.db` and any other backend database files | Older paper-trading records |
| `screener/training_data/` | Saved paper portfolios, configuration, journals and historical data |
| `screener/index_research_cache/` | Cached research data, if you want to keep it |
| Your indicator capture files, including `indicators/research/sessions/` | Captured observations that may not be in the uploaded commit |
| Other files you created or imported but have not committed | Reports, exports and personal research inputs |

SQLite can store recent changes in accompanying `-wal`, `-shm` or `-journal` files. Copy databases only after all writing processes have stopped, and include any remaining sidecars. Copying just the main database while a process is running can lose recent records. If a service must remain running, use a proper SQLite backup rather than an ordinary file copy.

Browser logins are separate from Git and `.env`. Plan to sign in again. The indicator observer uses a private profile at `%LOCALAPPDATA%\INDstocksIndicatorObserver`; if you choose to back it up, close Edge first and treat that profile as sensitive account data.

You do not need to back up `.venv/`, `frontend/node_modules/`, `frontend/dist*`, `.runtime/`, Python caches or desktop `.lnk` files. Setup recreates them. Logs are optional troubleshooting history and can contain private information.

### Example private backup

The following PowerShell example copies the main local data after you have stopped the app. **Change `E:\Kotak-Neo-private-backup` to your real external backup location first.** It does not upload anything.

```powershell
$repoPath = (Get-Location).Path
$backupPath = 'E:\Kotak-Neo-private-backup'
New-Item -ItemType Directory -Path $backupPath -Force | Out-Null

Copy-Item -LiteralPath (Join-Path $repoPath '.env') -Destination $backupPath
New-Item -ItemType Directory -Path (Join-Path $backupPath 'backend') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repoPath 'backend\data') -Destination (Join-Path $backupPath 'backend') -Recurse

foreach ($relativePath in @('', 'backend')) {
    $sourcePath = if ($relativePath) { Join-Path $repoPath $relativePath } else { $repoPath }
    $targetPath = if ($relativePath) { Join-Path $backupPath $relativePath } else { $backupPath }
    Get-ChildItem -LiteralPath $sourcePath -File |
        Where-Object { $_.Name -match '\.(db|sqlite|sqlite3)(-(wal|shm|journal))?$' } |
        Copy-Item -Destination $targetPath
}

foreach ($relativePath in @('screener\training_data', 'screener\index_research_cache', 'indicators\research\sessions')) {
    $sourcePath = Join-Path $repoPath $relativePath
    if (Test-Path -LiteralPath $sourcePath) {
        $targetParent = Join-Path $backupPath (Split-Path $relativePath -Parent)
        New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        Copy-Item -LiteralPath $sourcePath -Destination $targetParent -Recurse
    }
}
```

Run this from the project root. Use a new empty backup folder for each backup, so old and new SQLite sidecars are not mixed. Add any personal files or capture outputs outside the listed folders. Verify the result before resetting Windows.

## Upload source to GitHub

Keep credentials and personal account data out of commits, even if you use a private repository. `.gitignore` excludes dependencies, virtual environments, logs, databases and known local caches. Ignoring a file does not remove it from older commits or stop tracking a previously committed file.

From the project root, review before uploading:

```powershell
git status --short
git remote -v
git diff --stat
git add .
git diff --cached --stat
git diff --cached --name-only
```

Check the staged file list and content. It should include the source and setup files, not `.env`, tokens, database sidecars, installed packages or personal records. Then commit and push:

```powershell
git commit -m "Add Windows setup and recovery documentation"
git push origin main
```

These commands use this repository's existing `origin` and `main` branch. If Git asks, sign in to your GitHub account. If a push is rejected, resolve it and verify the remote commit before resetting; a local commit alone is not an uploaded backup. If Git says there is nothing to commit, check that the current commit is already pushed.

If generated or private files are already tracked, remove only the relevant files from Git's index with `git rm --cached` before committing; keep their local copies. Check the exact paths first. Do not use a force push or delete local files just to fix an upload error. If a real credential was committed previously, removing the current file does not erase its history; replace that credential through its provider.

## After resetting Windows

1. Install Git for Windows, then clone the repository into a permanent folder, or download and extract its ZIP. Use [README.md](../README.md#restore-on-a-fresh-windows-pc) for the clone command.
2. Double-click **Setup.cmd**. It installs missing Python/Node runtimes, creates the local package environment and desktop shortcuts. The first setup requires internet access.
3. Keep the application stopped. Copy your backed-up `.env` into the new project root. Restore your backed-up data to the same relative folders. Restore each database and any remaining sidecars together from the same stopped backup; do not mix files from different backup dates.
4. Do not restore the old `.venv`, `node_modules`, `.runtime` or shortcuts. They contain machine-specific or rebuildable files.
5. Check Kotak's API IP whitelist and refresh expired INDstocks tokens. Then use **Kotak Neo Terminal - Start**, log in and verify the dashboards and saved history.
6. Review managed live records and broker positions before choosing any reconcile/resume action. Installation and data restoration do not automatically resume broker protection or close positions.

If no private backup exists, the app can still install and start, but you must enter credentials again and local history starts from whatever remains available. Git cannot recover ignored files that were never uploaded.

## Updating without resetting

Stop the app, run `git pull --ff-only`, then use the desktop Start shortcut. The launcher checks the dependency files and installs updates when needed. Run `Setup.cmd` again if you move the project folder or need to recreate shortcuts. Your existing `.env` is preserved.
