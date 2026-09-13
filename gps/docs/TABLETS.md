# Auf die Tablets bringen

Für genau deine Anlage: **Windows-Tablet rechnet, Android-Tablet zeigt an.**
Der ausführliche Aufbau mit Verkabelung steht in
[DEINE_ANLAGE.md](DEINE_ANLAGE.md); hier geht es nur darum, das Programm auf
die Geräte zu bekommen.

Installiert wird **nur auf dem Windows-Tablet**. Das Android-Tablet bekommt
nichts – es zeigt über den Browser an. Das ist keine Notlösung: für die
Phidget-Motorsteuerung gibt es unter Android keinen Treiber, und ein Tablet,
das nur anzeigt, lässt sich ersetzen, wenn eins im Feld zu Bruch geht.

## Den Installer bauen

Am Hofrechner, im Ordner `gps`:

```bash
python3 scripts/make_exe.py -o /pfad/zum/stick
```

Heraus kommt **`AgriPilot-Setup-<version>-<commit>.exe`**, rund 15 MB. Eine
Datei, zum Doppelklicken – mit Willkommensseite, Fortschritt, Eintrag in
*Apps & Features* und Deinstallierer. Alle Python-Bibliotheken sind darin,
für Python 3.11 bis 3.14; das Tablet braucht dafür kein WLAN.

