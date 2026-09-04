# Hardware

## Was pro Traktor gebraucht wird

| Teil | Empfehlung | Grober Preis |
|---|---|---|
| Rechner | Raspberry Pi 4 (2 GB reichen) oder Pi 5 | 60–90 € |
| Speicher | SD-Karte 32 GB, **Industrie-/High-Endurance-Typ** | 15 € |
| Stromversorgung | 12 V → 5 V/3 A Wandler mit Puffer (Kfz-tauglich) | 20–30 € |
| GNSS-Empfänger | u-blox ZED-F9P (RTK-fähig, zwei Frequenzen) | 200–350 € |
| Antenne | Mehrfrequenz-Antenne mit Grundplatte, magnetisch | 40–120 € |
| Anzeige | vorhandenes Tablet im Browser, oder 7" Touch am Pi | 0–90 € |
| Netzwerk | WLAN-Router im Hof; im Feld Mobilfunk oder Richtfunk | – |

Der Pi kommt in ein geschlossenes Gehäuse. Staub und Rüttelei sind die zwei
Dinge, die diese Installation umbringen; ein Lüfter zieht Staub, ein
Alu-Gehäuse als Kühlkörper nicht.

**Zur SD-Karte:** eine normale Karte stirbt an einer Anlage, die täglich hart
vom Strom getrennt wird. High-Endurance-Karten kosten fünf Euro mehr. Die
Datenbank läuft im WAL-Modus und übersteht einen Stromausfall mitten im Satz,
aber gegen eine kaputte Karte hilft das nicht.

## Antenne montieren

Die Antenne gehört **mittig über die Hinterachse**, auf das Kabinendach, mit
freier Sicht zum Himmel. Jeder Zentimeter Versatz landet als Versatz in der
Spur – deshalb wird die Position gemessen und im Menü **Maschine** eingetragen:

* `Antenne nach vorn` – Abstand von der Mitte der Hinterachse nach vorn (+).
* `Antenne nach rechts` – Versatz aus der Fahrzeugmitte nach rechts (+).
* `Gerät hinter Achse` – von der Hinterachse nach hinten bis zur Arbeitsebene
  des Anbaugeräts.
* `Gerät seitlich` – wenn das Gerät außermittig läuft (Mulcher, Seitenmähwerk).

Diese vier Zahlen sind der häufigste Grund für „das System fährt daneben".
Einmal mit dem Maßband richtig gemessen, stimmt es danach immer.

## RTK-Korrekturdaten

Zentimeter gibt es nur mit Korrekturdaten von einer Basisstation. Drei Wege:

1. **Öffentlicher/staatlicher Dienst** (in Deutschland z.B. SAPOS, in Österreich
   APOS, dazu private Anbieter). Zugangsdaten eintragen, fertig. Kostet je nach
   Bundesland zwischen nichts und einigen hundert Euro im Jahr.
2. **Verein oder Nachbar** mit eigener Basis und offenem NTRIP-Caster.
3. **Eigene Basisstation**: ein zweiter ZED-F9P auf einem festen Punkt am Hof.
   Einmalig teurer, danach kostenlos und unabhängig vom Mobilfunk. Der Master
   kann die Korrekturen direkt weitergeben.

Eingetragen wird das in `/etc/agripilot/config.yaml` unter `ntrip:`. Der Master
hält **eine** Verbindung zum Caster und gibt den Datenstrom an alle anderen
Traktoren weiter (Port 2102) – die brauchen dadurch weder eigene SIM-Karte noch
zweiten Zugang.

Die Anzeige oben rechts zeigt jederzeit, woran man ist: `RTK fix` ist das Ziel,
`RTK float` reicht nicht (die Lösung kann um Dezimeter springen), `DGPS` und
`GPS` sind reine Orientierung.

## Empfänger einstellen

Der ZED-F9P wird mit u-center einmal eingerichtet:

