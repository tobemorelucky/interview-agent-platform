@echo off
setlocal enabledelayedexpansion

set PIDDIR=%~dp0.runtime\dev

echo ============================================
echo   Interview Agent Platform - Status
echo ============================================
echo.

REM ---- API ----
set STATUS=STOPPED
if exist "%PIDDIR%\api.pid" (
    set /p API_PID=<"%PIDDIR%\api.pid"
    tasklist /FI "PID eq !API_PID!" 2>nul | findstr /c:"!API_PID!" >nul
    if !errorlevel!==0 set STATUS=RUNNING
)
if "!STATUS!"=="STOPPED" (
    netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul
    if !errorlevel!==0 set STATUS=RUNNING
)
if "!STATUS!"=="RUNNING" (
    echo   [RUNNING]  API    http://localhost:8000/docs
) else (
    echo   [STOPPED]  API
)

REM ---- Worker ----
set STATUS=STOPPED
if exist "%PIDDIR%\worker.pid" (
    set /p WORKER_PID=<"%PIDDIR%\worker.pid"
    tasklist /FI "PID eq !WORKER_PID!" 2>nul | findstr /c:"!WORKER_PID!" >nul
    if !errorlevel!==0 set STATUS=RUNNING
)
if "!STATUS!"=="STOPPED" (
    tasklist /FI "WINDOWTITLE eq Worker" 2>nul | findstr "cmd" >nul
    if !errorlevel!==0 set STATUS=RUNNING
)
if "!STATUS!"=="RUNNING" (
    echo   [RUNNING]  Worker
) else (
    echo   [STOPPED]  Worker
)

REM ---- Web ----
set STATUS=STOPPED
if exist "%PIDDIR%\web.pid" (
    set /p WEB_PID=<"%PIDDIR%\web.pid"
    tasklist /FI "PID eq !WEB_PID!" 2>nul | findstr /c:"!WEB_PID!" >nul
    if !errorlevel!==0 set STATUS=RUNNING
)
if "!STATUS!"=="STOPPED" (
    netstat -ano | findstr ":5173 " | findstr "LISTENING" >nul
    if !errorlevel!==0 set STATUS=RUNNING
)
if "!STATUS!"=="RUNNING" (
    echo   [RUNNING]  Web    http://localhost:5173
) else (
    echo   [STOPPED]  Web
)

echo.
echo --- Docker ---
docker compose ps 2>nul
echo.
timeout /t 5 /nobreak >nul
exit /b 0
