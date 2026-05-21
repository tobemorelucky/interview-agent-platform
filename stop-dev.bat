@echo off
setlocal enabledelayedexpansion

set ROOT=%~dp0
set PIDDIR=%ROOT%.runtime\dev

echo ============================================
echo   Interview Agent Platform - Stop Dev
echo ============================================
echo.

REM ---- Kill API (PID file first, then port fallback) ----
echo [1/4] Stopping API...
set KILLED=0
if exist "%PIDDIR%\api.pid" (
    set /p API_PID=<"%PIDDIR%\api.pid"
    taskkill /PID !API_PID! /T /F 2>nul
    if !errorlevel!==0 set KILLED=1
    del "%PIDDIR%\api.pid" 2>nul
)
if !KILLED!==0 (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
        taskkill /PID %%a /T /F 2>nul
        set KILLED=1
    )
)
if !KILLED!==0 (echo   API was not running) else (echo   API stopped)

REM ---- Kill Worker (PID file first, then window title fallback) ----
echo [2/4] Stopping Worker...
set KILLED=0
if exist "%PIDDIR%\worker.pid" (
    set /p WORKER_PID=<"%PIDDIR%\worker.pid"
    taskkill /PID !WORKER_PID! /T /F 2>nul
    if !errorlevel!==0 set KILLED=1
    del "%PIDDIR%\worker.pid" 2>nul
)
if !KILLED!==0 (
    taskkill /FI "WINDOWTITLE eq Worker" /T /F 2>nul
    if !errorlevel!==0 set KILLED=1
)
if !KILLED!==0 (echo   Worker was not running) else (echo   Worker stopped)

REM ---- Kill Web (PID file first, then port fallback) ----
echo [3/4] Stopping Web...
set KILLED=0
if exist "%PIDDIR%\web.pid" (
    set /p WEB_PID=<"%PIDDIR%\web.pid"
    taskkill /PID !WEB_PID! /T /F 2>nul
    if !errorlevel!==0 set KILLED=1
    del "%PIDDIR%\web.pid" 2>nul
)
if !KILLED!==0 (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173 " ^| findstr "LISTENING"') do (
        taskkill /PID %%a /T /F 2>nul
        set KILLED=1
    )
)
if !KILLED!==0 (echo   Web was not running) else (echo   Web stopped)

REM ---- Clean up any remaining runner cmd windows ----
taskkill /FI "WINDOWTITLE eq API :8000" /T /F 2>nul
taskkill /FI "WINDOWTITLE eq Worker" /T /F 2>nul
taskkill /FI "WINDOWTITLE eq Web :5173" /T /F 2>nul

REM ---- Docker ----
echo [4/4] Stopping Docker containers...
docker compose -f "%ROOT%docker-compose.yml" down --timeout 30 2>nul

echo.
echo ============================================
echo   All services stopped
echo ============================================

REM Auto-close after 5 seconds (press any key to close sooner)
timeout /t 5 /nobreak >nul
exit /b 0
