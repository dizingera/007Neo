# Deine Anlage: F9P, Phidget-Motor, IMU Brick, Windows- und Android-Tablet

Diese Anleitung geht von genau den Geräten aus, die du hast. Kein Raspberry Pi
nötig – **das Windows-Tablet ist der Rechner**, das Android-Tablet die zweite
Anzeige.

## Wer macht was

| Gerät | Aufgabe |
|---|---|
| **u-blox ZED-F9P** | Position. Mit RTK-Korrekturdaten 2–3 cm, ohne sie 1–2 m. |
| **IMU Brick** | Hangausgleich und Drehrate. Siehe unten – der wichtigste Zugewinn nach dem RTK. |
| **Phidget-Motorsteuerung** | Treibt den Lenkmotor. Der Regelkreis läuft im Programm. |
| **Windows-Tablet** | Der Rechner: Empfänger, Sensor, Motor, Datenbank, Weboberfläche. |
| **Android-Tablet** | Zweite Anzeige im Browser. Kann auch als einzige Anzeige dienen, wenn das Windows-Tablet im Schrank liegt. |

Ein F9P heißt: **ein Traktor** ist ausgerüstet. Die Master/Client-Aufteilung des
Systems bleibt trotzdem sinnvoll – das Windows-Tablet läuft als Master und ist
später bereit, wenn ein zweiter Empfänger dazukommt.

## Was der IMU bringt (und warum er kein Zubehör ist)

Die Antenne sitzt gut drei Meter über dem Boden. Sechs Grad Seitenhang – das ist
nicht viel, das merkt man auf dem Sitz kaum – schieben die Antenne **31 cm** zur
Seite. Der Empfänger misst dabei völlig korrekt; er misst nur die Antenne und
nicht den Boden.

| Seitenhang | Versatz bei 3 m Antennenhöhe |
|---|---|
| 2° | 10 cm |
| 4° | 21 cm |
| 6° | 31 cm |
| 10° | 52 cm |

Ohne Ausgleich wandert die Spur genau um diesen Betrag, sobald es quer wird –
und weil der Hang sich ändert, wandert sie unregelmäßig. Mit RTK auf zwei
Zentimeter genau zu messen und dann dreißig Zentimeter danebenzuliegen, wäre
schade um das Geld für den Empfänger.

Der IMU liefert außerdem die **Drehrate**. Die brauchst du für die Lenkung,
falls du keinen Radwinkelsensor verbaust – dazu unten mehr.

## Was dir noch fehlt

Ehrlich aufgelistet, bevor du anfängst:

| Fehlt | Wofür | Grob |
|---|---|---|
| **RTK-Korrekturdaten** | Zentimeter statt Meter. Ohne sie ist der F9P ein besserer GPS-Stick. | 0–500 €/Jahr, je nach Anbieter |
| **12-V-Versorgung** für Tablet, F9P, IMU und Motor | Der Motor zieht deutlich mehr als die Elektronik – eigene, abgesicherte Leitung. | 30–60 € |
| **Not-Aus** in der Leitung zum Lenkmotor | Nicht verhandelbar, sobald der Motor lenkt. | 20–40 € |
| **Mechanik am Lenkrad** (Zahnkranz + Reibrad oder Zahnriemen) | Der Motor muss ans Lenkrad. | je nach Bauart |
| **Antenne mit Grundplatte** aufs Kabinendach | Mittig über der Hinterachse. | 40–120 € |
| *Optional:* **Radwinkelsensor** (Poti am Achsschenkel) | Macht die Lenkung deutlich besser. Geht auch ohne, siehe unten. | 30–80 € |

## Einrichtung, der Reihe nach

### 1. Erst am Schreibtisch

Auf dem Windows-Tablet, bevor irgendetwas angeschlossen ist:

```
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

Das Skript legt alles unter `C:\AgriPilot` an, richtet die Python-Umgebung ein,
öffnet Port 8080 in der Firewall (damit das Android-Tablet mitschauen kann) und
trägt einen automatischen Start beim Anmelden ein.

Danach `C:\AgriPilot\start.bat` und im Browser `http://localhost:8080` öffnen.
Ohne angeschlossene Geräte läuft der Simulator: virtueller Traktor, virtueller
Hang, alles bedienbar. So lernst du die Oberfläche kennen, ohne im Traktor zu
sitzen.

### 2. Treiber installieren

Zwei Dinge, die die Python-Pakete **nicht** mitbringen:

