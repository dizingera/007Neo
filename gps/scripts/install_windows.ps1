# AgriPilot auf einem Windows-Tablet einrichten.
#
# In einer PowerShell **als Administrator** im Ordner gps ausführen:
#
#     powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
#
# Der Aufruf ist wiederholbar: ein zweites Mal aktualisiert das Programm und
# lässt Konfiguration, Felder und aufgezeichnete Arbeiten unangetastet.

param(
    [string]$Ziel   = "C:\AgriPilot",
    [int]   $Port   = 8080,
    # Ordner mit vorab geladenen Python-Paketen (*.whl). Liegt er bei, wird
    # ohne Internet installiert - in einer Maschinenhalle der Normalfall.
    [string]$Pakete = ""
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
    # py.exe kommt mit dem Installer von python.org mit und steht auch dann im
    # Pfad, wenn beim Einrichten der Haken bei "Add to PATH" vergessen wurde -
    # was oft genug passiert.
    $starter = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($starter) { $python = $starter }
}
if (-not $python) {
    Write-Host "Python fehlt." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Von python.org herunterladen und installieren."
    Write-Host "  Beim Installieren den Haken bei 'Add python.exe to PATH' setzen."
    Write-Host "  Danach diese Datei noch einmal doppelklicken."
    Write-Host ""
    Write-Host "  Ohne Internet am Tablet: den Installer am Hofrechner laden und"
    Write-Host "  auf demselben Stick mitbringen."
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

# Ohne Internet: die Pakete liegen als Dateien bei. Mit Internet: der übliche
# Weg. Beides mit denselben Paketnamen, damit später nichts auseinanderläuft.
$treiber = @("phidget22", "tinkerforge")
$anforderungen = Join-Path $Ziel "backend\requirements.txt"
if ($Pakete -and (Test-Path $Pakete)) {
    Write-Host "   ohne Internet, aus $Pakete"
    # Die meisten beiliegenden Pakete passen auf jedes Python. Drei bringen
    # übersetzten Code mit und tragen die Fassung im Namen (cp311 und
    # dergleichen); das Paket bringt sie für mehrere Fassungen mit. Fehlt
    # ausgerechnet die hiesige, lehnt pip ab - mit einer Meldung, die nach
    # einem kaputten Paket aussieht statt nach dem falschen Python. Also
    # vorher nachsehen und es benennen.
    $hier = (& $pyexe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    $marke = "cp" + $hier.Replace(".", "")
    $raeder = @(Get-ChildItem $Pakete -Filter "*cp*-cp*-win_amd64.whl")
    if ($raeder.Count -gt 0 -and -not ($raeder | Where-Object { $_.Name -like "*-$marke-*" })) {
        $dabei = @($raeder | ForEach-Object {
            if ($_.Name -match "-cp(\d)(\d+)-") { "$($Matches[1]).$($Matches[2])" }
        } | Sort-Object -Unique)
        Write-Host ""
        Write-Host "Auf diesem Tablet läuft Python $hier - dafür liegen keine Pakete bei." -ForegroundColor Red
        Write-Host "Dabei sind: Python $($dabei -join ', ')." -ForegroundColor Red
        Write-Host "  Entweder eine dieser Fassungen installieren,"
        Write-Host "  oder das Tablet ins WLAN und ohne die beiliegenden Pakete einrichten,"
        Write-Host "  oder am Hofrechner neu bauen:"
        Write-Host "    python scripts\make_install.py --pakete --python $hier"
        exit 1
    }
    # pip selbst wird hier nicht erneuert - das mitgelieferte reicht, und ein
    # pip-Rad liegt nicht bei. Sonst bräche der Einbau an einer Nebensache ab.
    $fehler = 0
    & $pip install --quiet --no-index --find-links $Pakete -r $anforderungen
    if ($LASTEXITCODE -ne 0) { $fehler = $LASTEXITCODE }
    & $pip install --quiet --no-index --find-links $Pakete @treiber
    if ($LASTEXITCODE -ne 0) { $fehler = $LASTEXITCODE }
} else {
    $fehler = 0
    & $pip install --quiet --upgrade pip
    & $pip install --quiet -r $anforderungen
    if ($LASTEXITCODE -ne 0) { $fehler = $LASTEXITCODE }
    # Treiberbibliotheken für Lenkmotor und Neigungssensor
    & $pip install --quiet @treiber
    if ($LASTEXITCODE -ne 0) { $fehler = $LASTEXITCODE }
}
# Jeder Aufruf einzeln geprüft: sonst verdeckt ein gelungener letzter Schritt,
# dass der davor fehlgeschlagen ist - und das Programm startet später ohne
# eine Bibliothek, die es braucht.
if ($fehler -ne 0) {
    Write-Host ""
    Write-Host "Die Python-Pakete ließen sich nicht installieren." -ForegroundColor Red
    Write-Host "  Mit Internet:  das Tablet ins WLAN und diese Datei erneut starten."
    Write-Host "  Ohne Internet: am Hofrechner 'python scripts\make_install.py --pakete'"
    Write-Host "                 laufen lassen - das Paket bringt die Dateien dann mit."
    exit 1
}

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
Write-Host ""
Write-Host "Für die Kachel auf dem Android-Tablet - und dafür, dass dessen" -ForegroundColor Cyan
Write-Host "Bildschirm im Feld anbleibt - einmal noch:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$Ziel\scripts\make_cert.ps1`""
Write-Host "Der ganze Weg steht in $Ziel\docs\TABLETS.md"
