@echo off
title ESP32-CAM Hand Detection
echo ========================================
echo   Starting ESP32-CAM Hand Detection
echo ========================================

:: Kill anything already on port 5000
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)

:: Start the Python hand detection script
cd /d C:\YOUR_WIFI_SSID\esp32cam_miniproject
echo Starting hand detection script...
start "" /B .venv\Scripts\python.exe laptop\hand_detect.py

:: Wait for Flask to come up
timeout /t 3 /nobreak >nul

:: Open browser
echo Opening http://localhost:5000 ...
start http://localhost:5000

echo.
echo Script running. Open http://localhost:5000 to view.
echo Run kill.bat to stop everything.
