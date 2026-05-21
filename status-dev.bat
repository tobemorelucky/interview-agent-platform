@echo off
setlocal

echo ============================================
echo   Interview Agent Platform - Status
echo ============================================
echo.

netstat -ano | findstr :8000 | findstr LISTENING >nul
if %errorlevel%==0 (
    echo   [RUNNING]  API    http://localhost:8000/docs
) else (
    echo   [STOPPED]  API
)

netstat -ano | findstr :5173 | findstr LISTENING >nul
if %errorlevel%==0 (
    echo   [RUNNING]  Web    http://localhost:5173
) else (
    echo   [STOPPED]  Web
)

tasklist /FI "WINDOWTITLE eq Worker" 2>nul | findstr cmd >nul
if %errorlevel%==0 (
    echo   [RUNNING]  Worker
) else (
    echo   [STOPPED]  Worker
)

echo.
echo --- Docker ---
docker compose ps 2>nul
echo.
pause
