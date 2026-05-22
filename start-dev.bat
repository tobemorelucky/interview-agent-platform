@echo off
setlocal

set ROOT=%~dp0
set LOGDIR=%ROOT%logs\dev
set RUNDIR=%ROOT%.runtime\dev

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
if not exist "%RUNDIR%" mkdir "%RUNDIR%"

echo ============================================
echo   Interview Agent Platform - Start Dev
echo ============================================
echo.

REM ---- Docker ----
echo [1/4] Starting Docker infrastructure...
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "DATESTAMP=%%a"
docker compose up -d >> "%LOGDIR%\docker-%DATESTAMP%.log" 2>&1

REM ---- API ----
echo [2/4] Starting API on http://localhost:8000/docs
start "IAP_DEV_API_8000" "%RUNDIR%\run-api.bat"

REM ---- Worker ----
echo [3/4] Starting Worker (Celery, pool=solo)
start "IAP_DEV_WORKER" "%RUNDIR%\run-worker.bat"

REM ---- Web ----
echo [4/4] Starting Web on http://localhost:5173
start "IAP_DEV_WEB_5173" "%RUNDIR%\run-web.bat"

echo.
echo ============================================
echo   All services started
echo ============================================
echo.
echo   API:       http://localhost:8000/docs
echo   Web:       http://localhost:5173
echo   Logs:      logs\dev\*-%DATESTAMP%.log
echo.
echo   Tail logs: Get-Content logs\dev\api-%DATESTAMP%.log -Wait -Encoding UTF8
echo.
echo   Stop all:  stop-dev.bat
echo.
pause
