@echo off
REM Starts the Valorant coach. Close this window or press Ctrl+C to stop.
cd /d "%~dp0"
title Valorant Coach

call "%~dp0_python.bat"
if not defined PYOK (
    pause
    exit /b 1
)

%PY% main.py live %*
echo.
pause