Gebaut wird mit [NSIS](https://nsis.sourceforge.io/), das auch unter Linux
läuft (`apt-get install nsis`). Ein Windows-Rechner zum Bauen ist also nicht
nötig.

**Python bringt der Installer nicht mit.** Er sucht es, und wenn keines da
ist, sagt er das in einem Fenster und bietet an, python.org zu öffnen. Für
ein Tablet ohne WLAN: den Python-Installer am Hofrechner laden und **neben
die Setup-Datei** auf den Stick legen – dann wird er still mit installiert.

### Der alte Weg: ein Zip

Es gibt weiterhin ein Zip-Paket, für den Fall, dass jemand hineinsehen oder
die Einrichtung von Hand fahren will:

```bash
python3 scripts/make_install.py --pakete -o /pfad/zum/stick
```

Darin liegt `INSTALLIEREN.bat`, die dasselbe tut wie die .exe – nur eben nach
dem Entpacken und mit einer Rückfrage nach Administratorrechten.

| | `AgriPilot-Setup.exe` | Zip mit `INSTALLIEREN.bat` |
|---|---|---|
| Schritte auf dem Tablet | doppelklicken | entpacken, Datei finden, doppelklicken |
| Deinstallieren über Windows | ja | nein |
| Python-Fassung | 3.11 bis 3.14 | 3.11 bis 3.14 |
| Hineinsehen möglich | nein | ja |

Liegt auf dem Tablet eine Python-Fassung, die nicht dabei ist, sagt der
Installer, für welche die Bibliotheken gelten. Dann entweder eine davon
nachinstallieren, oder passend neu bauen:

```bash
python3 scripts/make_exe.py --python 3.15 -o /pfad/zum/stick
```

## Windows-Tablet einrichten

**`AgriPilot-Setup.exe` doppelklicken.** Windows fragt nach
Administratorrechten – die braucht es für die Firewall-Freigabe und den
automatischen Start. Dann durch die Seiten klicken; die Vorschläge passen.

Der Aufruf ist wiederholbar. Ein zweites Mal aktualisiert das Programm und
lässt Konfiguration, Felder und aufgezeichnete Arbeiten unangetastet. Läuft
AgriPilot gerade, wird es dafür kurz angehalten und danach wieder gestartet.

Am Ende stehen zwei Adressen im Fenster: eine für dieses Tablet
(`http://localhost:8080`) und eine für das Android-Tablet
(`http://<adresse>:8080`). AgriPilot läuft ab sofort und startet bei jeder
Anmeldung von selbst – ohne Fenster, im Hintergrund. Was der Server dabei
sagt, steht in `C:\ProgramData\AgriPilot\start.log`.

**Danach:** unter Menü → System → *Geräte suchen* findet das Programm
Empfänger, Neigungssensor und Lenksteuerung und trägt die Anschlüsse ein. Die
Inbetriebnahme Schritt für Schritt steht unter Menü → Einbau.

### Wieder entfernen

Einstellungen → Apps → **AgriPilot** → Deinstallieren. Das hält das Programm
an, trägt den automatischen Start aus, nimmt die Firewall-Freigabe zurück und
räumt den Ordner weg.

Felder, Grenzen, Konfiguration und aufgezeichnete Arbeiten bleiben unter
`C:\ProgramData\AgriPilot` liegen – mit Absicht: das ist die Arbeit von
Jahren, und Deinstallieren ist oft nur der erste Schritt einer
Neuinstallation.

## Wie das Programm gestartet wird

AgriPilot hat kein eigenes Fenster – es ist ein Server, die Anzeige läuft im
Browser. Zwei Wege führen hin, und der erste braucht gar nichts:

1. **Von selbst.** Der Installer trägt einen Start beim Anmelden ein, ohne
   Fenster. Nach dem Einschalten des Tablets läuft AgriPilot also schon.
2. **Symbol „AgriPilot" antippen**, auf dem Desktop und im Startmenü. Es sieht
   nach, ob der Server läuft, startet ihn sonst, wartet auf ihn und öffnet die
   Anzeige. Läuft er schon, blitzt nur kurz ein Fenster auf.

Von Hand geht es auch: `C:\AgriPilot\start.bat` starten und im Browser
`http://localhost:8080` aufrufen. Das ist der Weg, wenn etwas klemmt – dann
stehen die Meldungen im Fenster statt in `start.log`.

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

## Warum Python trotzdem sichtbar bleibt

Der Installer ist eine Datei, aber er presst nicht alles in eine. Auf dem
Tablet landet ein gewöhnlicher Python-Ordner unter `C:\AgriPilot`:
nachvollziehbar, einzeln austauschbar, und im Fehlerfall kann man hineinsehen.
Ein gebündeltes Programm müsste den Python-Unterbau, die Treiber für Phidget
und Tinkerforge und die Weboberfläche zusammenschmelzen – und jedes Mal neu,
wenn sich eine Kleinigkeit ändert. Ein Update wäre dann kein 1,5-MB-Paket
mehr, sondern wieder alles.

Der Preis ist der einmalige Python-Installer. Er kommt nicht mit, weil er von
python.org stammt und dort auch herkommen soll – der Installer sagt es und
öffnet die Seite, oder er führt ihn still aus, wenn er neben der Setup-Datei
liegt.

## Wenn etwas klemmt

| Meldung | Was zu tun ist |
|---|---|
| „kein Python installiert" | Von python.org holen (der Installer bietet an, die Seite zu öffnen), Haken bei *Add python.exe to PATH*, dann die Setup-Datei erneut. Ohne WLAN am Tablet: den Python-Installer daneben auf den Stick legen. |
| Pakete laden nicht | Tablet ins WLAN, oder die .exe neu bauen – dort sind sie immer dabei. |
| „dafür liegen keine Pakete bei" | Eine der genannten Python-Fassungen installieren, oder ohne `--pakete` mit WLAN einrichten, oder das Paket mit `--python <Fassung>` neu bauen. |
| „kann nicht entfernt werden … von einem anderen Prozess verwendet" | AgriPilot läuft noch und hält den Ordner offen. Der Installer hält es selbst an; bleibt die Meldung, das schwarze AgriPilot-Fenster schließen oder das Tablet neu starten. |
| Android-Tablet sieht nichts | Beide im selben WLAN? Die Firewall-Regel legt der Installer an – sie heißt „AgriPilot 8080". |
| Kein Empfänger | `C:\AgriPilot\venv\Scripts\python.exe C:\AgriPilot\scripts\scan_devices.py` zeigt, was angeschlossen ist. |
| „Kein CA-Zertifikat vorhanden" | `make_cert.ps1` wurde noch nicht ausgeführt. |
