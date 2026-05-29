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

REM ---- Clean up any leftover processes from previous run ----
echo [0/4] Cleaning up old processes...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /T /F 2>nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /T /F 2>nul
)
taskkill /FI "WINDOWTITLE eq IAP_DEV_API_8000" /T /F 2>nul
taskkill /FI "WINDOWTITLE eq IAP_DEV_WEB_5173" /T /F 2>nul
taskkill /FI "WINDOWTITLE eq IAP_DEV_WORKER" /T /F 2>nul
taskkill /FI "WINDOWTITLE eq IAP_DEV_WORKER_EXITED" /T /F 2>nul

REM ---- Docker ----
echo [1/4] Starting Docker infrastructure (PG + Redis + MinIO + Milvus + SearXNG)...
cd /d "%ROOT%"
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "DATESTAMP=%%a"
docker compose up -d >> "%LOGDIR%\docker-%DATESTAMP%.log" 2>&1

REM ---- API ----
echo [2/4] Starting API on http://localhost:8000/docs
start "IAP_DEV_API_8000" "%RUNDIR%\run-api.bat"

REM ---- Worker (optional, needed for resume processing) ----
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
echo   SearXNG:   http://localhost:8080
echo   Logs:      logs\dev\*-%DATESTAMP%.log
echo.
echo   SearXNG check:
echo      cd apps\api
echo      uv run python scripts\check_searxng.py "github"
echo.
echo   Stop all:  stop-dev.bat
echo   Status:    status-dev.bat
echo.
echo   This window will close in 3s.
echo   Dev log windows (API/Web/Worker) will stay open.
timeout /t 3 >nul
exit /b
