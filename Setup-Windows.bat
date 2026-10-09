@echo off
title Setup Albion Market Pulse (Windows)
cd /d "%~dp0"

echo ======================================================
echo    ⚔️  Albion Market Pulse - Windows Setup Assistant
echo ======================================================
echo.

:: 1. Check Python
set PYTHON_CMD=
where python >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=python
) else (
    where py >nul 2>&1
    if %errorlevel% equ 0 (
        set PYTHON_CMD=py
    )
)

if "%PYTHON_CMD%"=="" (
    echo [ERROR] Python is not installed or not added to PATH!
    echo.
    echo Please install Python 3.11+ from https://www.python.org/downloads/
    echo IMPORTANT: Make sure to check '[X] Add python.exe to PATH' during installation.
    echo.
    pause
    exit /b 1
)

echo [1/4] Setting up Python virtual environment (.venv)...
if not exist ".venv" (
    %PYTHON_CMD% -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -r requirements.txt

echo.
echo [2/4] Setting up config.json...
if not exist "config.json" (
    copy config.example.json config.json >nul
    echo Config file created from template.
) else (
    echo Config file already exists.
)

echo.
echo [3/4] Checking Albion Data Client (Live Ingest)...
if not exist "albiondata-client\albiondata-client.exe" (
    if not exist "albiondata-client.exe" (
        echo Albion Data Client is missing. Downloading Windows release from GitHub...
        powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/ao-data/albiondata-client/releases/download/v0.1.59/albiondata-client-windows-amd64.zip' -OutFile 'albiondata-client.zip'"
        if exist "albiondata-client.zip" (
            echo Extracting client...
            powershell -Command "Expand-Archive -Path 'albiondata-client.zip' -DestinationPath 'albiondata-client' -Force"
            del albiondata-client.zip >nul 2>&1
            echo [SUCCESS] Albion Data Client downloaded and extracted into albiondata-client folder!
        ) else (
            echo [WARNING] Could not auto-download client. You can manually download from:
            echo https://github.com/ao-data/albiondata-client/releases
        )
    )
) else (
    echo [OK] Albion Data Client is already present.
)

echo.
echo [4/4] Creating Desktop Shortcut...
call Create-Desktop-Shortcut.bat

echo.
echo ======================================================
echo  SETUP COMPLETE!
echo.
echo  CRITICAL FOR WINDOWS INGESTION:
echo  1. Make sure you install Npcap from https://npcap.com/
echo     (Check the box: 'Install Npcap in WinPcap API-compatible Mode')
echo  2. To start, simply double-click the 'Albion Market Pulse'
echo     shortcut on your Desktop!
echo ======================================================
echo.
pause
