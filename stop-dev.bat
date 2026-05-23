@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   Interview Agent Platform - Stop Dev
echo ============================================
echo.

REM ---- Kill API by port (most reliable) ----
echo [1/4] Stopping API...
set KILLED=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /T /F 2>nul
    if !errorlevel!==0 set KILLED=1
)
if !KILLED!==0 (echo   API was not running) else (echo   API stopped)

REM ---- Kill Web by port (most reliable) ----
echo [2/4] Stopping Web...
set KILLED=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /T /F 2>nul
    if !errorlevel!==0 set KILLED=1
)
if !KILLED!==0 (echo   Web was not running) else (echo   Web stopped)

REM ---- Kill Worker by window title ----
echo [3/4] Stopping Worker...
taskkill /FI "WINDOWTITLE eq IAP_DEV_WORKER" /T /F 2>nul
if !errorlevel!==0 (
    echo   Worker stopped
) else (
    echo   Worker was not running
)

REM ---- Docker (only if daemon is reachable) ----
echo [4/4] Stopping Docker containers...
docker info >nul 2>&1
if !errorlevel!==0 (
    docker compose down >nul 2>&1
    if !errorlevel!==0 (
        echo   Docker containers stopped
    ) else (
        echo   Docker compose down returned error (containers may already be stopped^)
    )
) else (
    echo   Docker daemon not reachable - skip docker compose down
)

echo.
echo ============================================
echo   All services stopped - window will close in 3s
echo ============================================
timeout /t 3 >nul
exit /b
