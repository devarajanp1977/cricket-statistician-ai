@echo off
setlocal
call "%~dp0stop_local_services.bat" %*
exit /b %ERRORLEVEL%