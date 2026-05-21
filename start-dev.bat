@echo off
setlocal enabledelayedexpansion

set ROOT=%~dp0
set RUNTIME=%ROOT%.runtime\dev

if not exist "%RUNTIME%" mkdir "%RUNTIME%"

echo ============================================
echo   Interview Agent Platform - Start Dev
echo ============================================
echo.

:: ---- Docker ----
echo [1/4] Starting Docker infrastructure...
docker compose up -d

:: ---- API ----
echo [2/4] Starting API on http://localhost:8000/docs
powershell -Command ^
  "$p = Start-Process cmd -ArgumentList '/k','cd /d %ROOT%apps\api && title API :8000 && uv run uvicorn interview_api.main:app --reload --host 0.0.0.0 --port 8000' -WindowStyle Normal -PassThru;" ^
  "$p.Id | Out-File '%RUNTIME%\api.pid' -NoNewline"

:: ---- Worker ----
echo [3/4] Starting Worker (Celery, pool=solo)
powershell -Command ^
  "$p = Start-Process cmd -ArgumentList '/k','cd /d %ROOT%apps\worker && title Worker && uv run celery -A interview_worker.celery_app worker -l info --pool=solo' -WindowStyle Normal -PassThru;" ^
  "$p.Id | Out-File '%RUNTIME%\worker.pid' -NoNewline"

:: ---- Web ----
echo [4/4] Starting Web on http://localhost:5173
powershell -Command ^
  "$p = Start-Process cmd -ArgumentList '/k','cd /d %ROOT%apps\web && title Web :5173 && pnpm dev' -WindowStyle Normal -PassThru;" ^
  "$p.Id | Out-File '%RUNTIME%\web.pid' -NoNewline"

echo.
echo ============================================
echo   All services started
echo ============================================
echo.
echo   API:       http://localhost:8000/docs
echo   Web:       http://localhost:5173
echo.
echo   Check status:   status-dev.bat
echo   Stop all:       stop-dev.bat
echo.
pause
