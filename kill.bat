@echo off
title Stopping Hand Detection...

:: Kill Python processes
taskkill /IM python.exe /F >nul 2>&1
taskkill /IM pythonw.exe /F >nul 2>&1

:: Kill anything on port 5000
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)

echo All processes stopped. Port 5000 is free.
timeout /t 2 /nobreak >nul
