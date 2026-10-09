@echo off
title Create Desktop Shortcut - Albion Market Pulse
cd /d "%~dp0"

echo Creating Albion Market Pulse desktop shortcut...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$desktop = [Environment]::GetFolderPath('Desktop'); " ^
  "$shortcut = $ws.CreateShortcut(\"$desktop\Albion Market Pulse.lnk\"); " ^
  "$shortcut.TargetPath = '%~dp0Start-Albion-Pulse.bat'; " ^
  "$shortcut.WorkingDirectory = '%~dp0'; " ^
  "$shortcut.Description = 'Launch Albion Market Pulse Dashboard'; " ^
  "$iconPath = '%~dp0assets\icon.ico'; " ^
  "if (Test-Path $iconPath) { $shortcut.IconLocation = $iconPath }; " ^
  "$shortcut.Save()"

if %errorlevel% equ 0 (
    echo.
    echo ======================================================
    echo  SUCCESS! 'Albion Market Pulse' shortcut created on
    echo  your Windows Desktop.
    echo ======================================================
) else (
    echo.
    echo Failed to create shortcut automatically. You can right-click
    echo Start-Albion-Pulse.bat and select 'Send to -> Desktop (create shortcut)'.
)

pause
