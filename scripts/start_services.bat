@echo off
setlocal
call "%~dp0start_local_services.bat" %*
exit /b %ERRORLEVEL%