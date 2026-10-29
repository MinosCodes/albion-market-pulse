@echo off
title Build Albion Analyser Setup
cd /d "%~dp0"

echo ======================================================
echo    Building dist\AlbionAnalyser-Setup-^<version^>.exe
echo ======================================================

:: 1. Build the app (tests + PyInstaller)
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1 && (py -3 -m venv .venv) || (python -m venv .venv)
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt pyinstaller==6.16.0 || goto :fail
python -m pytest -q || goto :fail
pyinstaller --noconfirm --clean AlbionMarketPulse.spec || goto :fail

:: 2. Find Inno Setup (winget install --id JRSoftware.InnoSetup -e)
set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
    echo [ERROR] Inno Setup 6 not found. Install it with:
    echo         winget install --id JRSoftware.InnoSetup -e
    goto :fail
)

:: 3. Build the installer with the app version from albion_flips\__init__.py
for /f "delims=" %%V in ('python -c "import albion_flips; print(albion_flips.__version__)"') do set "APPVER=%%V"
"%ISCC%" /DAppVersion=%APPVER% installer\AlbionAnalyser.iss || goto :fail

echo.
echo  SUCCESS: dist\AlbionAnalyser-Setup-%APPVER%.exe
pause
exit /b 0

:fail
echo.
echo  BUILD FAILED - see the messages above.
pause
exit /b 1
