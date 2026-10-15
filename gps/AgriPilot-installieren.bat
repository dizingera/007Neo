@echo off
rem AgriPilot installieren: Doppelklick genuegt. Fragt selbst nach Administratorrechten,
rem installiert bei Bedarf Python und startet das Programm.
rem Alles, was auf dem Bildschirm steht, landet auch in install-log.txt neben dieser Datei.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Administratorrechte werden angefordert ...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
if not exist "scripts\install_windows.ps1" (
    echo FEHLER: scripts\install_windows.ps1 nicht gefunden.
    echo Die Zip-Datei muss zuerst ENTPACKT werden - nicht aus der Zip heraus starten.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { try { & '.\scripts\install_windows.ps1' *>&1 | Tee-Object -FilePath '.\install-log.txt' } catch { $_ | Out-String | Tee-Object -FilePath '.\install-log.txt' -Append; exit 1 } }"
echo.
echo Ende. Bei einem Fehler bitte die Datei install-log.txt (im selben Ordner) schicken.
pause
