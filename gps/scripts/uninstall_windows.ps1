# AgriPilot von einem Windows-Tablet entfernen.
#
# Wird vom Deinstallieren in "Apps & Features" aufgerufen, lässt sich aber
# auch von Hand starten:
#
#     powershell -ExecutionPolicy Bypass -File C:\AgriPilot\scripts\uninstall_windows.ps1
#
# Was dieses Skript NICHT anfasst: die Daten unter ProgramData\AgriPilot -
# Felder, Grenzen, aufgezeichnete Arbeiten, Konfiguration. Das ist die Arbeit
# von Jahren, und ein Deinstallieren ist oft nur der erste Schritt einer
# Neuinstallation. Wer sie wirklich loswerden will, löscht den Ordner selbst.
#
# Die Programmdateien löscht der Aufrufer (der Deinstallierer), nicht dieses
# Skript - es kann sich nicht selbst unter den Füßen wegziehen.

param(
    [string]$Ziel = "C:\AgriPilot"
)

# Nicht "Stop": beim Aufräumen ist jeder Schritt für sich zu haben. Fehlt
# eine Firewall-Regel schon, ist das kein Grund, die Verknüpfung stehen zu
# lassen.
$ErrorActionPreference = "Continue"

function Schritt($text) { Write-Host "== $text ==" }

Schritt "AgriPilot anhalten"
if (Get-ScheduledTask -TaskName "AgriPilot" -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName "AgriPilot" -ErrorAction SilentlyContinue
}
$laeufer = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -in @("python.exe", "pythonw.exe") -and
                            ($_.ExecutablePath -like "$Ziel\*" -or
                             $_.CommandLine -like "*agripilot.server*") })
foreach ($laufend in $laeufer) {
    Stop-Process -Id $laufend.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "   $($laeufer.Count) laufende(s) Programm(e) beendet."

Schritt "Automatischen Start austragen"
if (Get-ScheduledTask -TaskName "AgriPilot" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "AgriPilot" -Confirm:$false `
        -ErrorAction SilentlyContinue
    Write-Host "   Aufgabe entfernt."
} else {
    Write-Host "   War nicht eingetragen."
}

Schritt "Firewall-Freigabe zurücknehmen"
# Der Port steht im Namen ("AgriPilot 8080"), und er kann bei der Einrichtung
# ein anderer gewesen sein. Also über das Muster, nicht über eine feste Zahl.
$regeln = @(Get-NetFirewallRule -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like "AgriPilot *" })
foreach ($regel in $regeln) {
    Remove-NetFirewallRule -Name $regel.Name -ErrorAction SilentlyContinue
}
Write-Host "   $($regeln.Count) Regel(n) entfernt."

Schritt "Verknüpfungen entfernen"
$weg = 0
foreach ($ort in @([Environment]::GetFolderPath("CommonDesktopDirectory"),
                   [Environment]::GetFolderPath("DesktopDirectory"),
                   (Join-Path $env:PROGRAMDATA "Microsoft\Windows\Start Menu\Programs"))) {
    $pfad = Join-Path $ort "AgriPilot.lnk"
    if (Test-Path $pfad) { Remove-Item $pfad -Force -ErrorAction SilentlyContinue; $weg++ }
}
Write-Host "   $weg Verknüpfung(en) entfernt."

Write-Host ""
Write-Host "Fertig. Die Daten unter $env:PROGRAMDATA\AgriPilot bleiben liegen."
exit 0
