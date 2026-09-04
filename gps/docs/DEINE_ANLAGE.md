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

### 5. Eigene Basisstation anbinden

Du hast eine eigene Basis – damit entfällt der Anbieter, und du bist unabhängig
vom Mobilfunk. Bleibt die Frage, **wie** die Korrekturen zum Traktor kommen:

**a) Deine Basis läuft als NTRIP-Caster** (z.B. RTKBase oder str2str im
Caster-Betrieb). Der häufigste Fall bei einer selbst gebauten Basis:

```yaml
corrections:
  source: ntrip
  host: 192.168.10.5        # Adresse der Basis im Hofnetz
  port: 2101
  mountpoint: BASIS1
  username: ''              # bei eigener Basis oft leer
  password: ''
  send_gga: false           # eine Einzelbasis braucht deine Position nicht
```

**b) Deine Basis öffnet nur einen Port** und schickt rohes RTCM3, ohne
Anmeldung und ohne Mountpoint:

```yaml
corrections:
  source: tcp
  host: 192.168.10.5
  port: 9000
```

**c) Ein Funkmodem hängt am Windows-Tablet** und reicht die Korrekturen herein:

```yaml
corrections:
  source: serial
  serial_port: COM4
  baudrate: 115200
```

**d) Das Funkmodem steckt direkt am F9P.** Dann `source: aus` – die Korrekturen
laufen an der Software vorbei direkt in den Empfänger, und das ist genau
richtig. Du siehst am Fix-Status trotzdem, ob sie ankommen.

`send_gga` braucht nur ein Netz-RTK-Dienst, der eine virtuelle Basis an deiner
Position rechnet. Bei einer einzelnen eigenen Basis kannst du es abschalten.

> **Der eine Fehler, der teuer wird:** Stelle deine Basis auf **feste
> Koordinaten** (Fixed Mode), nicht auf Survey-in bei jedem Einschalten. Bei
> Survey-in landet sie nach jedem Stromausfall auf leicht anderen Koordinaten –
> und weil alle Spuren relativ zur Basis liegen, wandert das ganze Feld mit.
> Deine AB-Linie von letzter Woche liegt dann um Dezimeter daneben, ohne dass
> irgendwo ein Fehler erscheint. Einmal ein langes Survey-in (mehrere Stunden),
> Ergebnis notieren, fest eintragen, nie wieder ändern.

Ziel ist **RTK fix** in der Anzeige oben rechts. `RTK float` reicht nicht – die
Lösung springt dann um Dezimeter, und das siehst du erst abends an den Streifen
im Feld. Auf der Systemseite steht außerdem das Alter der Korrekturen; steigt es
über etwa 30 Sekunden, trägt der Weg von der Basis nicht mehr.

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

**Mit Drehgeber am Motor** – das ist dein Fall, wenn du einen Zählwert je Grad
hast – regelt die Phidget-Platine selbst. Über den `RescaleFactor` bekommt sie
ihre Einheit auf Grad gesetzt, danach geht der Sollwinkel direkt in Grad
hinüber und der PID läuft in der Firmware. Ruhiger als jeder Regelkreis in
Python, weil er nicht an den zehn Positionen pro Sekunde vom Empfänger hängt.

```yaml
steering:
  enabled: true
  output: phidget
  require_rtk: true
  min_speed_ms: 0.3
  max_speed_ms: 8.0
  max_cross_track_m: 1.5

phidget:
  serial_number: -1        # aus scan_devices.py eintragen
  motor_channel: 0
  control: position
  counts_per_deg: 40.0     # DEIN Wert: Zählwerte je Grad Radeinschlag am Boden
  max_wheel_angle_deg: 35.0
  current_limit_a: 2.0     # bewusst niedrig!
  velocity_limit: 0.55
  dead_band_deg: 0.3
  failsafe_ms: 500
  position_kp: 12000
  position_ki: 40
  position_kd: 300000
```

**Zu `counts_per_deg`:** der Wert bezieht sich auf den Einschlag der Räder **am
Boden**, nicht auf die Drehung am Lenkrad – das ist die Größe, die die
Spurführung rechnet. Kennst du ihn nicht genau, miss ihn aus (Motor bleibt
stromlos, gelenkt wird von Hand):

```
C:\AgriPilot\venv\Scripts\python.exe C:\AgriPilot\scripts\measure_steering.py
```

Das Werkzeug zählt von Anschlag zu Anschlag mit, fragt nach dem gesamten
Einschlagbereich in Grad und gibt den fertigen Konfigurationsblock aus. Nach
einer Änderung den Dienst neu starten – der `RescaleFactor` wird beim Verbinden
gesetzt.

