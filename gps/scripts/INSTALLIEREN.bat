@echo off
setlocal
rem AgriPilot auf diesem Windows-Tablet einrichten.
rem
rem Diese Datei ist zum Doppelklicken da. Sie holt sich selbst die
rem Administratorrechte und ruft dann install_windows.ps1 auf - damit niemand
rem in der Kabine einen PowerShell-Befehl abtippen muss.
rem
rem Der Aufruf ist wiederholbar: ein zweites Mal aktualisiert das Programm und
rem laesst Konfiguration, Felder und aufgezeichnete Arbeiten unangetastet.

title AgriPilot einrichten

rem Ohne Administratorrechte laesst sich weder die Firewall oeffnen noch der
rem automatische Start eintragen. Also einmal neu starten - mit Rechten.
net session >nul 2>&1
if errorlevel 1 (
    echo Administratorrechte werden angefordert ...
    rem Mitgegebene Angaben (etwa -Port 8081) muessen den Neustart ueberleben.
    if "%*"=="" (
        powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    ) else (
        powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%*'"
    )
    exit /b
)

cd /d "%~dp0"

echo.
echo ===============================================
echo   AgriPilot - Einrichtung auf diesem Tablet
echo ===============================================
echo.

rem Diese Datei liegt im entpackten Paket ganz oben, im Quelltext dagegen in
rem scripts\. Beide Lagen sollen funktionieren, damit es nur eine Datei gibt.
set SKRIPT=%~dp0install_windows.ps1
set WURZEL=%~dp0..
if not exist "%SKRIPT%" (
    set SKRIPT=%~dp0scripts\install_windows.ps1
    set WURZEL=%~dp0
)
if not exist "%SKRIPT%" (
    echo install_windows.ps1 nicht gefunden.
    echo Das Paket ist unvollstaendig entpackt - bitte die Zip-Datei komplett
    echo entpacken, nicht nur einzelne Dateien daraus oeffnen.
    pause
    exit /b 1
)

rem Python-Pakete liegen bei, wenn das Paket mit --pakete gebaut wurde. Dann
rem laeuft der Einbau ohne Internet - in der Maschinenhalle der Normalfall.
set OFFLINE=
if exist "%WURZEL%\pakete" set OFFLINE=-Pakete "%WURZEL%\pakete"

powershell -NoProfile -ExecutionPolicy Bypass -File "%SKRIPT%" %OFFLINE% %*
set FEHLER=%errorlevel%

echo.
if %FEHLER% neq 0 (
    echo Die Einrichtung ist mit Fehler %FEHLER% abgebrochen.
    echo Die Meldung darueber sagt, woran es lag.
) else (
    echo Fertig. Die Adressen fuer das Android-Tablet stehen oben.
)
echo.
echo Fenster schliesst sich nicht von selbst - damit die Meldungen lesbar bleiben.
pause
endlocal
