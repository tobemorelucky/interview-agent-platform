@echo off
setlocal

set ROOT=%~dp0
set LOGDIR=%ROOT%logs\dev
set RUNDIR=%ROOT%.runtime\dev

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
if not exist "%RUNDIR%" mkdir "%RUNDIR%"

REM Clean up stale PID files from previous runs
del "%RUNDIR%\api.pid" 2>nul
del "%RUNDIR%\worker.pid" 2>nul
del "%RUNDIR%\web.pid" 2>nul

echo ============================================
echo   Interview Agent Platform - Start Dev
echo ============================================
echo.

REM ---- Docker ----
echo [1/4] Starting Docker infrastructure...
docker compose up -d > "%LOGDIR%\docker.log" 2>&1

REM ---- API ----
echo [2/4] Starting API on http://localhost:8000/docs
start "API :8000" "%RUNDIR%\run-api.bat"

REM ---- Worker ----
echo [3/4] Starting Worker (Celery, pool=solo)
start "Worker" "%RUNDIR%\run-worker.bat"

REM ---- Web ----
echo [4/4] Starting Web on http://localhost:5173
start "Web :5173" "%RUNDIR%\run-web.bat"

echo.
echo ============================================
echo   All services started
echo ============================================
echo.
echo   API:       http://localhost:8000/docs
echo   Web:       http://localhost:5173
echo   Logs:      logs\dev\*.log
echo.
echo   Tail logs: Get-Content logs\dev\api.log -Wait
echo.
echo   Stop all:  stop-dev.bat
echo.
pause
