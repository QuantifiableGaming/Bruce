@echo off
REM Opens the Valorant Coach Control Panel in your web browser.
REM Keep this window open while you use the panel. Close it to stop the panel.
cd /d "%~dp0"
title Valorant Coach - Control Panel (keep this window open)

call "%~dp0_python.bat"
if not defined PYOK (
    pause
    exit /b 1
)

echo Starting the Valorant Coach Control Panel...
echo Your web browser opens it in a moment.
echo.
echo Keep this window open while you use the panel. You can minimise it.
echo.
%PY% main.py panel
if errorlevel 1 (
    echo.
    echo The control panel stopped with an error. Read the message above.
    echo You can still use "Classic Setup Menu.bat" in the coach folder.
    pause
)
