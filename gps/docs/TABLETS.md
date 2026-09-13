# Auf die Tablets bringen

Für genau deine Anlage: **Windows-Tablet rechnet, Android-Tablet zeigt an.**
Der ausführliche Aufbau mit Verkabelung steht in
[DEINE_ANLAGE.md](DEINE_ANLAGE.md); hier geht es nur darum, das Programm auf
die Geräte zu bekommen.

Installiert wird **nur auf dem Windows-Tablet**. Das Android-Tablet bekommt
nichts – es zeigt über den Browser an. Das ist keine Notlösung: für die
Phidget-Motorsteuerung gibt es unter Android keinen Treiber, und ein Tablet,
das nur anzeigt, lässt sich ersetzen, wenn eins im Feld zu Bruch geht.

## Das Paket bauen

Am Hofrechner, im Ordner `gps`:

```bash
python3 scripts/make_install.py -o /pfad/zum/stick
```

Heraus kommt `agripilot-installation-<version>-<commit>.zip`, rund 1,5 MB.

**Wenn das Tablet in der Halle kein WLAN hat**, die Python-Bibliotheken gleich
mitnehmen:

```bash
python3 scripts/make_install.py --pakete -o /pfad/zum/stick
```

Dann sind es rund 15 MB, und der Einbau läuft ohne Internet. Die Pakete liegen
für **Python 3.11 bis 3.14** bei – eine dieser Fassungen muss auf dem Tablet
sein, welche ist gleich. Der Installer sieht nach und sagt es, statt mit einer
unverständlichen pip-Meldung abzubrechen.

| | ohne `--pakete` | mit `--pakete` |
|---|---|---|
| Größe | ~1,5 MB | ~15 MB |
| Tablet braucht WLAN | ja, einmal beim Einbau | nein |
| Python-Fassung | beliebig ab 3.9 | 3.11 bis 3.14 |

Ist auf dem Tablet eine Fassung, die nicht dabei ist, sagt der Installer, für
welche die Pakete gelten. Dann entweder eine davon nachinstallieren, oder das
Paket am Hofrechner passend bauen:

```bash
python3 scripts/make_install.py --pakete --python 3.15 -o /pfad/zum/stick
```

## Windows-Tablet einrichten

1. **Python**, falls noch nicht da: von python.org holen, beim Installieren
   den Haken bei *Add python.exe to PATH* setzen. Ohne Internet am Tablet den
   Installer am Hofrechner laden und auf denselben Stick legen.

2. **Zip entpacken** – vollständig, nicht nur hineinschauen. Windows öffnet
   Zip-Dateien wie Ordner; daraus zu starten geht schief, weil die Dateien
   dann gar nicht auf der Platte liegen.

3. **`INSTALLIEREN.bat` doppelklicken.** Windows fragt nach
   Administratorrechten – die braucht es für die Firewall-Freigabe und den
   automatischen Start.

Der Aufruf ist wiederholbar. Ein zweites Mal aktualisiert das Programm und
lässt Konfiguration, Felder und aufgezeichnete Arbeiten unangetastet.

Am Ende stehen zwei Adressen im Fenster: eine für dieses Tablet
(`http://localhost:8080`) und eine für das Android-Tablet
(`http://<adresse>:8080`). Ab jetzt startet AgriPilot bei jeder Anmeldung von
selbst.

## Wie das Programm gestartet wird

AgriPilot hat kein eigenes Fenster – es ist ein Server, die Anzeige läuft im
Browser. Zwei Wege führen hin, und der erste braucht gar nichts:

1. **Von selbst.** Der Installer trägt einen Start beim Anmelden ein. Nach dem
   Einschalten des Tablets läuft AgriPilot also schon.
2. **Symbol „AgriPilot" antippen**, auf dem Desktop und im Startmenü. Es sieht
   nach, ob der Server läuft, startet ihn sonst, wartet auf ihn und öffnet die
   Anzeige. Läuft er schon, blitzt nur kurz ein Fenster auf.

Von Hand geht es auch: `C:\AgriPilot\start.bat` starten und im Browser
`http://localhost:8080` aufrufen. Das ist der Weg, wenn etwas klemmt – dort
stehen die Meldungen.

**Danach:** unter Menü → System → *Geräte suchen* findet das Programm
Empfänger, Neigungssensor und Lenksteuerung und trägt die Anschlüsse ein. Die
Inbetriebnahme Schritt für Schritt steht unter Menü → Einbau.

## Android-Tablet: die Kachel

