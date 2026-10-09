@echo off
title Albion Market Pulse
cd /d "%~dp0"

echo ======================================================
echo    Albion Market Pulse - Starting Up...
echo ======================================================
echo.

:: Check for config.json
if not exist "config.json" (
    if exist "config.example.json" (
        echo Creating config.json from template...
        copy config.example.json config.json >nul
    )
)

:: Check for Python virtual environment
if not exist ".venv" (
    echo Setting up Python virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo Installing dependencies...
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

echo Starting Albion Market Pulse background service...
start /b "" ".venv\Scripts\python.exe" -m albion_flips.cli --watch --web

echo Waiting for server to initialize...
timeout /t 2 /nobreak >nul

echo Opening Web Dashboard...
start "" "http://localhost:8765"

echo.
echo ======================================================
echo  Dashboard running at http://localhost:8765
echo  Close this window anytime or leave it running.
echo ======================================================
exit
