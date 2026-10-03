@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\status-interview-platform.ps1" %*
exit /b %errorlevel%