Das Android-Tablet kann die Oberfläche sofort über `http://<adresse>:8080`
anzeigen. Für den Dauerbetrieb in der Kabine fehlt so aber das Wichtigste:

| | über `http://` | über `https://` |
|---|---|---|
| Kachel auf dem Startbildschirm | nur eine Verknüpfung | echte Installation, Vollbild |
| Oberfläche zwischengespeichert | nein – weiße Seite, wenn das WLAN wackelt | ja |
| **Bildschirm bleibt an** | nein – das Tablet wird während der Arbeit dunkel | ja |

Der dritte Punkt ist der, der auf dem Feld zählt. Eine Anzeige, die nach zwei
Minuten ausgeht, ist keine. Der Browser gibt diese drei Dinge nur in sicherem
Kontext her, und auf einem Traktor gibt es kein Let's Encrypt – also eigene
Papiere.

**Einmal auf dem Windows-Tablet**, in einer PowerShell als Administrator:

```powershell
powershell -ExecutionPolicy Bypass -File C:\AgriPilot\scripts\make_cert.ps1
```

Das legt eine kleine eigene Ausgabestelle an, stellt damit ein Zertifikat für
dieses Tablet aus, trägt beides in die Konfiguration ein und sagt zum Schluss,
wie es auf dem Android-Tablet weitergeht. AgriPilot danach neu starten (Tablet
neu anmelden genügt).

**Dann auf dem Android-Tablet**, einmalig:

1. `http://<adresse>:8080/ca.crt` aufrufen und die Datei speichern.
2. Einstellungen → Sicherheit → Verschlüsselung und Anmeldedaten →
   Zertifikat installieren → **CA-Zertifikat** → die Datei wählen.
   Android warnt dabei – das ist die übliche Warnung für eigene
   Ausgabestellen und hier richtig so.
3. `https://<adresse>:8080` aufrufen – jetzt ohne Warnung.
4. Chrome-Menü → **App installieren**. Die Kachel liegt auf dem
   Startbildschirm und startet im Vollbild.

Bekommt das Windows-Tablet später eine andere Adresse im Netz, `make_cert.ps1`
erneut laufen lassen. Die Ausgabestelle bleibt dabei bestehen, also ist am
Android-Tablet nichts zu wiederholen.

## Später aktualisieren

Für ein Tablet, auf dem AgriPilot schon läuft, braucht es kein neues
Installationspaket:

```bash
python3 scripts/make_update.py -o /pfad/zum/stick
```

Das Paket wird im Programm unter **Menü → System → Aktualisierung**
eingespielt, mit Prüfwert je Datei; der alte Stand wird gesichert und lässt
sich mit einem Druck zurückholen. Kein Terminal, keine Administratorrechte.

Das Installationspaket aus diesem Dokument ist für den ersten Einbau da – und
für den Fall, dass ein Tablet ersetzt wird.

## Warum kein fertiges .exe

Ein gebündeltes Programm müsste den Python-Unterbau, die Treiber für Phidget
und Tinkerforge und die Weboberfläche in eine Datei pressen – und jedes Mal
neu, wenn sich eine Kleinigkeit ändert. Auf dem Tablet liegt stattdessen ein
gewöhnlicher Python-Ordner unter `C:\AgriPilot`: nachvollziehbar, einzeln
austauschbar, und im Fehlerfall kann man hineinsehen. Der Preis ist der
einmalige Python-Installer aus Schritt 1.

## Wenn etwas klemmt

| Meldung | Was zu tun ist |
|---|---|
| „Python fehlt" | Schritt 1 nachholen, dann erneut doppelklicken. |
| Pakete laden nicht | Tablet ins WLAN, oder das Paket mit `--pakete` neu bauen. |
| „dafür liegen keine Pakete bei" | Eine der genannten Python-Fassungen installieren, oder ohne `--pakete` mit WLAN einrichten, oder das Paket mit `--python <Fassung>` neu bauen. |
| „kann nicht entfernt werden … von einem anderen Prozess verwendet" | AgriPilot läuft noch und hält den Ordner offen. Der Installer hält es selbst an; bleibt die Meldung, das schwarze AgriPilot-Fenster schließen oder das Tablet neu starten. |
| Android-Tablet sieht nichts | Beide im selben WLAN? Die Firewall-Regel legt der Installer an – sie heißt „AgriPilot 8080". |
| Kein Empfänger | `C:\AgriPilot\venv\Scripts\python.exe C:\AgriPilot\scripts\scan_devices.py` zeigt, was angeschlossen ist. |
| „Kein CA-Zertifikat vorhanden" | `make_cert.ps1` wurde noch nicht ausgeführt. |
