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
echo   Phase 3: Resume processing runs through Celery Worker.
echo   Required: PostgreSQL + Redis + MinIO + LLM_API_KEY
echo   Worker must stay connected for resume and KB document processing.
echo.

REM ---- Docker ----
echo [1/4] Starting Docker infrastructure (PG + Redis + MinIO + Milvus)...
cd /d "%ROOT%"
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "DATESTAMP=%%a"
docker compose up -d >> "%LOGDIR%\docker-%DATESTAMP%.log" 2>&1

REM ---- API ----
echo [2/4] Starting API on http://localhost:8000/docs
start "IAP_DEV_API_8000" "%RUNDIR%\run-api.bat"

REM ---- Worker (optional for Phase 3, needed for KB doc ingestion) ----
echo [3/4] Starting Worker (Celery, pool=solo) [optional for resume]
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
echo Window will close in 3s, or press any key to close now...
timeout /t 3 >nul
exit /b
