@echo off
title Build Albion Market Pulse .exe
cd /d "%~dp0"

echo ======================================================
echo    Building dist\AlbionMarketPulse.exe
echo ======================================================

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1 && (py -3 -m venv .venv) || (python -m venv .venv)
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt pyinstaller==6.16.0 || goto :fail

:: Regenerate recipes from the latest game data (optional; uses the bundled copy if offline)
python tools\build_recipes.py || echo [WARN] Could not refresh recipes, using the existing data\recipes.json

python -m pytest -q || goto :fail
pyinstaller --noconfirm --clean AlbionMarketPulse.spec || goto :fail

echo.
echo  SUCCESS: dist\AlbionMarketPulse.exe
echo  Copy it anywhere and double-click it. config.json and the cache are created next to it.
pause
exit /b 0

:fail
echo.
echo  BUILD FAILED - see the messages above.
pause
exit /b 1
