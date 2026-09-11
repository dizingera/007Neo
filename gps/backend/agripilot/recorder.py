"""Rohdaten aufzeichnen - und wieder abspielen.

Ein Fehler auf dem Feld ist teuer zu untersuchen: er passiert einmal, bei Regen,
mit einem Anhänger hinten dran, und wenn man ihn nachstellen will, steht der
Traktor schon wieder in der Halle. Was bleibt, ist die Erinnerung des Fahrers -
und die reicht nicht, um zwischen "der Empfänger hat gesprungen", "der Sensor
hatte ein falsches Vorzeichen" und "die Führung hat sich verrechnet" zu
unterscheiden.

Deshalb schreibt AgriPilot auf Wunsch mit, was **hereinkommt**: die rohen
NMEA-Sätze und die Lagemeldungen des Neigungssensors, mit Zeitstempel. Und
dieselbe Datei lässt sich als Quelle wieder einlesen. Am Schreibtisch läuft dann
dieselbe Fahrt noch einmal, durch dieselbe Rechenkette - nur dass man diesmal
zusehen, anhalten und den Code ändern kann.

Aufgezeichnet wird ausdrücklich das **Rohe**, nicht das Errechnete. Ein Protokoll
der Ergebnisse würde jeden Fehler mit aufzeichnen, den man gerade sucht: liegt
er in der Auswertung, steht in der Datei schon das falsche Ergebnis, und der
Abspielmodus bestätigt ihn brav. Roh heißt: was der Empfänger gesagt hat, in
seinen Worten.

**Format** - eine Textdatei, absichtlich:

    # agripilot-rohdaten 1
    # begonnen 2026-09-10T21:15:03
    0.000	N	$GNGGA,191503.00,4808.2334,N,...*5C
    0.000	I	-1.82,0.44,,2.10,3
    0.100	N	$GNRMC,191503.10,A,...*61

Erste Spalte: Sekunden seit dem Beginn. Zweite: `N` für einen NMEA-Satz, `I` für
eine Lagemeldung (roll, pitch, yaw, Drehrate, Kalibrierung - yaw darf leer sein).
Wer kein Programm zur Hand hat, öffnet die Datei im Texteditor und liest die
Sätze. Ein Binärformat wäre kleiner und in genau dem Moment nutzlos, in dem man
es braucht.

**Beim Abspielen führt eine Quelle beide Ströme.** Zwei getrennte Leser derselben
Datei würden auseinanderlaufen, und dann läge die Schräglage neben der Position -
also genau der Fehler, den man untersuchen wollte, nur diesmal selbst gemacht.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Optional

from .gnss import FixCallback, GnssSource
from .imu import Attitude, ImuSource

KOPFZEILE = "# agripilot-rohdaten 1"
DATEIMUSTER = re.compile(r"^rohdaten-\d{8}-\d{6}\.txt$")

# Bei zehn Positionen je Sekunde sind das rund 2 kB/s, also gut 7 MB je Stunde.
# Die Grenze ist keine Schikane, sondern die Zusage, dass eine vergessene
# Aufzeichnung dem Pi nicht die Karte vollschreibt.
GROESSE_MAX_MB = 200
SCHREIB_SAMMLUNG = 50       # Zeilen, bevor auf die Karte geschrieben wird


def dateiname(jetzt: Optional[float] = None) -> str:
    return time.strftime("rohdaten-%Y%m%d-%H%M%S.txt",
                         time.localtime(jetzt or time.time()))


def ist_gueltiger_name(name: str) -> bool:
    """Nur die selbst vergebenen Namen - keine Pfade, keine Ausflüge nach oben.

    Der Name kommt über die Schnittstelle herein und wird zu einem Pfad. Ohne
    diese Prüfung wäre ``../../etc/passwd`` ein gültiger "Aufzeichnungsname".
    """
    return bool(DATEIMUSTER.match(name))


class Aufzeichnung:
    """Schreibt mit, was hereinkommt.

    Der Schreibvorgang hängt an der Positionskette: hier zu bremsen hieße, die
    Führung zu bremsen. Deshalb wird gesammelt und in Blöcken geschrieben, und
    ein Fehler beim Schreiben beendet die Aufzeichnung, statt die Fahrt zu
    stören - eine Aufzeichnung ist ein Hilfsmittel, kein Grund anzuhalten.
    """

    def __init__(self, ordner: str | Path) -> None:
        self.ordner = Path(ordner)
        self.pfad: Optional[Path] = None
        self.begonnen_at = 0.0
        self.zeilen = 0
        self.fehler = ""
        self._datei = None
        self._puffer: list[str] = []

    @property
    def laeuft(self) -> bool:
        return self._datei is not None

    @property
    def groesse_b(self) -> int:
        if self.pfad is None or not self.pfad.exists():
            return 0
        return self.pfad.stat().st_size

    def start(self, jetzt: Optional[float] = None) -> dict:
        if self.laeuft:
            return self.status()
        jetzt = jetzt or time.time()
        self.ordner.mkdir(parents=True, exist_ok=True)
        self.pfad = self.ordner / dateiname(jetzt)
        # newline="" - unter Windows macht der Textmodus sonst aus jedem
        # Zeilenende ein CRLF, und die aufgezeichneten NMEA-Sätze tragen ihr
        # eigenes bereits mit sich.
        self._datei = open(self.pfad, "w", encoding="ascii",
                           errors="replace", newline="")
        self._puffer = []
        self.begonnen_at = jetzt
        self.zeilen = 0
        self.fehler = ""
        self._datei.write(KOPFZEILE + "\n")
        self._datei.write("# begonnen " + time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(jetzt)) + "\n")
        return self.status()

    def stop(self) -> dict:
        if self._datei is not None:
            datei, self._datei = self._datei, None
            try:
                if self._puffer:
                    datei.writelines(self._puffer)
                datei.close()
            except Exception:  # noqa: BLE001
                pass
        self._puffer = []
        return self.status()

    # -- Mitschreiben -----------------------------------------------------

    def nmea(self, zeile: str, jetzt: Optional[float] = None) -> None:
        self._schreiben("N", zeile.strip(), jetzt)

    def imu(self, lage: Attitude, jetzt: Optional[float] = None) -> None:
        yaw = "" if lage.yaw_deg is None else f"{lage.yaw_deg:.3f}"
        self._schreiben("I", f"{lage.roll_deg:.3f},{lage.pitch_deg:.3f},{yaw},"
                             f"{lage.yaw_rate_deg_s:.3f},{lage.calibration}", jetzt)

    def _schreiben(self, art: str, nutzlast: str,
                   jetzt: Optional[float] = None) -> None:
        if self._datei is None or not nutzlast:
            return
        versatz = (jetzt or time.time()) - self.begonnen_at
        self._puffer.append(f"{versatz:.3f}\t{art}\t{nutzlast}\n")
        self.zeilen += 1
        if len(self._puffer) >= SCHREIB_SAMMLUNG:
            self._leeren()

    def _leeren(self) -> None:
        if self._datei is None or not self._puffer:
            return
        try:
            self._datei.writelines(self._puffer)
            self._datei.flush()
        except Exception as exc:  # noqa: BLE001 - die Fahrt geht vor
            self.fehler = str(exc)
            self._puffer = []   # nicht beim Schließen noch einmal versuchen
            self.stop()
            return
        self._puffer = []
        if self.groesse_b > GROESSE_MAX_MB * 1024 * 1024:
            self.fehler = f"Grenze von {GROESSE_MAX_MB} MB erreicht"
            self.stop()

    # -- Auskunft ---------------------------------------------------------

    def status(self) -> dict:
        return {
            "laeuft": self.laeuft,
            "datei": self.pfad.name if self.pfad else "",
            "zeilen": self.zeilen,
            "dauer_s": (time.time() - self.begonnen_at) if self.laeuft else 0.0,
            "groesse_b": self.groesse_b,
            "fehler": self.fehler,
        }


def liste(ordner: Path) -> list[dict]:
    """Die vorhandenen Aufzeichnungen, neueste zuerst."""
    if not ordner.exists():
        return []
    eintraege = []
    for pfad in ordner.iterdir():
        if not pfad.is_file() or not ist_gueltiger_name(pfad.name):
            continue
        stat = pfad.stat()
        eintraege.append({
            "datei": pfad.name,
            "groesse_b": stat.st_size,
            "geaendert_at": stat.st_mtime,
            "dauer_s": _dauer(pfad),
        })
    return sorted(eintraege, key=lambda e: e["geaendert_at"], reverse=True)


def _dauer(pfad: Path) -> float:
    """Die Länge der Aufzeichnung - aus der letzten Zeile, nicht durch Lesen.

    Eine Datei von hundert Megabyte für eine Zahl durchzulesen wäre Unfug,
    zumal die Liste bei jedem Öffnen des Menüs geholt wird. Deshalb das letzte
    Stück vom Ende her.
    """
    try:
        with open(pfad, "rb") as datei:
            datei.seek(0, 2)
            ende = datei.tell()
            datei.seek(max(0, ende - 4096))
            letzte = datei.read().decode("ascii", errors="ignore").splitlines()
        for zeile in reversed(letzte):
            teile = zeile.split("\t")
            if len(teile) >= 2:
                return float(teile[0])
    except Exception:  # noqa: BLE001 - eine fehlende Dauer ist kein Fehler
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Abspielen
# ---------------------------------------------------------------------------


class ReplayImu(ImuSource):
    """Der Neigungssensor einer aufgezeichneten Fahrt.

    Läuft nicht selbst: gefüttert wird er von ``ReplaySource``, damit Lage und
    Position im selben Takt bleiben. Ein eigener Leser derselben Datei würde
    davonlaufen, und dann läge die Schräglage neben der Position - genau der
    Fehler, den man mit der Aufzeichnung untersuchen wollte.
    """

    def __init__(self) -> None:
        super().__init__()
        self.status = "Abspielen"

    async def run(self) -> None:
        self.running = True
        while self.running:
            await asyncio.sleep(0.5)

    def einspielen(self, roll: float, pitch: float, yaw: Optional[float],
                   yaw_rate: float, calibration: int) -> None:
        # Bewusst nicht über ``_publish``: aufgezeichnet wurde die bereits
        # genullte Lage, also genau das, womit die Rechenkette im Feld
        # gearbeitet hat. Die Nullung hier ein zweites Mal abzuziehen ergäbe
        # den doppelten Versatz - und zwar einen, den man beim Suchen nach dem
        # eigentlichen Fehler garantiert dem Sensor anlasten würde.
        self.attitude = Attitude(
            roll_deg=roll, pitch_deg=pitch, yaw_deg=yaw,
            yaw_rate_deg_s=yaw_rate, calibration=calibration,
            received_at=time.time(),
        )


class ReplaySource(GnssSource):
    """Eine aufgezeichnete Fahrt als Quelle.

    Die Sätze kommen in ihrem ursprünglichen Abstand wieder heraus, damit die
    ganze Kette dieselben Zeitschritte sieht wie im Feld. ``tempo`` verkürzt
    oder streckt diese Abstände.

    Zum Tempo eine Ehrlichkeit: bei ``tempo`` über 1 rücken die Positionen
    schneller vor, während in den Sätzen weiterhin die aufgezeichnete
    Geschwindigkeit steht. Zum Durchspulen ist das richtig; wer eine Stelle
    wirklich untersucht, stellt 1,0 ein - nur dort ist der Ablauf derselbe wie
    im Feld.
    """

    def __init__(self, on_fix: FixCallback, pfad: str | Path,
                 tempo: float = 1.0, schleife: bool = False) -> None:
        super().__init__(on_fix)
        self.pfad = Path(pfad)
        self.tempo = max(0.05, min(20.0, tempo))
        self.schleife = schleife
        self.imu = ReplayImu()
        self.position_s = 0.0
        self.durchlaeufe = 0
        # Wird gesetzt, wenn die Aufzeichnung durchgelaufen ist (oder gar nicht
        # erst gefunden wurde). Wer auf das Ende warten will, wartet hierauf.
        self.fertig = asyncio.Event()

    async def run(self) -> None:
        self.running = True
        self.fertig.clear()
        if not self.pfad.exists():
            # Kein stiller Rückfall auf den Simulator: dann stünde eine
            # erfundene Fahrt auf dem Bildschirm, während der Fahrer glaubt,
            # seine eigene zu sehen.
            self.status = f"Aufzeichnung nicht gefunden: {self.pfad}"
            self.fertig.set()
            while self.running:
                await asyncio.sleep(1.0)
            return
        while self.running:
            await self._einmal_abspielen()
            self.durchlaeufe += 1
            if not self.schleife:
                # Stehen bleiben, nicht verschwinden: "zu Ende" ist eine
                # Auskunft, "gestoppt" wäre eine andere.
                self.status = f"Aufzeichnung zu Ende ({self.pfad.name})"
                self.fertig.set()
                while self.running:
                    await asyncio.sleep(1.0)
                return
            self.fertig.set()
            # Eine Schleife über eine kurze Aufzeichnung darf nicht heißlaufen.
            await asyncio.sleep(0.1)

    async def abspielen(self) -> None:
        """Die Aufzeichnung genau einmal abspielen und dann zurückkommen.

        ``run`` bleibt danach stehen und meldet "zu Ende", weil im Betrieb eine
        Quelle, die einfach verschwindet, als "gestoppt" gelesen würde. Wer nur
        den Durchlauf will - eine Auswertung, ein Test -, nimmt diesen Weg.
        """
        self.running = True
        await self._einmal_abspielen()

    async def _einmal_abspielen(self) -> None:
        self.status = f"Abspielen: {self.pfad.name} ({self.tempo:g}×)"
        self.position_s = 0.0
        letzte = 0.0
        with open(self.pfad, "r", encoding="ascii", errors="ignore") as datei:
            for zeile in datei:
                if not self.running:
                    return
                if zeile.startswith("#") or not zeile.strip():
                    continue
                teile = zeile.rstrip("\r\n").split("\t")
                if len(teile) < 3:
                    continue
                try:
                    versatz = float(teile[0])
                except ValueError:
                    continue
                warten = (versatz - letzte) / self.tempo
                if warten > 0.0005:
                    await asyncio.sleep(min(5.0, warten))
                else:
                    # Kein Abstand zum Vorgänger - trotzdem den anderen einmal
                    # das Wort geben. Sonst hält eine dicht geschriebene oder
                    # schnell abgespielte Datei die Ereignisschleife fest, und
                    # Oberfläche wie Schnittstelle stehen still.
                    await asyncio.sleep(0)
                letzte = versatz
                self.position_s = versatz
                await self._eintrag(teile[1], teile[2])

    async def _eintrag(self, art: str, nutzlast: str) -> None:
        if art == "N":
            await self.handle_line(nutzlast)
        elif art == "I":
            felder = nutzlast.split(",")
            if len(felder) < 5:
                return
            try:
                self.imu.einspielen(
                    float(felder[0]), float(felder[1]),
                    float(felder[2]) if felder[2] else None,
                    float(felder[3]), int(felder[4]),
                )
            except ValueError:
                pass

    @property
    def healthy(self) -> bool:
        return self.running and (time.time() - self.last_line_at) < 3.0
