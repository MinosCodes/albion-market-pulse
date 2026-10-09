@echo off
title Albion Market Pulse
cd /d "%~dp0"

echo ======================================================
echo    ⚔️ Albion Market Pulse - Starting Up...
echo ======================================================
echo.

:: 1. If server is already running, just open the browser
netstat -ano 2>nul | findstr /C:":8765" | findstr /C:"LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Server is already running on port 8765. Opening browser...
    start "" "http://localhost:8765"
    timeout /t 2 /nobreak >nul
    exit /b 0
)

:: 2. Check for config.json
if not exist "config.json" (
    if exist "config.example.json" (
        echo [INFO] Creating config.json from template...
        copy config.example.json config.json >nul
    )
)

:: 3. Detect Python command
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
    echo.
    echo ======================================================
    echo  [ERROR] Python is not installed or not added to PATH!
    echo.
    echo  Please install Python 3.11+ from https://www.python.org/
    echo  IMPORTANT: During installation, check the box that says:
    echo  [X] "Add python.exe to PATH"
    echo ======================================================
    echo.
    pause
    exit /b 1
)

:: 4. Setup virtual environment if missing
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Setting up Python virtual environment...
    %PYTHON_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [INFO] Installing required dependencies...
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

:: 5. Open browser in background after short delay
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8765"

echo.
echo ======================================================
echo  🚀 Service is LIVE at http://localhost:8765
echo  Leave this window open while playing Albion Online!
echo ======================================================
echo.

:: 6. Run live watch & web server
".venv\Scripts\python.exe" -m albion_flips.cli --watch --web

pause
