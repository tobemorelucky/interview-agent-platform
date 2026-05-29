@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   Interview Agent Platform - Stop Dev
echo ============================================
echo.

REM ---- Kill API (port 8000) ----
echo [1/4] Stopping API (port 8000)...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /T /F 2>nul
    set FOUND=1
)
if !FOUND!==0 (echo   API was not running) else (echo   API stopped)

REM ---- Kill Web (port 5173) ----
echo [2/4] Stopping Web (port 5173)...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /T /F 2>nul
    set FOUND=1
)
if !FOUND!==0 (echo   Web was not running) else (echo   Web stopped)

REM ---- Close dev windows by title ----
echo [3/4] Closing dev windows...
taskkill /FI "WINDOWTITLE eq IAP_DEV_API_8000" /T /F 2>nul
taskkill /FI "WINDOWTITLE eq IAP_DEV_WEB_5173" /T /F 2>nul
taskkill /FI "WINDOWTITLE eq IAP_DEV_WORKER" /T /F 2>nul
taskkill /FI "WINDOWTITLE eq IAP_DEV_WORKER_EXITED" /T /F 2>nul
echo   Dev windows closed

REM ---- Docker ----
echo [4/4] Stopping Docker containers (including SearXNG)...
docker info >nul 2>&1
if !errorlevel!==0 (
    docker compose down >nul 2>&1
    if !errorlevel!==0 (
        echo   Docker containers stopped (including SearXNG)
    ) else (
        echo   Docker compose down returned error (containers may already be stopped^)
    )
) else (
    echo   Docker daemon not reachable - skip
)

echo.
echo ============================================
echo   All services stopped
echo ============================================
timeout /t 2 >nul
exit /b
