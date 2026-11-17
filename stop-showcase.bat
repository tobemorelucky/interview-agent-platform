@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-interview-platform.ps1" %*
exit /b %errorlevel%
