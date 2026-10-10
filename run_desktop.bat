@echo off
title Albion Market Pulse - Desktop Launcher
cd /d "%~dp0"

echo ========================================================
echo       Albion Market Pulse - Desktop Companion App
echo ========================================================
echo.

:: Check for virtual environment
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

:: Run the desktop app
python run_desktop.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Application closed with code %ERRORLEVEL%.
    pause
)
