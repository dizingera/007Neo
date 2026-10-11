@echo off
rem AgriPilot installieren: Doppelklick genuegt. Fragt selbst nach Administratorrechten,
rem installiert bei Bedarf Python und startet das Programm.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Administratorrechte werden angefordert ...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install_windows.ps1"
echo.
pause
