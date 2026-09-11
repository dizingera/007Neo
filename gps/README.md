# AgriPilot – GPS-Spurführung für Traktoren

Ein fertiges Lenksystem: Spurführung mit AB-Linien und Kurven, Hangausgleich
über einen Neigungssensor, Anzeige der bearbeiteten Fläche, Flächenvermessung
und Aufzeichnung aller Fahrten. Ein Gerät ist der **Master**, jeder weitere
Traktor ein **Client** – Felder, Spuren und RTK-Korrekturen verteilt der Master
von selbst.

Läuft auf einem **Raspberry Pi** oder ebenso auf einem **Windows-Tablet**.
Bedient wird über den Browser, also auf jedem Tablet, Handy oder Bildschirm im
gleichen Netz.

> **Du hast F9P, Phidget-Motor, IMU Brick und ein Windows- plus Android-Tablet?**
> Dann direkt zu [docs/DEINE_ANLAGE.md](docs/DEINE_ANLAGE.md) – dort steht der
> Weg für genau diese Geräte.

![Kabinenanzeige](docs/bilder/kabine.png)

## Was es kann

| | |
|---|---|
| **Spurführung** | AB-Linien, A+ (Punkt und Himmelsrichtung), aufgezeichnete Kurven, Kontur (die Feldgrenze als Ringspur). Lichtbalken und Abweichung in Zentimetern. Spurversatz („Nudge") in 10-cm-Schritten. Blick über die Haube in Perspektive, wahlweise flach oder Norden oben; Tag- und Nachtseite. |
| **Vorgewende** | Tiefe in Arbeitsbreiten aus der Feldgrenze, Restdistanz in Metern, Annäherungsalarm. Zwei Wendemuster – Ω-Wende (weiter Bogen) und U-Wende (kompakt) – jeweils geplant, angesehen und erst dann gefahren. Geprüft wird jeder Punkt der Route gegen die Feldgrenze. |
| **Bearbeitete Fläche** | Wird live mitgezeichnet. Hektar, Überlappung in Prozent, Lücken sofort sichtbar. |
| **Sektionen** | Bis zu 24 Teilbreiten, automatisch aus über bereits bearbeitetem Boden und außerhalb der Feldgrenze. |
| **Gezogene Geräte** | Anhängerkinematik statt starrem Versatz: die Ausrichtung des Geräts läuft dem Fahrzeug nach, abhängig von Tempo und Deichsellänge. In der Kurve wird dort markiert, wo das Gerät wirklich ist. |
| **Flächenvermessung** | Feld einmal umfahren – Grenze und Hektar sind gespeichert. |
| **Fahrtenaufzeichnung** | Jede Arbeit mit Datum, Dauer, Strecke, Fläche und Überlappung. Export als GPX, GeoJSON und CSV. |
| **Rohdaten und Abspielmodus** | Zeichnet auf Wunsch auf, was *hereinkommt* – rohe NMEA-Sätze und Lagemeldungen mit Zeitstempel – und spielt dieselbe Datei wieder ein. Ein Fehler vom Feld lässt sich damit am Schreibtisch nachstellen, statt ihn zu erraten. |
| **Mehrere Traktoren** | Der Master verteilt Felder und Spuren und gibt die RTK-Korrekturen weiter. Beide sehen, was der andere schon bearbeitet hat. |
| **Hangausgleich** | Neigungssensor (Tinkerforge IMU Brick) rechnet die Schräglage heraus. Bei 3 m Antennenhöhe sind 6° Hang sonst 31 cm Versatz. |
| **Lenkautomatik** | Rechnet den Lenkwinkel und treibt entweder einen Phidget-Motor (Positionsregler der Platine, Sollwinkel in Grad) oder gibt an eine externe Lenkplatine aus – **ab Werk abgeschaltet**, siehe [Sicherheit](#sicherheit). |

## Ehrlich vorweg: woran die Genauigkeit hängt

Die Software rechnet auf den Zentimeter genau. Ob das im Feld ankommt,
entscheidet allein der Empfänger:

| Ausstattung | Wiederholgenauigkeit | Wofür es reicht |
|---|---|---|
| Einfacher GPS-Stick | 2–5 m | gar nichts in der Spurführung |
| GNSS mit SBAS/EGNOS | 0,5–1,5 m | grobe Orientierung, Flächenmessung |
| RTK (z.B. u-blox ZED-F9P + Korrekturdaten) | **2–3 cm** | Spurführung, Sektionen, Lenkautomatik |

Ohne RTK ist alles außer der Fahrtenaufzeichnung und der Flächenmessung nur
eine Orientierungshilfe. Die Einkaufsliste steht in
[docs/HARDWARE.md](docs/HARDWARE.md).

Und selbst mit RTK bleibt am Hang eine Lücke: die Antenne steht drei Meter hoch,
sechs Grad Seitenhang schieben sie 31 cm zur Seite. Genau dafür ist der
Neigungssensor da.

## Schnellstart ohne Hardware

Das System läuft mit einem eingebauten Traktor-Simulator – so lässt sich alles
ansehen und einstellen, bevor ein einziges Kabel verlegt ist:

```bash
cd gps/backend
pip install -r requirements.txt
python3 ../scripts/run_sim.py
```

Dann `http://localhost:8088` öffnen. Unter **Menü → System** stehen zwei Regler
für Geschwindigkeit und Lenkung des virtuellen Traktors.

`run_sim.py` legt Konfiguration und Datenbank in `gps/.simulator/` ab und lässt
damit die Einstellungen einer echten Anlage unangetastet. Wer stattdessen
`python3 -m agripilot.server` startet, landet in `C:\ProgramData\AgriPilot`
bzw. `/etc/agripilot` – dort schreiben zu dürfen ist nicht garantiert.

Eine aufgezeichnete Fahrt statt des Simulators:

```bash
python3 ../scripts/run_sim.py --abspielen rohdaten-20260910-224314.txt
```

## Auf dem Traktor einrichten

**Raspberry Pi:**

```bash
sudo bash scripts/install_pi.sh master              # Pi im Hof
sudo bash scripts/install_pi.sh client 192.168.10.1 # jeder weitere Traktor
```

**Windows-Tablet** (PowerShell als Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

Welche Geräte angeschlossen sind – und welche Zeilen dafür in die
Konfiguration gehören – sagt `scripts/scan_devices.py`.

Der ganze Weg – vom leeren Pi bis zur ersten Spur – steht Schritt für Schritt in
[docs/INSTALL.md](docs/INSTALL.md). Die Bedienung im Feld erklärt
[docs/BEDIENUNG.md](docs/BEDIENUNG.md).

## Sicherheit

Die Lenkautomatik bewegt eine mehrere Tonnen schwere Maschine. Sie schaltet nur
ein, wenn **alles gleichzeitig** stimmt: in der Konfiguration freigegeben, vom
Fahrer scharf geschaltet, Spur aktiv, RTK-Fix vorhanden, Geschwindigkeit im
zulässigen Bereich, Abweichung unter 1,5 m und die Positionsdaten frisch. Fällt
eine Bedingung weg, geht die Lenkung sofort auf und sagt im Klartext, warum.
Reißt die Verbindung zur Lenkplatine ab, stellt deren Watchdog nach 0,5
Sekunden auf Mitte.

Das ersetzt keinen Not-Aus und keinen Fahrer auf dem Sitz. Auf öffentlichen
Straßen hat die Lenkautomatik nichts zu suchen.

## Aufbau

```
gps/
├── backend/agripilot/
│   ├── geo.py        Projektion in ein lokales Meter-Koordinatensystem, Flächen
│   ├── nmea.py       NMEA-0183-Auswertung, Fix-Qualität, Genauigkeit
│   ├── gnss.py       Empfänger über Seriell/TCP/UDP – und der Simulator
│   ├── imu.py        Neigungssensor: Hangausgleich, Drehrate, Kurs im Stand
│   ├── ntrip.py      RTK-Korrekturen (Caster, roher Strom, Funkmodem) und
│                     ihre Weitergabe an die anderen Traktoren
│   ├── guidance.py   AB-Linien, Kurven, Kontur, Spurabstand, Abweichung,
│                     Lenkwinkel, Nachlauf gezogener Geräte
│   ├── headland.py   Vorgewende: Restdistanz, Alarm, Ω- und U-Wende, Route
│   ├── recorder.py   Rohdaten mitschreiben und wieder abspielen
│   ├── coverage.py   Bearbeitete Fläche als Raster, Überlappung, Sektionen
│   ├── steering.py   Lenkbefehl mit allen Sicherheitsbedingungen
│   ├── actuators.py  Ausgänge: Phidget-Motor, externe Lenkplatine, nur Anzeige
│   ├── engine.py     Führt alles zusammen: eine Position rein, ganzes Bild raus
│   ├── storage.py    SQLite: Felder, Spuren, Aufträge, Fahrspuren
│   ├── sync.py       Master/Client-Abgleich, Zusammenführen der Flächen
│   ├── export.py     GPX, GeoJSON, CSV
│   ├── checklist.py  Inbetriebnahme: die acht Schritte, mitlesend geprüft
│   ├── settings.py   Alle Einstellungen: Bereiche, Erklärung, Wirkzeitpunkt
│   ├── yamlfile.py   Konfigurationsdatei lesen und schreiben, auch ohne PyYAML
│   └── server.py     Weboberfläche, Live-Verbindung, Schnittstelle
├── frontend/         Kabinenanzeige: Blick über die Haube, Tag und Nacht
│                     (kein Bauschritt nötig; Schriften liegen bei, weil der
│                     Pi im Feld kein Internet hat)
├── scripts/          Installation (Pi und Windows), Dienst, Gerätesuche,
│                     Empfänger- und Lenkung einmessen
└── docs/             Hardware, Installation, Bedienung, deine Anlage,
                      Android-Tablet als Kabinenanzeige
```

## Tests

```bash
cd gps/backend && python3 -m unittest discover -s tests -v
```

203 Tests, ohne Zusatzpakete lauffähig. Geprüft wird vor allem, was im Feld Geld
kostet, wenn es falsch ist: Flächen, das Vorzeichen der Abweichung, der
Hangausgleich und die Bedingungen, unter denen die Lenkautomatik einschalten
darf.

Die Wende wird dabei einmal wirklich gefahren: Position hinein, Einschlag
heraus, Einspurmodell bewegt die Maschine, von vorn – und am Ende die Frage,
ob sie auf der Nachbarspur steht und zurückschaut. Ein verdrehtes Vorzeichen
sieht in jedem einzelnen Test richtig aus und schickt die Maschine auf dem Feld
trotzdem in die andere Richtung.
