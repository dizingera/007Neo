# Teilbreiten-Adapter für AgriPilot 1.5.41

Stand: 16.10.2026. Eigene UDP-Schnittstelle, kein Herstellerprotokoll und kein
ISOBUS. Noch keine reale Hardware ausgewählt oder erprobt. Standard: Ausgang aus.

## Verbindung und Befehl

AgriPilot öffnet einen verbundenen UDP-Socket zur eingetragenen Adresse/Port
(Standard 127.0.0.1:8890), sendet etwa alle 100 ms ein UTF-8-JSON-Datagramm:

```json
{"protokoll":"agripilot-teilbreiten/1","sitzung":"zufaellige-sitzung","seq":17,"maske":5,"anzahl":4,"watchdog_ms":500}
```

`maske` ist eine nichtnegative Ganzzahl, `anzahl` 1 bis 32. Bit 0 bezeichnet die
erste linke Teilbreite in Fahrtrichtung, danach nach rechts. 5 schaltet hier
Teilbreiten 1 und 3 an. Nicht vorhandene Bits müssen null sein. `seq` steigt je
Datagramm; `sitzung` ändert sich bei neuem Ausgang. AUS ist immer Maske 0.

Der Adapter antwortet an die Quelladresse und den Quellport des Datagramms:

```json
{"protokoll":"agripilot-teilbreiten/1","sitzung":"zufaellige-sitzung","seq":17,"maske":5}
```

Die Rückmeldung muss den **tatsächlich geschalteten Zustand** wiedergeben.
Zusätzliche eingeschaltete Bits gegenüber dem Befehl werden verworfen. Ein
bloßes Zurückspiegeln der Sollmaske ohne Zustandsprüfung bestätigt keine Ventile.
AgriPilot übernimmt ausschließlich passende Sitzung, Sequenz und aktuellen
Schaltstand. Bei Masken-/Anzahlwechsel werden frühere Rückmeldungen entwertet.
Telegramme über 2048 Byte, ungültige JSON-/Zahlwerte oder verspätete/ältere
Rückmeldungen werden ignoriert. Bestätigung gilt höchstens 400 ms ab dem
ursprünglichen Sendezeitpunkt; Duplikate verlängern diese Frist nicht.

## Abschalten und Darstellung

Der Fahrdatenwunsch läuft nach 500 ms ohne Erneuerung ab und wird null. Bei
Arbeitsende, ungültigem Fix und verworfenem Filterausreißer fordert die Engine
sofort null an. Der Ausgang sendet beim regulären Beenden zusätzlich AUS.
Im UDP-Betrieb sind nur frisch bestätigte Bits wirksam für Flächenmarkierung
und Abschnittsanzeige. Verlorene Rückmeldungen erzeugen deshalb keine behauptete
Behandlung. Eine bereits behandelte Fläche bleibt natürlich gespeichert.

Der Adapter **muss unabhängig vom PC** bei fehlenden gültigen Befehlen innerhalb
von 500 ms alle Ausgänge abschalten. Beim Einschalten, Neustart oder neuer
Sitzung zunächst AUS; alte Pakete/Sitzungen dürfen keine Ventile wieder öffnen.
Hardware-Not-Aus, sichere Ausgangsstufen und Zuordnung der Ventile sind Aufgaben
der später gewählten Hardware. UDP enthält keine Verschlüsselung oder
Authentifizierung und ist für den lokalen Adapter bzw. ein geschütztes
Maschinennetz vorgesehen, nicht für eine direkte Internetanbindung.

Ein frischer ACK zeigt nur die Rückmeldung des Adapters. Eine echte mechanische
Ventilrückmeldung erfordert passende Sensorik. Die Software kann weder defekte
Relais noch mechanisch klemmende Ventile erkennen, wenn der Adapter dies nicht
meldet. Vor realer Verwendung muss das gewählte Gerät gegen dieses Protokoll und
den eigenen Watchdog geprüft werden. Der lokale UDP-Testpeer in der Testsuite
ist ein Testwerkzeug, keine Steuergeräte-Firmware.
