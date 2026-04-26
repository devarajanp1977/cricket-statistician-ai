@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0start_local_services.ps1" %*
exit /b %ERRORLEVEL%