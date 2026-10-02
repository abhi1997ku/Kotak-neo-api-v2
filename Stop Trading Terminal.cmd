@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_servers.ps1" -Action stop
set "result=%ERRORLEVEL%"
echo.
if not "%result%"=="0" echo Stop failed with exit code %result%.
pause
exit /b %result%