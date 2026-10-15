# Android-Tablet als Kabinenanzeige

Der Rechner ist der Raspberry Pi: an ihm hängen Empfänger, Neigungssensor und
Lenkausgang, er hält die Datenbank und die RTK-Verbindung. Das Android-Tablet
zeigt nur an und bedient – über den Browser, im selben Netz.

Das ist keine Notlösung, sondern der Aufbau, für den das System gedacht ist. Ein
Tablet, das rechnet, müsste USB-Geräte bedienen; für die Phidget-Motorsteuerung
gibt es unter Android keinen Treiber. Ein Tablet, das anzeigt, braucht nichts als
einen Browser – und kann jederzeit durch ein anderes ersetzt werden, wenn eins
im Feld zu Bruch geht.

## Kurz

1. Auf dem Pi einmal `sudo bash scripts/make_cert.sh`.
2. Die zwei `tls_`-Zeilen in `/etc/agripilot/config.yaml` eintragen
   (oder in der Oberfläche unter **Einstellungen → Anzeige und Daten**),
   dann `systemctl restart agripilot`.
3. Auf dem Tablet `http://<pi>.local:8080/ca.crt` aufrufen und das Zertifikat
   installieren.
4. `https://<pi>.local:8080` öffnen, Chrome-Menü → **App installieren**.

## Warum HTTPS, obwohl nichts ins Internet geht

Drei Dinge gibt der Browser nur in sicherem Kontext her – HTTPS oder localhost.
Über `http://192.168.x.x` fehlen alle drei:

| | ohne HTTPS | mit HTTPS |
|---|---|---|
| Kachel auf dem Startbildschirm | nur eine Verknüpfung | echte Installation, startet im Vollbild |
| Oberfläche zwischengespeichert | nein – weiße Seite, wenn das WLAN wackelt | ja, die Anzeige ist sofort da |
| **Bildschirm bleibt an** | nein – das Tablet wird während der Arbeit dunkel | ja |

Der dritte Punkt ist der, der auf dem Feld zählt. Eine Anzeige, die nach zwei
Minuten ausgeht, ist keine.

Zwischengespeichert wird dabei ausschließlich die Hülle – Seite, Stil, Programm,
Symbole. Position, Fix-Status und Lenkbedingungen kommen immer frisch aus dem
Netz oder gar nicht: eine zwischengespeicherte Antwort auf `/api/state` wäre ein
alter Fix-Status, und darauf soll sich niemand verlassen.

## Warum eine eigene Ausgabestelle

Auf einem Traktor im Feld gibt es kein Let's Encrypt, also eigene Papiere.
Android vertraut einem einzelnen selbst signierten Zertifikat auch dann nicht,
wenn man es installiert – vertraut wird nur einer Stelle, die *ausstellt*.
`make_cert.sh` legt deshalb zwei Papiere an:

* eine kleine Hof-Ausgabestelle (`ca.crt`), die einmal auf jedes Tablet wandert,
* ein davon signiertes Serverzertifikat, das auf dem Pi bleibt.

In das Serverzertifikat werden Hostname, `<hostname>.local` und alle aktuellen
IPv4-Adressen des Pi eingetragen. Der Name im Zertifikat muss zu dem passen, was
im Tablet in der Adresszeile steht – sonst warnt der Browser trotzdem. Bekommt
der Pi später eine andere Adresse, das Skript erneut laufen lassen; die
Ausgabestelle bleibt dabei bestehen, sonst müssten alle Tablets neu eingerichtet
werden.

## Zertifikat auf dem Tablet installieren

1. `http://<pi>.local:8080/ca.crt` aufrufen – die Datei wird heruntergeladen.
   Der Weg geht bewusst über **http**: das Tablet kann der verschlüsselten
   Verbindung noch nicht trauen, es hat die Papiere ja noch nicht. Ausgeliefert
   wird nur der öffentliche Teil; der Schlüssel bleibt auf dem Pi.
2. Einstellungen → Sicherheit → Verschlüsselung und Anmeldedaten →
   Zertifikat installieren → **CA-Zertifikat** → die Datei wählen.
   Android warnt dabei deutlich – das ist richtig so, ein CA-Zertifikat ist
   nichts Beiläufiges. Es gilt nur für diese eine Hof-Ausgabestelle.
3. `https://<pi>.local:8080` öffnen. Kein Schloss-Warnhinweis mehr.

## Kachel anlegen

Chrome-Menü → **App installieren** (erscheint erst, wenn Schritt 2 erledigt ist).
Die Anzeige startet dann im Vollbild, quer, ohne Adresszeile – und hält den
Bildschirm an, solange sie im Vordergrund ist.

Ohne installiertes Zertifikat bleibt „Zum Startbildschirm hinzufügen" als
Verknüpfung möglich. Sie startet ebenfalls im Vollbild, aber ohne
Zwischenspeicher und ohne Bildschirmsperre.

## Mehrere Anzeigen

Es dürfen beliebig viele Tablets gleichzeitig zusehen – jede offene Seite
bekommt denselben Live-Strom über die WebSocket-Verbindung. Praktisch ist das
beim Umfahren der Feldgrenze: das feste Tablet bleibt in der Kabine, das zweite
nimmt man mit.

Was ein zweites Tablet **nicht** kann, ist den Pi ersetzen. Fällt der aus, steht
die Anzeige – und die Lenkung geht auf, weil der Wachhund der Platine nach 500 ms
ohne frischen Befehl den Motor anhält.

## Wenn etwas nicht geht

| Bild | Ursache | Abhilfe |
|---|---|---|
| `<pi>.local` wird nicht gefunden | avahi läuft nicht | `systemctl status avahi-daemon`; ersatzweise die IP verwenden |
| Browser warnt trotz Zertifikat | Adresse steht nicht im Zertifikat | `make_cert.sh` erneut laufen lassen, danach Dienst neu starten |
| „App installieren" fehlt im Menü | kein HTTPS oder kein Service Worker | Adresszeile prüfen: steht dort `https://`? |
| Bildschirm geht trotzdem aus | Sperre nur im Vordergrund | Anzeige nicht wegschalten; Energiesparmodus des Tablets prüfen |
| Anzeige bleibt weiß | Pi nicht erreichbar | `systemctl status agripilot` auf dem Pi |
