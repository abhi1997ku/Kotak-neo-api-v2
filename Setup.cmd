@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "result=%ERRORLEVEL%"
echo.
if not "%result%"=="0" echo Setup failed with exit code %result%. Read the message above before trying again.
pause
exit /b %result%