* Ausgabe **10 Hz** (5 Hz gehen auch, weniger nicht).
* Aktive Sätze: **GGA, RMC, VTG, GST**. GGA liefert die Fix-Qualität, GST die
  Genauigkeitsschätzung, die im Display steht.
* Schnittstelle USB oder UART mit **115200 Baud**.
* RTCM3-Eingang auf demselben Anschluss freigeben – dort schickt AgriPilot die
  Korrekturen hin.

Prüfen lässt sich das ohne den Rest des Systems:

```bash
python3 scripts/check_receiver.py                # Anschlüsse auflisten
python3 scripts/check_receiver.py /dev/ttyACM0   # 20 s mitlesen
```

## Kurs bei langsamer Fahrt

Aus einer einzelnen Antenne kommt kein echter Kurs, sondern nur die
Bewegungsrichtung. Unter etwa 0,5 m/s ist die unbrauchbar – genau beim
Anfahren am Vorgewende. AgriPilot hält deshalb den letzten guten Wert, bis der
Traktor wieder rollt.

Wer das sauber lösen will, nimmt einen **Zweiantennen-Empfänger** (liefert einen
`HDT`-Satz mit echter Fahrzeugausrichtung); AgriPilot benutzt den automatisch,
sobald er da ist.

## Lenkautomatik anschließen

> Erst lesen, dann bauen. Eine Lenkung, die sich selbst bewegt, ist keine
> Bastelei – ohne Not-Aus und ohne Abschaltung bei Fahrereingriff gehört das
> System nicht in Betrieb.

AgriPilot rechnet den Lenkwinkel und schickt ihn als UDP-Telegramm an eine
Lenkplatine (Arduino, ESP32 oder eine fertige Platine aus dem AgOpenGPS-Umfeld).
Die Platine treibt den Lenkmotor oder das Proportionalventil und meldet zurück.

**Befehl an die Platine, 11 Byte:**

| Byte | Inhalt |
|---|---|
| 0–1 | `A` `P` |
| 2 | Version, aktuell 1 |
| 3 | Flags: Bit 0 = lenken (1) oder Mitte (0) |
| 4–5 | Soll-Lenkwinkel in Hundertstel Grad, vorzeichenbehaftet, + = rechts |
| 6–7 | Geschwindigkeit in cm/s, ohne Vorzeichen |
| 8–9 | Abweichung von der Spur in mm, vorzeichenbehaftet |
| 10 | XOR-Prüfsumme über Byte 0–9 |

Alles Little-Endian. Der Befehl kommt zehnmal pro Sekunde. **Bleibt er länger
als 0,5 Sekunden aus, muss die Platine von sich aus auf Mitte stellen** – das
ist die Absicherung gegen einen abgestürzten Pi oder ein abgezogenes Kabel.

**Rückmeldung an AgriPilot** (an denselben Port, mindestens 6 Byte): `A`, `P`,
Version, dann Ist-Lenkwinkel in Hundertstel Grad (2 Byte) und ein Flag-Byte mit
Bit 0 = Fahrer hat ins Lenkrad gegriffen, Bit 1 = Schalter am Bedienteil.
Kommt Bit 0, schaltet AgriPilot sofort ab und bleibt aus, bis der Fahrer neu
scharf schaltet – ein stilles Wiedereinschalten mitten in einer
Handkorrektur wäre die unangenehmste Überraschung, die so ein System machen kann.

## Netzwerk

Am einfachsten: ein WLAN-Router im Hof, alle Pis mit fester Adresse.

```
Master   192.168.10.1     Port 8080 Anzeige, 2102 Korrekturdaten
Traktor2 192.168.10.11
Traktor3 192.168.10.12
```

Auf dem Feld ohne Empfang arbeitet jeder Traktor eigenständig weiter – die
komplette Datenbank liegt auf jedem Gerät. Sobald der Master wieder erreichbar
ist, gleicht sich alles von selbst ab. Zwei Traktoren auf demselben Feld
bekommen die bearbeiteten Flächen des anderen dazu, sobald wieder Verbindung
besteht.
