@echo off
setlocal
cd /d "%~dp0"
for /f %%d in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "capture_date=%%d"
if not "%~1"=="" set "capture_date=%~1"
echo Opening 1-minute call and put capture for %capture_date%.
echo Sign in, select a call and put with the same underlying, strike and expiry.
echo Keep both indicators and Table View open on each tab. Sampling every 2 seconds.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -NoShortcuts -NonInteractive
if errorlevel 1 goto setup_failed
"%~dp0.venv\Scripts\python.exe" "%~dp0indicators\research\multi_observer.py" --date "%capture_date%" --include-put --timeframes 1m --interval 2
set "result=%ERRORLEVEL%"
pause
exit /b %result%
:setup_failed
echo Setup failed. See the error above.
pause
exit /b 1
