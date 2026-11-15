@echo off
REM Setup menu for the Valorant coach: calibration, settings, and voice/mic tests.
cd /d "%~dp0"
title Valorant Coach - Classic Setup Menu

call "%~dp0_python.bat"
if not defined PYOK (
    pause
    exit /b 1
)

:menu
cls
echo ==============================================
echo            VALORANT COACH - SETUP
echo ==============================================
echo.
echo   1. Region editor - drag the boxes on a screenshot (recommended)
echo   2. Calibrate from the game screen (picture + text check)
echo   3. Calibrate from a recorded clip
echo   4. Edit all settings (config.yaml)
echo   5. Choose and hear a personality
echo   6. Test the microphone and "Bruce"
echo   7. List microphones
echo   8. List Windows voices
echo   9. Open the quick start guide
echo   A. AI coach setup (Claude or ChatGPT)
echo   T. Test the AI coach lines
echo   I. Install Tesseract (the number reader)
echo   V. Download the voice model (for "Bruce")
echo   Q. Quit
echo.
choice /c 123456789ATIVQ /n /m "Choose an option: "
if errorlevel 14 exit /b 0
if errorlevel 13 goto voskdl
if errorlevel 12 goto tess
if errorlevel 11 goto aitest
if errorlevel 10 goto aisetup
if errorlevel 9 goto guide
if errorlevel 8 goto voices
if errorlevel 7 goto mics
if errorlevel 6 goto listen
if errorlevel 5 goto preview
if errorlevel 4 goto edit
if errorlevel 3 goto calclip
if errorlevel 2 goto calscreen
if errorlevel 1 goto editor

:editor
cls
echo REGION EDITOR
echo.
echo Your web browser opens the editor.
echo 1. Start Valorant in Windowed Fullscreen and join a Deathmatch.
echo 2. In the editor, click "Take screenshot", then switch to Valorant.
echo 3. Drag the coloured boxes over the ammo, health and banner areas.
echo 4. Click "Test reading", then "Save to config".
echo 5. Click "Done" in the editor to come back to this menu.
echo.
%PY% main.py editor
pause
goto menu

:calscreen
cls
echo CALIBRATE FROM THE GAME SCREEN
echo.
echo 1. Start Valorant in Windowed Fullscreen and join a Deathmatch.
echo 2. Press a key here. You then have 10 seconds to switch to Valorant.
echo 3. Stay in the game until this window says the picture is saved.
echo.
pause
%PY% main.py calibrate --delay 10
if errorlevel 1 goto calfail
goto calresult

:calclip
cls
echo CALIBRATE FROM A RECORDED CLIP
echo.
set CLIP=
set /p CLIP=Drag the video file into this window, then press Enter:
set AT=
set /p AT=Seconds into the video to use (for example 30):
if "%AT%"=="" set AT=0
%PY% main.py calibrate --video %CLIP% --at %AT%
if errorlevel 1 goto calfail
goto calresult

:calfail
echo.
echo Calibration did not finish. Read the error above.
echo If you cannot fix it, send a screenshot of this window.
echo.
pause
goto menu

:calresult
echo.
if exist calibration.png start "" calibration.png
echo The picture shows the regions as green boxes and enemy highlight pixels in magenta.
echo Check that each green box covers its item: ammo number, health number, round banner.
echo Check that Ammo and Health above show the numbers that were on screen.
echo.
choice /c YN /n /m "Open the region editor to move a box? (Y/N): "
if errorlevel 2 goto menu
goto editor

:edit
start "" notepad config.yaml
echo.
echo Notepad is open. Change the numbers under "regions", then save the file.
echo Each region is [left, top, width, height] as a fraction of the screen (0 to 1).
echo After you save, run calibration again to check the result.
echo.
pause
goto menu

:preview
cls
%PY% main.py personalities
echo.
set PERS=
set /p PERS=Type a personality name (for example sassy):
if "%PERS%"=="" goto menu
%PY% main.py preview --personality %PERS%
echo.
echo To make this the default, set coach: personality: in config.yaml (option 4).
pause
goto menu

:listen
cls
echo Say "Bruce, how am I doing?" The window shows what the coach heard.
echo Press Ctrl+C, then N, to go back to the menu.
echo.
%PY% main.py listen
pause
goto menu

:mics
cls
%PY% main.py mics
echo.
echo To use a mic that is not the Windows default, put its number in
echo voice_control: mic_device: in config.yaml (option 4).
pause
goto menu

:voices
cls
%PY% main.py voices
echo.
echo To use a voice, put part of its name in voice: name_contains: in a personality file.
pause
goto menu

:guide
start "" notepad QUICKSTART.md
goto menu

:voskdl
cls
%PY% main.py install-voice-model
echo.
pause
goto menu

:tess
cls
%PY% main.py install-tesseract
echo.
pause
goto menu

:aisetup
cls
%PY% main.py ai-setup
echo.
pause
goto menu

:aitest
cls
set PERS=
set /p PERS=Personality to test (Enter for your default): 
if "%PERS%"=="" (%PY% main.py ai-test) else (%PY% main.py ai-test --personality %PERS%)
echo.
pause
goto menu
