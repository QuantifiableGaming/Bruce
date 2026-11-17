@echo off
REM Shared helper for the launchers. Finds Python and installs missing packages.
REM Sets PY to the Python command. Sets PYOK=1 when everything is ready.
set PYOK=
set PY=
where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py
if not defined PY (
    echo Python was not found. Install Python 3.15 from python.org, then run install.bat.
    exit /b 1
)

%PY% -c "import cv2, numpy, mss, pyttsx3, pynput, ruamel.yaml, win32com.client, winrt.windows.media.ocr" >nul 2>nul
if errorlevel 1 (
    echo Some Python packages are missing. Installing them now. This takes a minute...
    echo.
    %PY% -m pip install -r "%~dp0requirements.txt"
    %PY% -c "import cv2, numpy, mss, pyttsx3, pynput, ruamel.yaml, win32com.client, winrt.windows.media.ocr" >nul 2>nul
    if errorlevel 1 (
        echo.
        echo The packages could not be installed. Read the error above, or run install.bat.
        exit /b 1
    )
    echo.
    echo Packages installed.
    echo.
)
set PYOK=1
exit /b 0
