"""Den Sollwert ausgeben: von der Applikationskarte an die Maschine.

``applikation.py`` beantwortet die Frage *welcher Wert gilt hier?*. Dieses
Modul beantwortet die nächste: *darf er hinaus, und wohin?*

Warum das ein eigenes Modul ist und nicht drei Zeilen im Motor
--------------------------------------------------------------

Weil hier Geld liegt. Die Lenkung kann eine Maschine in den Graben fahren, und
dafür gibt es ``steering.py`` mit seinen sechs Bedingungen. Ein falscher
Sollwert fährt nichts kaputt - er legt still das Zehnfache ab, über zwanzig
Hektar, und auffallen tut es im Herbst. Beides verlangt dieselbe Bauart: lieber
ablehnen als versuchen, und jede Ablehnung mit Grund im Klartext.

Ausgegeben wird nur, wenn alles gleichzeitig stimmt:

* in der Konfiguration freigegeben - ab Werk steht hier "nur Anzeige",
* eine Applikationskarte ist geladen,
* **Markieren ist an**. Ohne laufende Arbeit wird nicht ausgebracht; auf der
  Straße und auf dem Weg zum Feld hat ein Sollwert nichts zu suchen,
* der Fix ist gut genug und frisch - ein Sprung um Dezimeter setzt an der
  Zonengrenze den falschen Wert.

Fällt eine Bedingung weg, geht der Rückfallwert hinaus, samt Grund.

Das Loch in der Karte
---------------------

Wo keine Karte gilt, gibt es keinen Sollwert - und das ist etwas anderes als
die Null. Was dann hinausgeht, ist eine Entscheidung, die niemand stillschweigend
für den Betrieb treffen sollte, deshalb steht sie in der Konfiguration:

* ``halten`` - der letzte Wert bleibt stehen. Richtig für die üblichen kleinen
  Lücken mitten im Schlag; am Feldrand streut die Maschine damit weiter.
* ``aus`` - null. Richtig am Rand; ein Loch mitten im Feld wird damit zum
  unbehandelten Fleck.
* eine Zahl - der Betriebsdurchschnitt, den der Berater ohnehin genannt hat.

Ab Werk ``halten``, weil Lücken mitten im Feld häufiger sind als Karten, die am
Rand aufhören - aber die Anzeige sagt in beiden Fällen "ohne Karte", damit der
Fahrer sieht, dass gerade kein Wert aus der Karte kommt.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

RUECKFALL_ARTEN = ("halten", "aus")

# Obergrenze für alles, was auf den Draht geht. Kein Maß, sondern eine
# Plausibilitätsschranke: die größte Einheit, die hier vorkommt, ist Stück je
# Hektar mit ein paar Millionen. Eine Milliarde ist unter keiner Einheit eine
# Ausbringmenge, sondern ein kaputter Wert - und eine 31-stellige Zahl sprengt
# das Eingabefeld jeder Gegenstelle.
WERT_MAX = 1e9

# Kleiner als das ist kein neuer Sollwert, sondern Rauschen an einer
# Zonengrenze. Ohne diese Schwelle schickt eine Rasterkarte bei jeder
# Zellgrenze einen Befehl, und ein Streuer, der jedem Zappeln folgt, streut
# ungleichmäßiger als einer, der Stufen fährt.
AENDERUNG_MIN = 0.5

# Auch ohne Änderung wird regelmäßig gesendet: die Gegenstelle hat meist einen
# Wachhund, der ohne frische Befehle abschaltet.
HERZSCHLAG_S = 1.0


def sendbar(wert: Optional[float]) -> bool:
    """Taugt diese Zahl als Sollwert für eine Maschine?

    Die eine Stelle, an der entschieden wird, was den Rechner verlassen darf.
    Eine kaputte Karte liefert ohne Weiteres ein NaN - aus einer leeren Zelle,
    aus einer Division, aus einer Umrechnung mit einem fehlenden Faktor -, und
    ``f"{nan:.2f}"`` schreibt anstandslos ``SOLL nan kg/ha`` auf den Draht. Was
    eine Gegenstelle daraus macht, weiß niemand: im besten Fall lehnt sie ab,
    im schlechtesten liest sie eine Null oder Müll.

    Negativ ist ebenso wenig ein Sollwert. Eine Maschine kann nichts
    *ent*streuen, und ein Minuszeichen an der falschen Stelle einer Karte darf
    nicht als Anweisung durchgehen.
    """
    if wert is None:
        return False
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return False
    if zahl != zahl or zahl in (float("inf"), float("-inf")):
        return False
    return 0.0 <= zahl <= WERT_MAX


def rueckfall_lesen(text: Any) -> tuple[str, Optional[float], str]:
    """Die Einstellung "Ohne Karte" auswerten.

    Liefert die Art (``halten``, ``aus`` oder ``zahl``), bei einer Zahl deren
    Wert, und einen Klartext für die Anzeige. Was nicht zu verstehen ist, wird
    zu ``halten`` - mit einem Vermerk, damit es nicht stillschweigend
    geschieht.

    Das Komma zählt wie der Punkt: die ganze Oberfläche ist deutsch, und
    ``140,5`` ist hier die naheliegendste Schreibweise. Ohne diese Zeile würde
    daraus stumm "halten", und der Fahrer suchte den Fehler bei sich.
    """
    roh = str(text if text is not None else "").strip()
    if roh == "aus":
        return "aus", 0.0, "aus (null)"
    if roh in ("", "halten"):
        return "halten", None, "halten (letzter Wert)"
    try:
        zahl = float(roh.replace(",", "."))
    except ValueError:
        return "halten", None, f"'{roh}' nicht verstanden - es gilt halten"
    if not sendbar(zahl):
        return "halten", None, f"'{roh}' ist kein Sollwert - es gilt halten"
    return "zahl", zahl, f"fest {zahl:g}"


@dataclass
class SollwertBefehl:
    """Was gerade hinausgeht - und warum."""

    aktiv: bool = False
    wert: Optional[float] = None
    einheit: str = ""
    grund: str = "nicht freigegeben"
    aus_karte: bool = False       # kam der Wert aus der Karte oder vom Rückfall?

    def to_dict(self) -> dict:
        return {
            "aktiv": self.aktiv,
            "wert": self.wert,
            "einheit": self.einheit,
            "grund": self.grund,
            "aus_karte": self.aus_karte,
        }


# ---------------------------------------------------------------------------
# Die Ausgänge
# ---------------------------------------------------------------------------


class SollwertAusgang:
    """Gemeinsame Form aller Ausgänge - und der Ausgang, der nichts tut."""

    name = "none"

    def __init__(self) -> None:
        self.ready = False
        self.status = "nicht eingerichtet"
        self.gesendet = 0
        self.letzter_fehler = ""

    async def start(self) -> None:
        self.ready = True

    async def stop(self) -> None:
        self.ready = False

    def send(self, befehl: SollwertBefehl) -> None:
        self.gesendet += 1

    def status_dict(self) -> dict:
        return {"typ": self.name, "status": self.status, "bereit": self.ready,
                "gesendet": self.gesendet, "fehler": self.letzter_fehler}


class NurAnzeige(SollwertAusgang):
    """Ab Werk: der Sollwert steht in der Kabine, es geht nichts hinaus."""

    name = "anzeige"

    async def start(self) -> None:
        self.ready = True
        self.status = "nur Anzeige (kein Ausgang)"


def satz(befehl: SollwertBefehl) -> str:
    """Der Satz, der auf den Draht geht.

    Ohne Wert geht die Null hinaus, nicht ein leeres Feld: die Gegenstelle
    bekommt immer eine Zahl, und "nichts ausbringen" ist eine klare Anweisung.
    Was hier ankommt, ist durch ``sendbar`` gegangen - eine Zahl also, keine
    Überraschung.
    """
    wert = befehl.wert if sendbar(befehl.wert) else 0.0
    return f"SOLL {wert:.2f} {befehl.einheit or '-'}"


class SeriellerAusgang(SollwertAusgang):
    """Eine Zeile je Befehl auf eine serielle Schnittstelle.

    ``SOLL <wert> <einheit>`` mit Zeilenende - so schlicht, dass sich ein
    Streuerrechner oder ein kleiner Mikrocontroller dazwischenhängen lässt,
    ohne dass jemand ein Protokoll nachbauen muss. Wer ISOBUS hat, hängt hier
    seine Brücke an; das Kabel ist derselbe USB-Seriell-Adapter, der ohnehin
    im Schrank liegt.

    Geschrieben wird in einem eigenen Faden, und ``send`` legt nur ab
    ---------------------------------------------------------------

    Ein serielles Schreiben blockiert, wenn der Puffer voll ist - weil das
    Gerät aus ist, das Kabel ab oder die Gegenstelle klemmt. Aufgerufen wird
    ``send`` aber aus der Schleife, in der auch der Empfänger gelesen und die
    Lenkung gerechnet wird; eine halbe Sekunde im ``write`` ist eine halbe
    Sekunde ohne Regelung. Bei 10 km/h sind das anderthalb Meter.

    Deshalb ein Fach mit **einem** Platz: ``send`` legt den neuesten Befehl
    hinein und kehrt sofort zurück, ein Faden schreibt ihn. Dass dabei ein
    überholter Befehl verfällt, ist kein Verlust, sondern der Punkt - ein
    Sollwert ist ein Zustand und keine Ereigniskette. Was gilt, ist der letzte.
    """

    name = "seriell"

    def __init__(self, port: str, baud: int = 38400) -> None:
        super().__init__()
        self.port = port
        self.baud = baud
        self._seriell = None
        self._fach: Optional[SollwertBefehl] = None
        self._wecker = threading.Event()
        self._schluss = threading.Event()
        self._faden: Optional[threading.Thread] = None

    async def start(self) -> None:
        try:
            import serial
        except ImportError:
            self.status = "pyserial fehlt (pip install pyserial)"
            self.ready = False
            return
        try:
            # write_timeout als zweite Sicherung: hängt das Gerät trotz allem,
            # bricht das Schreiben ab, statt den Faden für immer zu binden.
            self._seriell = serial.Serial(self.port, self.baud, timeout=0.2,
                                          write_timeout=0.5)
            self.ready = True
            self.status = f"{self.port} @ {self.baud}"
        except Exception as exc:                       # pragma: no cover - Hardware
            self.ready = False
            self.status = f"{self.port}: {exc}"
            return
        self._schluss.clear()
        self._faden = threading.Thread(target=self._schreiben, name="sollwert",
                                       daemon=True)
        self._faden.start()

    async def stop(self) -> None:
        self._schluss.set()
        self._wecker.set()
        faden, self._faden = self._faden, None
        if faden is not None:
            # Kurz warten, damit die letzte Null noch hinausgeht - aber nicht
            # länger, als ein Herunterfahren dauern darf.
            faden.join(timeout=1.0)
        self.ready = False
        if self._seriell is not None:
            try:
                self._seriell.close()
            except Exception:                          # pragma: no cover - Hardware
                pass
            self._seriell = None

    def send(self, befehl: SollwertBefehl) -> None:
        """Nur ablegen und wecken - hier wird nichts geschrieben."""
        self._fach = befehl
        self._wecker.set()

    def _schreiben(self) -> None:
        """Der Faden: nimmt, was im Fach liegt, und schreibt es."""
        while not self._schluss.is_set():
            self._wecker.wait(timeout=0.5)
            self._wecker.clear()
            befehl, self._fach = self._fach, None
            if befehl is None or self._seriell is None:
                continue
            zeile = (satz(befehl) + "\r\n").encode("ascii", "replace")
            try:
                self._seriell.write(zeile)
                self.gesendet += 1
                self.letzter_fehler = ""
            except Exception as exc:                   # pragma: no cover - Hardware
                self.letzter_fehler = str(exc)
                self.ready = False
                self.status = f"Schreibfehler: {exc}"
                return


class UdpAusgang(SollwertAusgang):
    """Derselbe Satz als UDP-Paket - für eine Steuerung im selben Netz."""

    name = "udp"

    # So oft darf ein Senden hintereinander scheitern, bevor der Ausgang als
    # gestört gilt. Ein einzelnes verlorenes Paket ist bei UDP normal; ein
    # Name, der sich nicht auflösen lässt, oder ein Netz, das weg ist, sind es
    # nicht - und dann soll in der Kabine "Ausgang gestört" stehen und nicht
    # weiter "gibt aus".
    FEHLER_BIS_GESTOERT = 5

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._socket = None
        self._fehler_am_stueck = 0

    async def start(self) -> None:
        import socket
        # Die Konfigurationsdatei ist handgeschrieben, und ein Port außerhalb
        # des Bereichs lässt sendto mit einem OverflowError platzen - der käme
        # aus der Schleife heraus, in der der Empfänger gelesen wird.
        if not (0 < int(self.port) < 65536):
            self.ready = False
            self.status = f"Port {self.port} liegt außerhalb von 1 bis 65535"
            return
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Nicht blockierend: ein voller Sendepuffer soll einen Fehler
            # geben, nicht die Schleife anhalten, in der die Lenkung rechnet.
            self._socket.setblocking(False)
            self.ready = True
            self._fehler_am_stueck = 0
            self.status = f"{self.host}:{self.port}"
        except OSError as exc:                         # pragma: no cover - Netz
            self.ready = False
            self.status = str(exc)

    async def stop(self) -> None:
        self.ready = False
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def send(self, befehl: SollwertBefehl) -> None:
        if self._socket is None or not self.ready:
            return
        try:
            self._socket.sendto(satz(befehl).encode("ascii", "replace"),
                                (self.host, self.port))
            self.gesendet += 1
            self.letzter_fehler = ""
            self._fehler_am_stueck = 0
        except Exception as exc:
            # Bewusst weit gefasst: was hier herausfliegt, fliegt bis in die
            # Schleife, die den Empfänger liest. Ein Sollwert, der nicht
            # ankommt, ist ein Ärgernis - eine Positionsverarbeitung, die
            # daran stirbt, ist ein Ausfall.
            self.letzter_fehler = str(exc)
            self._fehler_am_stueck += 1
            if self._fehler_am_stueck >= self.FEHLER_BIS_GESTOERT:
                self.ready = False
                self.status = f"{self.host}:{self.port} - {exc}"


def ausgang_bauen(config) -> SollwertAusgang:
    """Den in der Konfiguration genannten Ausgang bauen."""
    art = getattr(config, "ausgang", "anzeige")
    if art == "seriell":
        return SeriellerAusgang(config.port, config.baud)
    if art == "udp":
        return UdpAusgang(config.host, config.udp_port)
    return NurAnzeige()


# ---------------------------------------------------------------------------
# Die Entscheidung
# ---------------------------------------------------------------------------


class SollwertRegler:
    """Entscheidet, ob und mit welchem Wert ausgegeben wird."""

    def __init__(self, config, ausgang: Optional[SollwertAusgang] = None) -> None:
        self.config = config
        self.ausgang = ausgang or NurAnzeige()
        self.befehl = SollwertBefehl()
        self._letzter_wert: Optional[float] = None
        self._letzter_versand = 0.0
        self.abschaltungen = 0

    # -- Rückfall ---------------------------------------------------------

    def _rueckfall(self) -> Optional[float]:
        """Der Wert, der ohne Karte gilt - siehe der Hinweis oben im Modul."""
        art, zahl, _ = rueckfall_lesen(getattr(self.config, "rueckfall", "halten"))
        if art == "aus":
            return 0.0
        if art == "zahl":
            return zahl
        return self._letzter_wert

    # -- Hauptschleife ----------------------------------------------------

    def update(self, sollwert: Optional[float], einheit: str, fix,
               karte_aktiv: bool, arbeit_laeuft: bool,
               now: Optional[float] = None) -> SollwertBefehl:
        """Einmal je Position: was geht hinaus?"""
        now = now or time.time()
        grund = self._sperrgrund(fix, karte_aktiv, arbeit_laeuft, now)

        if grund:
            if self.befehl.aktiv:
                self.abschaltungen += 1
            # Beim Sperren geht die Null hinaus, nicht der letzte Wert: "darf
            # nicht" heißt nicht "mach weiter wie bisher". Der Rückfall gilt
            # nur für Löcher in einer Karte, die gerade wirklich ausgebracht
            # wird.
            self.befehl = SollwertBefehl(aktiv=False, wert=None, einheit=einheit,
                                         grund=grund, aus_karte=False)
            self._letzter_wert = None
            self._senden(now, erzwingen=True)
            return self.befehl

        # Ein Wert, der keiner ist, ist ein Fehler in der Karte und kein Loch
        # in ihr. Deshalb wird er nicht über den Rückfall überbrückt, sondern
        # sperrt: bei einem Loch weiß man, dass dort nichts steht; bei einem
        # NaN weiß man nur, dass die Karte kaputt ist - und dann über die
        # Stelle weiterzustreuen, wäre geraten.
        if sollwert is not None and not sendbar(sollwert):
            if self.befehl.aktiv:
                self.abschaltungen += 1
            self.befehl = SollwertBefehl(
                aktiv=False, wert=None, einheit=einheit,
                grund=f"Sollwert der Karte ist kein gültiger Wert ({sollwert!r})",
                aus_karte=False)
            self._letzter_wert = None
            self._senden(now, erzwingen=True)
            return self.befehl

        aus_karte = sollwert is not None
        wert = sollwert if aus_karte else self._rueckfall()
        if not sendbar(wert):
            wert = None
        self.befehl = SollwertBefehl(
            aktiv=wert is not None, wert=wert, einheit=einheit,
            grund="gibt aus" if aus_karte else "ohne Karte - Rückfall",
            aus_karte=aus_karte,
        )
        if aus_karte:
            self._letzter_wert = sollwert
        self._senden(now)
        return self.befehl

    def _senden(self, now: float, erzwingen: bool = False) -> None:
        """Nur bei Änderung senden - und regelmäßig, damit der Wachhund ruht.

        Eine Rasterkarte wechselt an jeder Zellgrenze; zehnmal je Sekunde einen
        Befehl zu schicken, der sich um ein Gramm unterscheidet, macht die
        Ausbringung nicht genauer, sondern den Streuer unruhig.
        """
        alt = getattr(self, "_gesendeter_wert", None)
        neu = self.befehl.wert
        geaendert = (alt is None) != (neu is None) or (
            alt is not None and neu is not None and abs(alt - neu) >= AENDERUNG_MIN)
        if erzwingen or geaendert or now - self._letzter_versand >= HERZSCHLAG_S:
            self.ausgang.send(self.befehl)
            self._gesendeter_wert = neu
            self._letzter_versand = now

    def _sperrgrund(self, fix, karte_aktiv: bool, arbeit_laeuft: bool,
                    now: float) -> Optional[str]:
        cfg = self.config
        if not getattr(cfg, "enabled", False):
            return "in der Konfiguration nicht freigegeben"
        if not self.ausgang.ready:
            return f"Ausgang nicht bereit: {self.ausgang.status}"
        if not karte_aktiv:
            return "keine Applikationskarte geladen"
        if not arbeit_laeuft:
            # Ohne Markieren wird nicht gearbeitet - und ohne Arbeit nichts
            # ausgebracht. Auf dem Weg zum Feld wäre alles andere teuer.
            return "Markieren ist aus"
        if fix is None or not fix.valid:
            return "kein GPS-Fix"
        alter = now - fix.received_at if getattr(fix, "received_at", 0) else 99.0
        if alter > 1.0:
            return f"GPS-Daten veraltet ({alter:.1f} s)"
        if getattr(cfg, "require_rtk", True) and fix.rank < 4:
            return f"RTK nötig, aktuell: {fix.fix_label}"
        return None

    # -- Aufbau und Abbau -------------------------------------------------

    async def start(self) -> None:
        await self.ausgang.start()

    async def stop(self) -> None:
        self.befehl = SollwertBefehl(grund="System wird beendet")
        self.ausgang.send(self.befehl)
        await self.ausgang.stop()

    def status(self) -> dict:
        _, _, rueckfall_text = rueckfall_lesen(
            getattr(self.config, "rueckfall", "halten"))
        return {
            "freigegeben": bool(getattr(self.config, "enabled", False)),
            # Nicht die rohe Eingabe, sondern was daraus geworden ist: wer
            # "140,5" eingetragen hat und "halten" liest, weiß sofort Bescheid.
            "rueckfall": rueckfall_text,
            "befehl": self.befehl.to_dict(),
            "ausgang": self.ausgang.status_dict(),
            "abschaltungen": self.abschaltungen,
        }
