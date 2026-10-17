@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
set "repoBuildExit=%ERRORLEVEL%"
pause
exit /b %repoBuildExit%