**Die Mitte:** der Drehgeber zählt relativ und kennt keine Geradeausstellung.
Sie wird beim Scharfschalten gelernt – **beim Scharfschalten stehen die Räder
also gerade.** Neu setzen über **Menü → System → Lenkung: Mitte lernen**.

**Ohne Drehgeber** bleibt der Weg über die Drehrate: `control: velocity` und
`feedback: yaw_rate`. Der Sollwert kommt dann aus dem Einspurmodell – bei diesem
Radwinkel und dieser Geschwindigkeit müsste sich der Traktor so schnell drehen,
und genau das misst der IMU. Braucht keinen zusätzlichen Sensor, ist aber
weniger direkt.

**Zwei Sicherheiten stecken bewusst in der Hardware:**

* **Stromgrenze niedrig.** Du musst das Lenkrad jederzeit gegen den Motor
  bewegen können. Das ist die letzte Ebene, wenn Programm und Elektronik
  gleichzeitig versagen. Lieber zu schwach anfangen und in Schritten von 0,5 A
  erhöhen, bis die Lenkung zügig genug folgt.
* **Failsafe der Phidget-Steuerung.** Sie bekommt eine Frist von 500 ms gesetzt
  und hält den Motor selbstständig an, wenn das Programm verstummt. Ein
  abgestürztes Tablet oder ein abgezogenes USB-Kabel führen damit zum Stillstand
  des Motors – nicht zu einem festgehaltenen Einschlag.

**Zur Ehrlichkeit beim Eingriff des Fahrers:** Mit dem Positionsregler bekommt
das System einen brauchbaren Schutz geschenkt – bleibt die Abweichung groß,
während die Platine nahe ihrer Leistungsgrenze arbeitet, hält entweder du
dagegen oder die Mechanik klemmt, und beides führt zum Abgeben. Bei
`control: velocity` mit `feedback: yaw_rate` geht das **nicht**: dort sieht das
Programm das Rad nicht und merkt nichts, wenn du gegenhältst. Die niedrige
Stromgrenze und der Not-Aus sind dort Bedingung, keine Empfehlung.

Unabhängig davon: bleibt der Empfänger länger als zwei Sekunden stumm, schaltet
die Lenkung von selbst ab – zusätzlich zum Failsafe der Platine.

**Erste Fahrt:** freie Fläche, Schritttempo, Hand am Lenkrad, Räder gerade beim
Scharfschalten. Zieht es zu träge auf die Spur, `position_kp` in Schritten von
etwa 20 % erhöhen. Zittert der Motor um die Mitte, `position_kp` verringern oder
`dead_band_deg` leicht erhöhen.

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
3. Basis anbinden – prüfen, dass sie auf festen Koordinaten steht, und warten,
   bis „RTK fix" dauerhaft steht.
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
| Fix bleibt „GPS", Korrekturen kommen nicht an | falsche Quelle eingestellt | `corrections.source` prüfen – Caster, roher Port, Funkmodem oder „aus" |
| Alter der Korrekturen steigt | Weg von der Basis abgerissen | Funkstrecke oder Netz prüfen; die Byte-Zahl allein täuscht |
| Spuren von letzter Woche liegen daneben | Basis macht Survey-in statt fester Koordinaten | Basis auf Fixed Mode umstellen |
| Systemseite: „Phidget-Treiber fehlt" | Nur das Python-Paket installiert | Phidgets-Installer von phidgets.com |
| Systemseite: „Kein IMU Brick gefunden" | brickd läuft nicht | Brick Daemon installieren und starten |
| Hang zeigt Nick statt Neigung | Sensor quer eingebaut | `axis_map: swapped` |
| Beim Kippeln wird die Abweichung größer | Ausgleich verkehrt herum | `roll_sign: -1.0` |
| Spur liegt gleichmäßig daneben | Antennenmaße | unter **Maschine** nachmessen |
| Lenkung wird nicht scharf | eine Bedingung fehlt | die Anzeige nennt den Grund |
| Motor zittert um die Mitte | Regler zu scharf | `position_kp` verringern oder `dead_band_deg` erhöhen |
| Motor folgt zu träge | Regler zu weich oder Strom zu knapp | `position_kp` oder `current_limit_a` erhöhen |
| Lenkt systematisch zu viel oder zu wenig | `counts_per_deg` falsch | mit `measure_steering.py` neu ausmessen |
| Lenkung geht in die falsche Richtung | Zählrichtung verkehrt | `invert_motor: true` |
| „Mitte noch nicht gelernt" | Räder standen beim Scharfschalten schräg | gerade stellen, **System → Lenkung: Mitte lernen** |
| Android-Tablet erreicht nichts | Firewall oder falsches Netz | Port 8080 freigegeben? Gleiches WLAN? |
