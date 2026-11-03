@echo off
setlocal
cd /d "%~dp0"
for /f %%d in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "capture_date=%%d"
if not "%~1"=="" set "capture_date=%~1"
echo Opening indicator observer for %capture_date%.
echo Configure all three chart tabs. Keep this console open. Ctrl+C stops capture.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -NoShortcuts -NonInteractive
if errorlevel 1 goto setup_failed
"%~dp0.venv\Scripts\python.exe" "%~dp0indicators\research\multi_observer.py" --date "%capture_date%"
set "result=%ERRORLEVEL%"
pause
exit /b %result%
:setup_failed
echo Setup failed. See the error above.
pause
exit /b 1
