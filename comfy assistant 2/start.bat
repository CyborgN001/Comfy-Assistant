
@echo off
title ComfyUI Assistant V3.6
color 0B

cd /d "%~dp0"

echo.
echo =======================================================
echo          COMFYUI ASSISTANT V3.6
echo =======================================================
echo.

echo Pruefe Python...
py --version

if errorlevel 1 (
    echo.
    echo FEHLER: Python wurde nicht gefunden.
    pause
    exit /b 1
)

echo.
echo Installiere benoetigte Pakete...
py -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo FEHLER bei der Installation.
    pause
    exit /b 1
)

echo.
echo Starte ComfyUI Assistant...
echo.
echo Browser: http://127.0.0.1:5000
echo ComfyUI: http://127.0.0.1:8188
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5000"
py app.py

echo.
echo ComfyUI Assistant wurde beendet.
pause