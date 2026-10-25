# Zweite Kopie der AgriPilot-Sicherungen vom Pi-Master auf den PC holen.
#
# WARUM: Der Master laeuft auf dem Pi und sichert taeglich nach /var/lib/agripilot/sicherungen
# (SD-Karte). Die SSD des Pi faellt immer wieder aus und taugt nicht als zweiter Ort. Also holt
# der PC die Sicherungen ab - der PC holt, der Pi schickt nicht (der PC ist nicht immer an).
#
# WIE: Jede Sicherung, die hier noch fehlt, kommt per "ssh sudo tar | tar.exe" (Binaerdaten
# durch cmd, nie durch eine PowerShell-Umleitung - die macht aus Bytes Text) und wird per
# SHA-256 gegen den Pi geprueft. Behalten werden die neuesten $Behalten. Fehler -> Protokoll,
# status.json und Exit 1.
#
# Einrichten (einmal, als Andy, ohne Adminrechte):
#   powershell -ExecutionPolicy Bypass -File pi_sicherung_holen.ps1 -Einrichten
# legt die Aufgabe "AgriPilotPiSicherung" an: taeglich 12:30, nachgeholt, wenn der PC aus war.
# Nur ASCII (Windows PowerShell 5.1 liest UTF-8 ohne BOM als ANSI).

param([switch]$Einrichten, [string]$Wurzel = "J:\AgriPilot\Pi-Master-Sicherungen", [int]$Behalten = 14)

$Schluessel = Join-Path $env:USERPROFILE ".ssh\myterra_pi"
$PiAdressen = "192.168.0.126", "192.168.0.139"      # LAN-Kabel, WLAN
$PiOrdner = "/var/lib/agripilot/sicherungen"
$Muster = '^agripilot-\d{8}-\d{6}\.zip$'

function Protokoll([string]$text) {
    try {
        New-Item -ItemType Directory -Force -Path $Wurzel | Out-Null
        Add-Content -LiteralPath (Join-Path $Wurzel "sicherung.log") -Value ("{0}  {1}" -f (Get-Date).ToString("dd.MM.yyyy HH:mm:ss"), $text) -Encoding UTF8
    } catch { }
}

function Status([bool]$ok, [string]$meldung) {
    try {
        $o = [ordered]@{ zeit = (Get-Date).ToString("s"); ok = $ok; meldung = $meldung }
        ($o | ConvertTo-Json) | Set-Content -LiteralPath (Join-Path $Wurzel "status.json") -Encoding UTF8
    } catch { }
}

function Aufruf([string]$exe, [string]$argumente, [int]$sekunden) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $argumente
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $aus = $p.StandardOutput.ReadToEndAsync()
    $fehl = $p.StandardError.ReadToEndAsync()
    $fertig = $p.WaitForExit($sekunden * 1000)
    if (-not $fertig) { & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null }
    [pscustomobject]@{ Exit = $(if ($fertig) { $p.ExitCode } else { -1 }); Aus = $aus.Result; Fehl = $fehl.Result }
}

function Pi-Adresse() {
    foreach ($h in $PiAdressen) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            if ($c.ConnectAsync($h, 22).Wait(3000) -and $c.Connected) { $c.Close(); return $h }
            $c.Close()
        } catch { }
    }
    return $null
}

function Ssh-Argumente([string]$pi) {
    '-i "{0}" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 pi@{1}' -f $Schluessel, $pi
}

if ($Einrichten) {
    # conhost --headless: kein Fenster, das einmal am Tag aufblitzt (Windows 11).
    $start = '--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
    $aktion = New-ScheduledTaskAction -Execute "conhost.exe" -Argument $start
    $zeit = New-ScheduledTaskTrigger -Daily -At "12:30"
    $regeln = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName "AgriPilotPiSicherung" -Action $aktion -Trigger $zeit -Settings $regeln -Description "Zweite Kopie der AgriPilot-Sicherungen vom Pi-Master nach $Wurzel" -Force | Out-Null
    Write-Output "Aufgabe AgriPilotPiSicherung eingerichtet (taeglich 12:30, nachgeholt)."
    exit 0
}

try {
    New-Item -ItemType Directory -Force -Path $Wurzel | Out-Null
    $pi = Pi-Adresse
    if (-not $pi) { throw "Pi unter keiner Adresse erreichbar ($($PiAdressen -join ', '), Port 22)" }

    # Liste mit Pruefsummen - auf dem Pi gerechnet, der Datenordner ist nur fuer den Dienst lesbar.
    $liste = Aufruf "ssh.exe" ('{0} "sudo -n sh -c ''cd {1} && sha256sum agripilot-*.zip''"' -f (Ssh-Argumente $pi), $PiOrdner) 120
    if ($liste.Exit -ne 0) { throw "Liste vom Pi: $($liste.Fehl.Trim())" }
    $fern = @{}
    foreach ($zeile in ($liste.Aus -split "`n")) {
        $teile = $zeile.Trim() -split '\s+', 2
        if ($teile.Count -eq 2 -and $teile[1] -match $Muster) { $fern[$teile[1]] = $teile[0].ToLower() }
    }
    if ($fern.Count -eq 0) { throw "Auf dem Pi liegt keine Sicherung in $PiOrdner" }

    $neu = 0
    foreach ($name in ($fern.Keys | Sort-Object)) {
        $ziel = Join-Path $Wurzel $name
        if ((Test-Path -LiteralPath $ziel) -and ((Get-FileHash -LiteralPath $ziel -Algorithm SHA256).Hash.ToLower() -eq $fern[$name])) { continue }
        $teil = Join-Path $Wurzel ".teil"
        if (Test-Path -LiteralPath $teil) { Remove-Item -LiteralPath $teil -Recurse -Force }
        New-Item -ItemType Directory -Path $teil | Out-Null
        $cmd = 'ssh.exe {0} "sudo -n tar -C {1} -cf - {2}" | tar.exe -xf - -C "{3}"' -f (Ssh-Argumente $pi), $PiOrdner, $name, $teil
        $t = Aufruf "cmd.exe" ('/c "{0}"' -f $cmd) 900
        $geholt = Join-Path $teil $name
        if ($t.Exit -ne 0 -or -not (Test-Path -LiteralPath $geholt)) { throw "Holen von $name fehlgeschlagen: $($t.Fehl.Trim())" }
        $ist = (Get-FileHash -LiteralPath $geholt -Algorithm SHA256).Hash.ToLower()
        if ($ist -ne $fern[$name]) { throw "$($name): Pruefsumme falsch ($ist statt $($fern[$name]))" }
        Move-Item -LiteralPath $geholt -Destination $ziel -Force
        Remove-Item -LiteralPath $teil -Recurse -Force
        $neu++
    }

    # Nur die neuesten behalten - aeltere loescht der Pi ohnehin.
    $alle = Get-ChildItem -LiteralPath $Wurzel -File | Where-Object { $_.Name -match $Muster } | Sort-Object Name -Descending
    $alle | Select-Object -Skip $Behalten | Remove-Item -Force
    $neueste = ($alle | Select-Object -First 1).Name
    $text = "ok: $neu neu geholt (SHA-256 geprueft), neueste $neueste, behalten $([math]::Min($alle.Count, $Behalten)), Pi $pi"
    Protokoll $text
    Status $true $text
    exit 0
} catch {
    Protokoll ("FEHLER: " + $_.Exception.Message)
    Status $false $_.Exception.Message
    exit 1
}
