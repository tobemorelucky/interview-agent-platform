@echo off
setlocal

echo ============================================
echo   Interview Agent Platform - Status
echo ============================================
echo.

REM ---- API (port 8000) ----
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo   [RUNNING]  API    http://localhost:8000/docs
) else (
    echo   [STOPPED]  API
)

REM ---- Worker (window title) ----
tasklist /FI "WINDOWTITLE eq IAP_DEV_WORKER" 2>nul | findstr "cmd" >nul
if %errorlevel%==0 (
    echo   [RUNNING]  Worker
) else (
    echo   [STOPPED]  Worker
)

REM ---- Web (port 5173) ----
netstat -ano | findstr ":5173 " | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo   [RUNNING]  Web    http://localhost:5173
) else (
    echo   [STOPPED]  Web
)

echo.
echo --- Docker ---
docker info >nul 2>&1
if %errorlevel%==0 (
    docker compose ps 2>nul
) else (
    echo   Docker daemon not reachable
)
echo.
timeout /t 5 /nobreak >nul
exit /b 0
