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

REM ---- Redis (Celery broker) ----
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try { $c.Connect('localhost',6379); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
    echo   [RUNNING]  Redis  localhost:6379
) else (
    echo   [STOPPED]  Redis  localhost:6379
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
echo Window will close in 3s, or press any key to close now...
timeout /t 3 >nul
exit /b
