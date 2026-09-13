# Zertifikate für die Kabinenanzeige erzeugen - auf einem Windows-Tablet.
#
#     powershell -ExecutionPolicy Bypass -File C:\AgriPilot\scripts\make_cert.ps1
#
# Das Gegenstück zu make_cert.sh, das dasselbe auf einem Raspberry Pi tut.
#
# Warum überhaupt: Ein Android-Tablet als Kabinenanzeige bekommt drei Dinge nur
# über HTTPS - die Kachel auf dem Startbildschirm (echte Installation statt
# Verknüpfung), den Zwischenspeicher für die Oberfläche, und das Wachhalten des
# Bildschirms. Über http://192.168.x.x lässt der Browser all das bewusst nicht
# zu. Das dritte ist das, was auf dem Feld zählt: eine Anzeige, die nach zwei
# Minuten dunkel wird, ist keine.
#
# Warum eine eigene Ausgabestelle und nicht nur ein selbst signiertes
# Serverzertifikat: Android vertraut einem einzelnen selbst signierten Papier
# auch dann nicht, wenn man es installiert - vertraut wird nur einer Stelle,
# die ausstellt. Deshalb zwei Papiere: eine kleine eigene Ausgabestelle (die
# einmal aufs Tablet wandert) und ein davon signiertes Serverzertifikat (das
# auf dem Windows-Tablet bleibt).
#
# Die Namen im Zertifikat müssen zu dem passen, was im Tablet in der Adresszeile
# steht. Eingetragen werden deshalb der Rechnername, <Rechnername>.local,
# localhost und alle aktuellen IPv4-Adressen. Bekommt das Tablet später eine
# andere Adresse, dieses Skript erneut laufen lassen.

param(
    [string]$Ordner = (Join-Path $env:PROGRAMDATA "AgriPilot\tls"),
    [string]$Konfig = (Join-Path $env:PROGRAMDATA "AgriPilot\config.yaml"),
    [int]   $Jahre  = 10
)

$ErrorActionPreference = "Stop"
function Schritt($text) { Write-Host "`n== $text ==" -ForegroundColor Cyan }

if (-not ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Bitte als Administrator ausführen." -ForegroundColor Red
    Write-Host "  (Rechtsklick auf PowerShell -> 'Als Administrator ausführen')"
    exit 1
}

$Name = $env:COMPUTERNAME
New-Item -ItemType Directory -Force -Path $Ordner | Out-Null

# Alle IPv4-Adressen einsammeln, damit auch der direkte Aufruf über die Adresse
# ohne Warnung durchgeht - im Feld ist das der übliche Weg, weil die
# .local-Auflösung nicht auf jedem Tablet zuverlässig ist.
$adressen = @((Get-NetIPAddress -AddressFamily IPv4 |
               Where-Object { $_.IPAddress -notlike "127.*" }).IPAddress)
$namen = @($Name, "$Name.local", "localhost") + $adressen

Schritt "Ausgabestelle (einmalig)"
$caPfad = Join-Path $Ordner "ca.crt"
# Die Ausgabestelle wird im Zertifikatspeicher dieses Rechners gehalten - dort
# liegt auch ihr privater Schlüssel, den Windows nicht als Datei herausgibt.
# Gesucht wird sie am Namen: so überlebt ein zweiter Aufruf die erste Stelle,
# statt eine neue anzulegen. Sonst müssten alle Tablets neu eingerichtet werden.
$ca = Get-ChildItem Cert:\LocalMachine\My |
      Where-Object { $_.Subject -eq "CN=AgriPilot Hof-CA" -and $_.HasPrivateKey } |
      Sort-Object NotAfter -Descending | Select-Object -First 1

