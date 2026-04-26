@echo off
setlocal
call "%~dp0restart_local_services.bat" %*
exit /b %ERRORLEVEL%