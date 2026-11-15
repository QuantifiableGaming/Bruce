@echo off
REM Creates "Valorant Coach" and "Valorant Coach Setup" icon shortcuts in this folder and on the desktop.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcuts.ps1"
echo.
echo If you move this folder later, run create_shortcuts.bat again.
pause
