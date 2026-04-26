@echo off
setlocal
call "%~dp0status_local_services.bat" %*
exit /b %ERRORLEVEL%