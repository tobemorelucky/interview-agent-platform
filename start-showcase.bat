@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-showcase.ps1" %*
exit /b %errorlevel%
