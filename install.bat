@echo off
REM Installs the Python packages for the Valorant coach.
cd /d "%~dp0"

set PY=
where python >nul 2>nul && set PY=python
if not defined PY (
    where py >nul 2>nul && set PY=py
)
if not defined PY (
    echo Python was not found.
    echo.
    echo 1. Download Python 3.15 ^(or 3.14^) from https://www.python.org/downloads/
    echo 2. Run the Windows installer ^(64-bit^).
    echo 3. On the FIRST screen, tick "Add python.exe to PATH", then click "Install Now".
    echo 4. Close this window and double-click install.bat again.
    echo.
    echo If Python is already installed, run the installer again, choose "Modify",
    echo click Next, and tick "Add Python to environment variables".
    pause
    exit /b 1
)

echo Using: %PY%
%PY% --version
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)"
if errorlevel 1 (
    echo.
    echo NOTE: This Python is older than 3.14. The coach still works on 3.11 or later,
    echo but it is tested on Python 3.14 and 3.15. Get the latest from https://www.python.org/downloads/
    echo.
)

echo Installing Python packages...
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo Install failed. Read the error above.
    pause
    exit /b 1
)

if not exist models mkdir models

%PY% -c "import sys; from vcoach.hud import find_tesseract; sys.exit(0 if find_tesseract() else 1)" >nul 2>nul
if not errorlevel 1 goto tess_ok
echo.
echo Tesseract is not installed. The coach needs it to read the ammo, health and round banner.
choice /c YN /n /m "Install Tesseract now? (Y/N): "
if errorlevel 2 goto tess_done
%PY% main.py install-tesseract
goto tess_done
:tess_ok
echo Tesseract found.
:tess_done

%PY% main.py install-voice-model
if errorlevel 1 (
    echo.
    echo NOTE: The voice model download did not work. The coach tries again the next time it starts.
)

echo.
if "%PY%"=="py" (
    echo IMPORTANT: On this PC, type "py" instead of "python" in every command.
    echo Example: py main.py live
    echo.
)
echo Install complete.
echo.
echo Creating icon shortcuts in this folder...
set SC_ARGS=
choice /c YN /n /m "Also put the icons on the desktop? (Y/N): "
if errorlevel 2 set SC_ARGS=-FolderOnly
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcuts.ps1" %SC_ARGS%
echo.
echo Next: the Control Panel opens in your web browser. Follow the steps on its Home page.
echo Later, open it again with the "Valorant Coach Setup" icon.
pause
start "" "%~dp0Coach Setup.bat"
