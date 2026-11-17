@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\status-showcase.ps1" %*
exit /b %errorlevel%
