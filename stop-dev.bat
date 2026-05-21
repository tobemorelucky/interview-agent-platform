@echo off
setlocal

set ROOT=%~dp0
set RUNTIME=%ROOT%.runtime\dev

echo ============================================
echo   Interview Agent Platform - Stop Dev
echo ============================================
echo.

REM --- API ---
if exist "%RUNTIME%\api.pid" (
    echo Stopping API...
    for /f %%i in ('type "%RUNTIME%\api.pid"') do taskkill /PID %%i /T /F 2>nul
    del "%RUNTIME%\api.pid" 2>nul
) else (
    echo Stopping API by port 8000...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /PID %%a /T /F 2>nul
)

REM --- Worker ---
if exist "%RUNTIME%\worker.pid" (
    echo Stopping Worker...
    for /f %%i in ('type "%RUNTIME%\worker.pid"') do taskkill /PID %%i /T /F 2>nul
    del "%RUNTIME%\worker.pid" 2>nul
) else (
    echo Stopping Worker by window title...
    taskkill /FI "WINDOWTITLE eq Worker" /T /F 2>nul
)

REM --- Web ---
if exist "%RUNTIME%\web.pid" (
    echo Stopping Web...
    for /f %%i in ('type "%RUNTIME%\web.pid"') do taskkill /PID %%i /T /F 2>nul
    del "%RUNTIME%\web.pid" 2>nul
) else (
    echo Stopping Web by port 5173...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5173 ^| findstr LISTENING') do taskkill /PID %%a /T /F 2>nul
)

REM --- Docker ---
echo.
echo Stopping Docker containers...
docker compose down

echo.
echo ============================================
echo   All services stopped
echo ============================================
echo.
pause
