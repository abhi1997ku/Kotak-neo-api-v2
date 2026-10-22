@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_servers.ps1" -Action stop -Workspace paper
set "result=%ERRORLEVEL%"
pause
exit /b %result%
