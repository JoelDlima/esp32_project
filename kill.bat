@echo off
title Killing all local dev processes...

:: Kill Python processes (hand_detect.py, flask, etc.)
taskkill /IM python.exe /F >nul 2>&1
taskkill /IM python3.exe /F >nul 2>&1
taskkill /IM pythonw.exe /F >nul 2>&1

:: Kill anything on common dev ports: 5000, 3000, 8000, 8080, 4200, 5173
for %%p in (5000 3000 8000 8080 4200 5173 1880) do (
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%%p "') do (
        taskkill /PID %%a /F >nul 2>&1
    )
)

echo All dev processes killed. Ports 5000/3000/8000/8080 are free.
timeout /t 1 /nobreak >nul