* **Phidget-Treiber** von phidgets.com (der „Phidgets Installer" für Windows).
  Ohne ihn findet das Programm die Motorsteuerung nicht – es sagt das auf der
  Systemseite im Klartext, statt abzustürzen.
* **Brick Daemon (brickd)** von tinkerforge.com. Er ist die Brücke zwischen dem
  IMU Brick und dem Programm und läuft als Dienst im Hintergrund.

### 3. Geräte suchen lassen

Alles anstecken, dann:

```
C:\AgriPilot\venv\Scripts\python.exe C:\AgriPilot\scripts\scan_devices.py
```

Das Werkzeug listet serielle Anschlüsse, Phidget-Kanäle und Tinkerforge-Geräte –
und gibt die passenden Zeilen für die Konfiguration gleich zum Kopieren aus. So
musst du weder COM-Nummern raten noch die UID des IMU Bricks suchen.

Die Ausgabe in `C:\ProgramData\AgriPilot\config.yaml` übernehmen.

### 4. F9P einstellen

Einmalig mit u-center:

* Ausgabe **10 Hz**
* Sätze **GGA, RMC, VTG, GST** aktiv (GGA bringt die Fix-Qualität, GST die
  Genauigkeitsschätzung, die in der Anzeige steht)
* USB oder UART mit **115200 Baud**
* RTCM3-Eingang auf demselben Anschluss freigeben – dorthin schickt AgriPilot
  die Korrekturdaten

Prüfen mit `scripts\check_receiver.py COM3`. Dort muss nach ein paar Sekunden
etwas anderes als „kein Fix" stehen.

### 5. RTK-Zugang eintragen

```yaml
ntrip:
  enabled: true
  host: ntrip.mein-anbieter.de
  port: 2101
  mountpoint: VRS_3_2G_BY
  username: meinbenutzer
  password: meinpasswort
  send_gga: true
```

Ziel ist **RTK fix** in der Anzeige oben rechts. `RTK float` reicht nicht – die
Lösung springt dann um Dezimeter, und das siehst du erst abends an den Streifen
im Feld.

### 6. IMU einrichten

```yaml
imu:
  source: tinkerforge
  uid: ''                 # aus scan_devices.py, oder leer für das erste Gerät
  axis_map: standard      # siehe unten
  roll_sign: 1.0
  terrain_compensation: true
```

Dann in der Kabine, in dieser Reihenfolge:

1. Den IMU **fest** einbauen – geschraubt, nicht gelegt. Er misst Neigung; alles,
   was wackelt, misst Unsinn.
2. Die **Antennenhöhe** über Boden messen und unter **Menü → Maschine** eintragen.
   Diese Zahl ist der Maßstab des ganzen Ausgleichs.
3. Auf **ebenem** Boden **Menü → System → Neigungssensor nullen** drücken. Der
   Einbau ist nie exakt waagerecht, und ein Grad sind schon 5 cm Dauerversatz.
4. Auf einen bekannten Hang fahren und die Anzeige prüfen: Der Wert unter „Hang"
   muss dem Gefälle entsprechen. Zeigt er Nick statt Roll, `axis_map` auf
   `swapped` stellen; ist das Vorzeichen verkehrt, `inverted` oder
   `swapped_inverted` probieren.
5. **Probe:** Über eine Furche fahren, sodass der Traktor kippelt. Die
   angezeigte Abweichung darf dabei fast ruhig bleiben. Wird sie beim Kippeln
   *größer*, geht der Ausgleich in die falsche Richtung – dann `roll_sign` auf
   `-1.0` setzen.

Punkt 5 ist die eigentliche Abnahme. Ein falsches Vorzeichen verdoppelt den
Fehler, statt ihn aufzuheben.

### 7. Lenkmotor – zuerst lesen

> Ein Motor am Lenkrad bewegt mehrere Tonnen. Ohne Not-Aus in der Leitung und
> ohne Fahrer auf dem Sitz gehört die Anlage nicht in Betrieb. Auf öffentlichen
> Straßen hat sie nichts zu suchen.

**Wie geregelt wird**, hängt daran, was du zurückmeldest:

| `feedback` | Voraussetzung | Güte |
|---|---|---|
| `was` | Radwinkelsensor am Achsschenkel an einem Spannungsverhältnis-Eingang | am besten: geregelt wird genau das, was zählt |
| `yaw_rate` | **nur der IMU** – kein zusätzlicher Sensor | gut: geregelt wird die Drehrate des Traktors statt des Radwinkels |
| `encoder` | Drehgeber am Motor | Notlösung: die Mitte läuft über den Tag weg |

Da du den IMU ohnehin hast, ist `yaw_rate` der sinnvolle Start. Der Sollwert
kommt aus dem Einspurmodell: bei diesem Radwinkel und dieser Geschwindigkeit
müsste sich der Traktor so und so schnell drehen – und genau das misst der IMU.
Willst du es später besser, kommt ein Radwinkelsensor an einen freien
Phidget-Eingang und `feedback: was`.

```yaml
steering:
  enabled: true
  output: phidget
  require_rtk: true
  min_speed_ms: 0.3
  max_speed_ms: 8.0
  max_cross_track_m: 1.5

phidget:
  serial_number: -1       # aus scan_devices.py eintragen
  motor_channel: 0
  feedback: yaw_rate
  current_limit_a: 2.0    # bewusst niedrig!
  max_duty: 0.55
  failsafe_ms: 500
  gain_p: 0.09
  gain_i: 0.02
  gain_d: 0.01
```

**Zwei Sicherheiten stecken bewusst in der Hardware:**

* **Stromgrenze niedrig.** Du musst das Lenkrad jederzeit gegen den Motor
  bewegen können. Das ist die letzte Ebene, wenn Programm und Elektronik
  gleichzeitig versagen. Lieber zu schwach anfangen und in Schritten von 0,5 A
  erhöhen, bis die Lenkung zügig genug folgt.
* **Failsafe der Phidget-Steuerung.** Sie bekommt eine Frist von 500 ms gesetzt
  und hält den Motor selbstständig an, wenn das Programm verstummt. Ein
  abgestürztes Tablet oder ein abgezogenes USB-Kabel führen damit zum Stillstand
  des Motors – nicht zu einem festgehaltenen Einschlag.

**Zur Ehrlichkeit:** Ohne Radwinkelsensor kann das Programm einen Eingriff des
Fahrers nicht erkennen – es sieht das Rad ja nicht. Es schaltet dann nicht von
selbst ab, wenn du gegenhältst. Deshalb sind bei `yaw_rate` die niedrige
Stromgrenze und der Not-Aus keine Empfehlung, sondern Bedingung. Mit
Radwinkelsensor erkennt das System den Eingriff und gibt sofort ab.

**Erste Fahrt:** freie Fläche, Schritttempo, Hand am Lenkrad. Zieht es zu träge
auf die Spur, `gain_p` in Schritten von 0,02 erhöhen. Pendelt es um die Spur,
`gain_p` verringern oder `gain_d` leicht erhöhen.

### 8. Android-Tablet als Anzeige

Im Browser `http://<Adresse-des-Windows-Tablets>:8080` öffnen – die Adresse gibt
das Installationsskript am Ende aus. Dann im Browsermenü **Zum Startbildschirm
hinzufügen**: danach startet die Anzeige im Vollbild wie eine App.

Beide Tablets zeigen dasselbe live und können beide bedienen. Praktisch, wenn
das Windows-Tablet fest verbaut ist und du das Android-Tablet zum Umfahren der
Feldgrenze in die Hand nimmst.

Das Android-Tablet **kann den Rechner nicht ersetzen** – F9P, IMU und Motor
hängen per USB am Windows-Tablet.

## Reihenfolge der Inbetriebnahme

Nicht alles auf einmal. In dieser Reihenfolge findest du Fehler dort, wo sie
entstehen:

1. Simulator am Schreibtisch – Oberfläche kennenlernen.
2. F9P dran, Fläche vermessen, eine Fahrt aufzeichnen. **Noch ohne Lenkung.**
3. RTK-Zugang – warten, bis „RTK fix" dauerhaft steht.
4. IMU dran, nullen, Vorzeichen am Hang prüfen.
5. Ein paar Stunden als reine Lenkhilfe fahren (Lichtbalken, du lenkst). Erst
   wenn die Spuren sauber liegen, stimmt die Grundlage.
6. Motor mechanisch anbauen, Not-Aus einbauen, `steering.enabled: true`.
7. Erste Lenkfahrt auf freier Fläche, langsam, Hand am Lenkrad.

Wer Schritt 5 überspringt, sucht später Lenkfehler, die in Wahrheit Mess- oder
Maßfehler sind.

## Wenn etwas nicht geht

| Bild | Ursache | Abhilfe |
|---|---|---|
| Systemseite: „Phidget-Treiber fehlt" | Nur das Python-Paket installiert | Phidgets-Installer von phidgets.com |
| Systemseite: „Kein IMU Brick gefunden" | brickd läuft nicht | Brick Daemon installieren und starten |
| Hang zeigt Nick statt Neigung | Sensor quer eingebaut | `axis_map: swapped` |
| Beim Kippeln wird die Abweichung größer | Ausgleich verkehrt herum | `roll_sign: -1.0` |
| Spur liegt gleichmäßig daneben | Antennenmaße | unter **Maschine** nachmessen |
| Lenkung wird nicht scharf | eine Bedingung fehlt | die Anzeige nennt den Grund |
| Motor zittert um die Mitte | Regler zu scharf | `gain_p` verringern |
| Motor folgt zu träge | Regler zu weich oder Strom zu knapp | `gain_p` oder `current_limit_a` erhöhen |
| Android-Tablet erreicht nichts | Firewall oder falsches Netz | Port 8080 freigegeben? Gleiches WLAN? |