if ($ca) {
    Write-Host "   vorhanden, gültig bis $($ca.NotAfter.ToString('dd.MM.yyyy')) - bleibt bestehen."
} else {
    $ca = New-SelfSignedCertificate `
            -Subject "CN=AgriPilot Hof-CA" `
            -KeyUsage CertSign, CRLSign, DigitalSignature `
            -KeyLength 2048 -KeyAlgorithm RSA -HashAlgorithm SHA256 `
            -CertStoreLocation "Cert:\LocalMachine\My" `
            -NotAfter (Get-Date).AddYears($Jahre) `
            -TextExtension @("2.5.29.19={text}CA=true&pathlength=0")
    Write-Host "   neu angelegt."
}
# Die öffentliche Hälfte als Datei - die wandert aufs Android-Tablet.
[IO.File]::WriteAllText($caPfad, @"
-----BEGIN CERTIFICATE-----
$([Convert]::ToBase64String($ca.RawData, 'InsertLineBreaks'))
-----END CERTIFICATE-----
"@)

Schritt "Serverzertifikat"
$server = New-SelfSignedCertificate `
            -Subject "CN=$Name" `
            -DnsName $namen `
            -Signer $ca `
            -KeyLength 2048 -KeyAlgorithm RSA -HashAlgorithm SHA256 `
            -KeyExportPolicy Exportable `
            -CertStoreLocation "Cert:\LocalMachine\My" `
            -NotAfter (Get-Date).AddYears($Jahre) `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.1")

# uvicorn will Dateien, keinen Zertifikatspeicher - also heraus damit, in die
# beiden Dateien, die der Server erwartet.
$crtPfad = Join-Path $Ordner "server.crt"
$keyPfad = Join-Path $Ordner "server.key"
[IO.File]::WriteAllText($crtPfad, @"
-----BEGIN CERTIFICATE-----
$([Convert]::ToBase64String($server.RawData, 'InsertLineBreaks'))
-----END CERTIFICATE-----
"@)

$schluessel = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($server)
if (-not $schluessel) {
    Write-Host "Der private Schlüssel ließ sich nicht lesen." -ForegroundColor Red
    exit 1
}

# Den Schlüssel als PEM schreiben - und zwar so, dass es auf dem Windows
# funktioniert, das auf einem Tablet wirklich läuft.
#
# ExportPkcs8PrivateKey() wäre eine Zeile, gibt es aber erst ab .NET Core.
# Windows bringt PowerShell 5.1 auf .NET Framework mit; dort fehlt die Methode,
# und das Skript stürbe genau dann, wenn es gebraucht wird. Deshalb: wenn es
# die Methode gibt, nehmen wir sie; sonst wird aus den Kennzahlen des
# Schlüssels ein PKCS#1-Block gebaut. Das ist ein Dutzend Zeilen ASN.1, und
# Python nimmt beide Formen an.
if ($schluessel.PSObject.Methods.Name -contains "ExportPkcs8PrivateKey") {
    $pem = "-----BEGIN PRIVATE KEY-----`n" +
           [Convert]::ToBase64String($schluessel.ExportPkcs8PrivateKey(), 'InsertLineBreaks') +
           "`n-----END PRIVATE KEY-----`n"
} else {
    function DerLaenge([int]$n) {
        # Kurze Form bis 127, darüber die Anzahl der Längenbytes voran.
        if ($n -lt 128) { return @([byte]$n) }
        $bytes = @()
        $rest = $n
        while ($rest -gt 0) { $bytes = @([byte]($rest -band 0xFF)) + $bytes; $rest = $rest -shr 8 }
        return @([byte](0x80 -bor $bytes.Count)) + $bytes
    }
    function DerZahl([byte[]]$wert) {
        # ASN.1-INTEGER ist vorzeichenbehaftet: ist das oberste Bit gesetzt,
        # muss eine Null davor - sonst läse ein Leser eine negative Zahl.
        if ($null -eq $wert -or $wert.Count -eq 0) { $wert = @([byte]0) }
        while ($wert.Count -gt 1 -and $wert[0] -eq 0 -and ($wert[1] -band 0x80) -eq 0) {
            $wert = $wert[1..($wert.Count - 1)]
        }
        if (($wert[0] -band 0x80) -ne 0) { $wert = @([byte]0) + $wert }
        return @([byte]0x02) + (DerLaenge $wert.Count) + $wert
    }

    $k = $schluessel.ExportParameters($true)
    $inhalt = (DerZahl @([byte]0)) +
              (DerZahl $k.Modulus)  + (DerZahl $k.Exponent) + (DerZahl $k.D) +
              (DerZahl $k.P)        + (DerZahl $k.Q) +
              (DerZahl $k.DP)       + (DerZahl $k.DQ) + (DerZahl $k.InverseQ)
    $der = @([byte]0x30) + (DerLaenge $inhalt.Count) + $inhalt
    $pem = "-----BEGIN RSA PRIVATE KEY-----`n" +
           [Convert]::ToBase64String([byte[]]$der, 'InsertLineBreaks') +
           "`n-----END RSA PRIVATE KEY-----`n"
}
[IO.File]::WriteAllText($keyPfad, $pem)

# Der Schlüssel ist das Papier, mit dem sich jemand als dieses Tablet ausgeben
# könnte. Also nur für Administratoren und das System lesbar.
$rechte = Get-Acl $keyPfad
$rechte.SetAccessRuleProtection($true, $false)
foreach ($wer in @("BUILTIN\Administrators", "NT AUTHORITY\SYSTEM")) {
    $rechte.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $wer, "FullControl", "Allow")))
}
Set-Acl $keyPfad $rechte

