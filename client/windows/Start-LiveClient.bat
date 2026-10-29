@echo off
rem Starts the Albion Data Client so that your market visits also go to your Albion Market Pulse server.
rem Settings: client.json next to this file (copy client.example.json).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-LiveClient.ps1" %*
pause
