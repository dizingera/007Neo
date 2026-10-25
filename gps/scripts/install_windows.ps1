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

# Der Microsoft-Store-Platzhalter (WindowsApps\python.exe) zählt nicht: er öffnet
# nur den Store. Gesucht wird ein echtes Python ab Version 3.10.
function Finde-Python {
    foreach ($kandidat in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        if ($kandidat.Source -like "*WindowsApps*") { continue }
        & $kandidat.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $kandidat.Source }
    }
    return $null
}

function Pfad-Neu-Lesen {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

$python = Finde-Python
if (-not $python) {
    Write-Host "Python fehlt - wird jetzt installiert (einmalig, braucht Internet)."
    # Erst winget (Windows 10/11), sonst der Installer von python.org.
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            & winget install -e --id Python.Python.3.12 --scope machine --silent `
                --accept-package-agreements --accept-source-agreements
        } catch { Write-Host "   winget gescheitert: $_" }
        Pfad-Neu-Lesen
        $python = Finde-Python
    }
    if (-not $python) {
        $installer = Join-Path $env:TEMP "python-3.12.8-amd64.exe"
        try {
            Invoke-WebRequest "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" `
                -OutFile $installer -UseBasicParsing
            Start-Process $installer -Wait `
                -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0"
        } catch { Write-Host "   Download gescheitert: $_" }
        Pfad-Neu-Lesen
        $python = Finde-Python
    }
    if (-not $python) {
        Write-Host "Python konnte nicht installiert werden. Bitte von python.org installieren" `
                   "(Haken bei 'Add to PATH') und diese Datei noch einmal starten." -ForegroundColor Red
        exit 1
    }
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

corrections:
  # RTK-Korrekturen. Quelle je nach Anlage:
  #   ntrip  - Caster (Dienst, oder eigene Basis mit Caster wie RTKBase)
  #   tcp    - roher RTCM3-Strom von einer eigenen Basis, ohne Anmeldung
  #   serial - Funkmodem an diesem Rechner (z.B. COM4)
  #   aus    - Funkmodem steckt direkt am Empfänger, oder kein RTK
  source: aus
  host: ''
  port: 2101
  mountpoint: ''
  username: ''
  password: ''
  serial_port: ''
  baudrate: 115200
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
# Stürzt das Programm ab (Ausstieg mit Fehlercode), startet Windows die Aufgabe
# nach einer Minute von selbst neu. Ein gewollter Neustart über die Oberfläche
# endet mit Code 0 und löst das nicht aus.
$option  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
             -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
             -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "AgriPilot" -Action $aktion -Trigger $ausloes `
    -Settings $option -Force -RunLevel Highest | Out-Null

$adressen = (Get-NetIPAddress -AddressFamily IPv4 |
             Where-Object { $_.IPAddress -notlike "127.*" }).IPAddress

Schritt "Starten"
# Verknüpfung auf dem Desktop für alle Benutzer: ein Doppelklick öffnet die Anzeige.
try {
    $desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
    Set-Content -Encoding ASCII (Join-Path $desktop "AgriPilot.url") `
        "[InternetShortcut]`r`nURL=http://localhost:$Port`r`n"
    Write-Host "   Verknüpfung 'AgriPilot' auf dem Desktop angelegt."
} catch { Write-Host "   Verknüpfung nicht angelegt: $_" }

Start-ScheduledTask -TaskName "AgriPilot"
Write-Host "   Programm wird gestartet ..."
$bereit = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    try {
        Invoke-WebRequest "http://localhost:$Port/api/system" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $bereit = $true
        break
    } catch { }
}
if ($bereit) {
    Start-Process "http://localhost:$Port"
} else {
    Write-Host "   Es hat sich nach 40 Sekunden nicht gemeldet - Protokoll: $Daten\start.log" -ForegroundColor Yellow
}

Write-Host "`nFertig." -ForegroundColor Green
Write-Host "Anzeige auf diesem Tablet:  http://localhost:$Port  (oder Symbol 'AgriPilot' auf dem Desktop)"
foreach ($adresse in $adressen) {
    Write-Host "Auf dem Android-Tablet:     http://${adresse}:$Port"
}
Write-Host "`nNächster Schritt: Geräte suchen:  `"$pyexe`" `"$Ziel\scripts\scan_devices.py`""
