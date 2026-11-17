@echo off
REM Starts the Valorant coach. Close this window or press Ctrl+C to stop.
cd /d "%~dp0"
title Valorant Coach

call "%~dp0_python.bat"
if not defined PYOK (
    pause
    exit /b 1
)

REM A video dropped on this file goes to the split tool, not to the coach.
for %%E in (.mp4 .mkv .mov .avi) do if /i "%~x1"=="%%E" (
    if not exist "%~dp0vcoach\split_video.py" (
        echo This is a video file. The coach cannot use a video file. Start the coach with no file.
        pause
        exit /b 1
    )
    echo This is a video file. It goes to the split tool, not to the coach.
    echo.
    %PY% -m vcoach.split_video %*
    echo.
    pause
    exit /b 0
)

%PY% main.py live %*
echo.
pause