Write-Host "   Namen im Zertifikat: $($namen -join ', ')"

Schritt "Konfiguration eintragen"
if (Test-Path $Konfig) {
    $text = Get-Content $Konfig -Raw
    if ($text -match "(?m)^\s*tls_cert:") {
        Write-Host "   tls_cert steht schon in $Konfig - bitte prüfen:"
        Write-Host "     tls_cert: $crtPfad"
        Write-Host "     tls_key:  $keyPfad"
    } else {
        # Unter "server:" einhängen, mit derselben Einrückung wie die Zeilen
        # darunter. Angehängt ans Dateiende landete es im falschen Abschnitt.
        $neu = $text -replace "(?m)^(server:\s*$)", "`$1`n  tls_cert: '$crtPfad'`n  tls_key: '$keyPfad'"
        if ($neu -eq $text) {
            Write-Host "   Kein Abschnitt 'server:' gefunden. Bitte von Hand eintragen:" -ForegroundColor Yellow
            Write-Host "     server:"
            Write-Host "       tls_cert: '$crtPfad'"
            Write-Host "       tls_key: '$keyPfad'"
        } else {
            Set-Content -Path $Konfig -Value $neu -Encoding UTF8
            Write-Host "   in $Konfig eingetragen."
        }
    }
} else {
    Write-Host "   $Konfig gibt es noch nicht - erst INSTALLIEREN.bat ausführen." -ForegroundColor Yellow
}

$adresse = if ($adressen) { $adressen[0] } else { $Name }

Write-Host "`nFertig." -ForegroundColor Green
Write-Host "AgriPilot neu starten (Aufgabenplanung oder Tablet neu anmelden),"
Write-Host "danach läuft die Oberfläche unter https:// statt http://."
Write-Host ""
Write-Host "Auf dem Android-Tablet EINMAL einrichten:" -ForegroundColor Cyan
Write-Host "  1. http://${adresse}:8080/ca.crt aufrufen und die Datei speichern."
Write-Host "  2. Einstellungen > Sicherheit > Verschlüsselung und Anmeldedaten >"
Write-Host "     Zertifikat installieren > CA-Zertifikat > die Datei wählen."
Write-Host "     (Android warnt dabei - das ist die übliche Warnung für eigene"
Write-Host "      Ausgabestellen und hier richtig so.)"
Write-Host "  3. https://${adresse}:8080 aufrufen - jetzt ohne Warnung."
Write-Host "  4. Chrome-Menü > 'App installieren' legt die Kachel an."
Write-Host ""
Write-Host "Bekommt das Tablet später eine andere Adresse: dieses Skript erneut"
Write-Host "laufen lassen. Das Zertifikat der Ausgabestelle bleibt dabei gültig,"
Write-Host "also muss am Android-Tablet nichts wiederholt werden."
