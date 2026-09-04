# AgriPilot auf einem Windows-Tablet einrichten.
#
# In einer PowerShell **als Administrator** im Ordner gps ausführen:
#
#     powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
#
# Der Aufruf ist wiederholbar: ein zweites Mal aktualisiert das Programm und
# lässt Konfiguration, Felder und aufgezeichnete Arbeiten unangetastet.

param(
    [string]$Ziel  = "C:\AgriPilot",
    [int]   $Port  = 8080
)

$ErrorActionPreference = "Stop"
$Daten  = Join-Path $env:PROGRAMDATA "AgriPilot"
$Konfig = Join-Path $Daten "config.yaml"
$Quelle = Split-Path -Parent $PSScriptRoot

function Schritt($text) { Write-Host "`n== $text ==" -ForegroundColor Cyan }

if (-not ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Bitte als Administrator ausführen." -ForegroundColor Red
    exit 1
}

Schritt "Python prüfen"
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Host "Python fehlt. Von python.org installieren (Haken bei 'Add to PATH')." -ForegroundColor Red
    exit 1
}
& $python --version

Schritt "Programm nach $Ziel kopieren"
New-Item -ItemType Directory -Force -Path $Ziel, $Daten | Out-Null
foreach ($ordner in @("backend", "frontend", "scripts")) {
    $pfad = Join-Path $Ziel $ordner
    if (Test-Path $pfad) { Remove-Item -Recurse -Force $pfad }
    Copy-Item -Recurse (Join-Path $Quelle $ordner) $pfad
}
Copy-Item (Join-Path $Quelle "README.md") $Ziel -ErrorAction SilentlyContinue
Copy-Item -Recurse (Join-Path $Quelle "docs") (Join-Path $Ziel "docs") -Force -ErrorAction SilentlyContinue

Schritt "Python-Umgebung"
$venv = Join-Path $Ziel "venv"
if (-not (Test-Path $venv)) { & $python -m venv $venv }
$pip    = Join-Path $venv "Scripts\pip.exe"
$pyexe  = Join-Path $venv "Scripts\python.exe"
& $pip install --quiet --upgrade pip
& $pip install --quiet -r (Join-Path $Ziel "backend\requirements.txt")
# Treiberbibliotheken für Lenkmotor und Neigungssensor
& $pip install --quiet phidget22 tinkerforge

Schritt "Konfiguration"
if (Test-Path $Konfig) {
    Write-Host "   $Konfig ist vorhanden und bleibt unverändert."
} else {
@"
# AgriPilot - Windows-Tablet in der Kabine.
# Die Anschlüsse zuerst mit scan_devices.py ermitteln:
#   C:\AgriPilot\venv\Scripts\python.exe C:\AgriPilot\scripts\scan_devices.py

gnss:
  source: serial
  port: COM3               # aus scan_devices.py übernehmen
  baudrate: 115200
  rtcm_out: auto

imu:
  source: aus              # auf 'tinkerforge' setzen, sobald brickd läuft
  uid: ''                  # leer = erstes gefundenes IMU-Gerät
  axis_map: standard
  roll_sign: 1.0
  terrain_compensation: true

ntrip:
  enabled: false           # RTK-Zugangsdaten hier eintragen
  host: ''
  port: 2101
  mountpoint: ''
  username: ''
  password: ''
  send_gga: true

network:
  role: master
  device_id: $($env:COMPUTERNAME)
  device_name: $($env:COMPUTERNAME)
  rtcm_relay_port: 2102

steering:
  enabled: false           # NUR mit Lenkmotor, Not-Aus und geprüftem Einbau
  output: phidget
  require_rtk: true
  min_speed_ms: 0.3
  max_speed_ms: 8.0
  max_cross_track_m: 1.5

phidget:
  serial_number: -1        # -1 = erstes gefundenes Gerät
  motor_channel: 0
  feedback: yaw_rate       # was | yaw_rate | encoder
  current_limit_a: 2.0     # niedrig: das Lenkrad muss von Hand zu übersteuern sein
  max_duty: 0.55
  failsafe_ms: 500

server:
  host: 0.0.0.0            # 0.0.0.0, damit das Android-Tablet mitschauen kann
  port: $Port
  data_dir: '$Daten'          # in Anführungszeichen: YAML lässt Backslashes sonst stehen, wie sie sind
"@ | Set-Content -Encoding UTF8 $Konfig
    Write-Host "   $Konfig angelegt."
}

Schritt "Firewall für das Android-Tablet"
$regel = "AgriPilot $Port"
if (-not (Get-NetFirewallRule -DisplayName $regel -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $regel -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
    Write-Host "   Port $Port freigegeben."
} else {
    Write-Host "   Freigabe war schon vorhanden."
}

Schritt "Automatischer Start"
$start = Join-Path $Ziel "start.bat"
@"
@echo off
set AGRIPILOT_CONFIG=$Konfig
cd /d "$Ziel\backend"
"$pyexe" -m agripilot.server
"@ | Set-Content -Encoding ASCII $start

# Als Aufgabe beim Anmelden - überlebt einen Neustart des Tablets, ohne dass
# jemand daran denken muss.
$aktion  = New-ScheduledTaskAction -Execute $start
$ausloes = New-ScheduledTaskTrigger -AtLogOn
$option  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
             -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "AgriPilot" -Action $aktion -Trigger $ausloes `
    -Settings $option -Force -RunLevel Highest | Out-Null

$adressen = (Get-NetIPAddress -AddressFamily IPv4 |
             Where-Object { $_.IPAddress -notlike "127.*" }).IPAddress

Write-Host "`nFertig." -ForegroundColor Green
Write-Host "Anzeige auf diesem Tablet:  http://localhost:$Port"
foreach ($adresse in $adressen) {
    Write-Host "Auf dem Android-Tablet:     http://${adresse}:$Port"
}
Write-Host "`nJetzt starten:  $start"
Write-Host "Geräte suchen:  `"$pyexe`" `"$Ziel\scripts\scan_devices.py`""
