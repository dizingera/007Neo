# AgriPilot: Neustart nach Absturz nachrüsten (für Tablets, die vor dieser
# Version eingerichtet wurden). Ändert nur die Einstellungen der vorhandenen
# Aufgabe "AgriPilot" - Programm, Daten und Konfiguration bleiben unberührt.
# Als Administrator ausführen:  powershell -ExecutionPolicy Bypass -File wache_einrichten.ps1
$ErrorActionPreference = "Stop"
$aufgabe = Get-ScheduledTask -TaskName "AgriPilot" -ErrorAction SilentlyContinue
if (-not $aufgabe) {
    Write-Host "Keine Aufgabe 'AgriPilot' gefunden - erst install_windows.ps1 ausführen." -ForegroundColor Red
    exit 1
}
$option = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Set-ScheduledTask -TaskName "AgriPilot" -Settings $option | Out-Null
Write-Host "Fertig: stürzt AgriPilot ab, startet Windows es nach einer Minute neu." -ForegroundColor Green
