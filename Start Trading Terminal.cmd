@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_servers.ps1" -Action start
set "result=%ERRORLEVEL%"
echo.
if not "%result%"=="0" echo Start failed with exit code %result%.
pause
exit /b %result%