@echo off
title ESP32-CAM Hand Detection
echo ========================================
echo   Starting ESP32-CAM Hand Detection
echo ========================================

set "ESP32_IP=10.183.227.27"

:: Kill anything already on port 5000
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)

:: Kill any existing Python processes
taskkill /IM python.exe /F >nul 2>&1

:: Start the Python hand detection script
cd /d "%~dp0"
echo Starting hand detection script...
echo (Using ESP32_IP=%ESP32_IP% and waiting for localhost:5000)
start "" /B laptop\venv\Scripts\python.exe laptop\hand_detect.py

:: Wait for Flask to come up before opening the browser
echo Waiting for server to start...
set "READY="
for /l %%i in (1,1,30) do (
    curl -s http://localhost:5000 >nul 2>&1 && set "READY=1" && goto :open_browser
    timeout /t 1 /nobreak >nul
)

:open_browser

:: Open browser
echo Opening http://localhost:5000 ...
start http://localhost:5000

echo.
echo Script running. Open http://localhost:5000 to view.
echo Run kill.bat to stop everything.
echo Press any key to exit this window (script keeps running).
pause >nul
