"""NTRIP und RTCM3 lesen: was der Caster antwortet, und ob im Strom wirklich Korrekturen stecken.

Hier ist alles rein (Bytes rein, Ergebnis raus), damit es sich ohne Netz prüfen
lässt. Drei Dinge, an denen RTK still scheitert, ohne dass eine Fehlermeldung
kommt:

* **NTRIP 2.0 schickt "chunked".** Wer nach Ntrip/2.0 fragt, bekommt die Daten
  oft in Stücken mit vorangestellter Länge. Reicht man die Bytes ungeprüft
  weiter, stehen die Längenangaben mitten im RTCM-Strom, und der Empfänger
  verwirft die Nachrichten - es kommen Bytes an, aber nie ein Fix.
* **Eine Mountpoint-Liste ist kein Erfolg.** Kennt der Caster den Mountpoint
  nicht, antwortet er mit "SOURCETABLE 200 OK". Wer nur auf "200" schaut,
  hält das für eine Verbindung und schickt die Liste als Text an den Empfänger.
* **Bytes sind keine Korrekturen.** Erst ein RTCM3-Rahmen mit stimmender
  Prüfsumme zählt. Die Basisposition aus 1005/1006 zeigt zusätzlich, wie weit
  die Basis entfernt ist - über etwa 30 km wird RTK unzuverlässig.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# --- Antwort des Casters ----------------------------------------------------


class CasterFehler(ConnectionError):
    """Der Caster hat abgelehnt - mit einem Satz, den man in der Kabine versteht."""


@dataclass
class Antwort:
    ok: bool
    chunked: bool = False
    version: str = ""            # "1" bei ICY, "2" bei HTTP
    fehler: str = ""
    sourcetable: bool = False


def antwort_pruefen(statuszeile: str, kopf: dict[str, str]) -> Antwort:
    """Statuszeile und Kopfzeilen des Casters deuten.

    `kopf` mit kleingeschriebenen Schlüsseln.
    """
    zeile = (statuszeile or "").strip()
    teile = zeile.split()
    if zeile.upper().startswith("ICY 200"):
        return Antwort(ok=True, version="1")
    if zeile.upper().startswith("SOURCETABLE"):
        return Antwort(ok=False, sourcetable=True, fehler=(
            "Mountpoint unbekannt - der Caster schickt nur seine Liste. "
            "Mountpoint über „Mountpoints abrufen“ auswählen."))
    code = teile[1] if len(teile) > 1 and teile[0].upper().startswith("HTTP") else ""
    if code == "200":
        if "sourcetable" in kopf.get("content-type", "").lower():
            return Antwort(ok=False, sourcetable=True, version="2", fehler=(
                "Mountpoint unbekannt - der Caster schickt nur seine Liste. "
                "Mountpoint über „Mountpoints abrufen“ auswählen."))
        chunked = "chunked" in kopf.get("transfer-encoding", "").lower()
        return Antwort(ok=True, chunked=chunked, version="2")
    gruende = {
        "401": "Benutzer oder Passwort falsch (401).",
        "403": "Zugang gesperrt oder für diesen Mountpoint nicht freigeschaltet (403).",
        "404": "Diesen Mountpoint gibt es auf dem Caster nicht (404).",
        "409": "Mit diesem Zugang ist schon jemand verbunden (409) - meist ein zweites Gerät.",
        "503": "Caster überlastet oder Basis gerade ohne Daten (503).",
    }
    if code in gruende:
        return Antwort(ok=False, fehler=gruende[code])
    return Antwort(ok=False, fehler=f"Unerwartete Antwort des Casters: {zeile or '(leer)'}")


def kopf_zerlegen(roh: bytes) -> tuple[str, dict[str, str]]:
    """Den Antwortkopf (bis zur Leerzeile, ohne sie) in Statuszeile und Felder teilen."""
    zeilen = roh.decode("latin-1").split("\r\n") if b"\r\n" in roh else roh.decode("latin-1").split("\n")
    status = zeilen[0].strip() if zeilen else ""
    kopf: dict[str, str] = {}
    for zeile in zeilen[1:]:
        if ":" in zeile:
            name, wert = zeile.split(":", 1)
            kopf[name.strip().lower()] = wert.strip()
    return status, kopf


def anfrage(host: str, port: int, mountpoint: str, benutzer: str = "", passwort: str = "") -> bytes:
    """Die GET-Anfrage nach NTRIP 2.0 (verstehen auch die meisten 1.0-Caster)."""
    import base64

    zeilen = [
        f"GET /{mountpoint} HTTP/1.1",
        f"Host: {host}:{port}",
        "Ntrip-Version: Ntrip/2.0",
        "User-Agent: NTRIP AgriPilot/1.0",
    ]
    if benutzer or passwort:
        zugang = base64.b64encode(f"{benutzer}:{passwort}".encode()).decode()
        zeilen.append(f"Authorization: Basic {zugang}")
    zeilen.append("Connection: close")
    return ("\r\n".join(zeilen) + "\r\n\r\n").encode()


class ChunkedDecoder:
    """Packt "Transfer-Encoding: chunked" Stück für Stück aus.

    Die Stücke kommen beliebig zerteilt über das Netz; der Decoder merkt sich,
    wo er stehen geblieben ist.
    """

    def __init__(self) -> None:
        self._puffer = b""
        self._rest = 0          # noch zu lesende Nutzbytes im laufenden Stück
        self._crlf = False      # nach einem Stück folgt CRLF
        self.ende = False

    def feed(self, daten: bytes) -> bytes:
        self._puffer += daten
        heraus = bytearray()
        while self._puffer and not self.ende:
            if self._crlf:
                if len(self._puffer) < 2:
                    break
                self._puffer = self._puffer[2:]
                self._crlf = False
                continue
            if self._rest:
                stueck = self._puffer[:self._rest]
                heraus += stueck
                self._puffer = self._puffer[len(stueck):]
                self._rest -= len(stueck)
                if not self._rest:
                    self._crlf = True
                continue
            zeilenende = self._puffer.find(b"\r\n")
            if zeilenende < 0:
                if len(self._puffer) > 64:
                    raise CasterFehler("Kaputte Stücklänge im Datenstrom des Casters")
                break
            groesse = self._puffer[:zeilenende].split(b";")[0].strip()
            try:
                self._rest = int(groesse, 16)
            except ValueError as exc:
                raise CasterFehler("Kaputte Stücklänge im Datenstrom des Casters") from exc
            self._puffer = self._puffer[zeilenende + 2:]
            if self._rest == 0:
                self.ende = True
        return bytes(heraus)


# --- RTCM3 ------------------------------------------------------------------

def _crc24q(daten: bytes) -> int:
    crc = 0
    for byte in daten:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def rtcm_rahmen(nutzdaten: bytes) -> bytes:
    """Einen RTCM3-Rahmen bauen (für Tests und den Simulator)."""
    kopf = bytes([0xD3, (len(nutzdaten) >> 8) & 0x03, len(nutzdaten) & 0xFF])
    crc = _crc24q(kopf + nutzdaten)
    return kopf + nutzdaten + crc.to_bytes(3, "big")


def _bits(daten: bytes, start: int, laenge: int, vorzeichen: bool = False) -> int:
    wert = 0
    for i in range(start, start + laenge):
        wert = (wert << 1) | ((daten[i // 8] >> (7 - i % 8)) & 1)
    if vorzeichen and wert & (1 << (laenge - 1)):
        wert -= 1 << laenge
    return wert


def ecef_zu_wgs84(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Kartesische Erdkoordinaten (m) in Breite, Länge (Grad) und Höhe (m)."""
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    hoehe = 0.0
    for _ in range(6):
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        hoehe = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - e2 * n / (n + hoehe)))
    return math.degrees(lat), math.degrees(lon), hoehe


def entfernung_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# Welche Nachrichten wofür stehen - für die Anzeige, nicht zur Auswertung.
SYSTEME = {
    range(1001, 1005): "GPS", range(1009, 1013): "GLONASS",
    range(1071, 1078): "GPS", range(1081, 1088): "GLONASS",
    range(1091, 1098): "Galileo", range(1121, 1128): "BeiDou",
    range(1111, 1118): "QZSS",
}


def system_von(typ: int) -> str:
    for bereich, name in SYSTEME.items():
        if typ in bereich:
            return name
    return ""


@dataclass
class RtcmPruefer:
    """Liest mit, was durch die Leitung geht: gültige Rahmen, Typen, Basis."""

    rahmen: int = 0
    crc_fehler: int = 0
    fremde_bytes: int = 0
    typen: dict[int, int] = field(default_factory=dict)
    station: Optional[int] = None
    basis: Optional[tuple[float, float, float]] = None   # lat, lon, Höhe
    letzter_rahmen_at: float = 0.0
    _puffer: bytes = b""

    MAX_PUFFER = 4096

    def feed(self, daten: bytes, jetzt: float = 0.0) -> None:
        puffer = self._puffer + daten
        pos = 0
        while True:
            start = puffer.find(b"\xd3", pos)
            if start < 0:
                self.fremde_bytes += len(puffer) - pos
                pos = len(puffer)
                break
            self.fremde_bytes += start - pos
            if len(puffer) - start < 3:
                pos = start
                break
            laenge = ((puffer[start + 1] & 0x03) << 8) | puffer[start + 2]
            ende = start + 3 + laenge + 3
            if puffer[start + 1] & 0xFC or laenge == 0:
                self.fremde_bytes += 1
                pos = start + 1
                continue
            if len(puffer) < ende:
                pos = start
                break
            rahmen = puffer[start:ende]
            if _crc24q(rahmen[:-3]) != int.from_bytes(rahmen[-3:], "big"):
                self.crc_fehler += 1
                pos = start + 1
                continue
            self._rahmen(rahmen[3:-3], jetzt)
            pos = ende
        self._puffer = puffer[pos:][-self.MAX_PUFFER:]

    def _rahmen(self, nutz: bytes, jetzt: float) -> None:
        self.rahmen += 1
        self.letzter_rahmen_at = jetzt
        if len(nutz) < 2:
            return
        typ = _bits(nutz, 0, 12)
        self.typen[typ] = self.typen.get(typ, 0) + 1
        if typ in (1005, 1006) and len(nutz) >= 19:
            self.station = _bits(nutz, 12, 12)
            x = _bits(nutz, 34, 38, True) * 1e-4
            y = _bits(nutz, 74, 38, True) * 1e-4
            z = _bits(nutz, 114, 38, True) * 1e-4
            if x or y or z:
                self.basis = ecef_zu_wgs84(x, y, z)

    @property
    def systeme(self) -> list[str]:
        return sorted({s for s in (system_von(t) for t in self.typen) if s})

    def zusammenfassung(self, position: Optional[tuple[float, float]] = None) -> dict:
        abstand = None
        if self.basis and position:
            abstand = round(entfernung_km(position[0], position[1], self.basis[0], self.basis[1]), 1)
        return {
            "rahmen": self.rahmen,
            "crc_fehler": self.crc_fehler,
            "fremde_bytes": self.fremde_bytes,
            "typen": sorted(self.typen),
            "systeme": self.systeme,
            "station": self.station,
            "basis": ([round(self.basis[0], 7), round(self.basis[1], 7), round(self.basis[2], 2)]
                      if self.basis else None),
            "basis_km": abstand,
        }


def rtcm_urteil(zusammenfassung: dict, empfangene_bytes: int) -> str:
    """Ein Satz, ob der Strom taugt - leer, wenn alles in Ordnung ist."""
    if not empfangene_bytes:
        return "Noch keine Daten vom Caster."
    if not zusammenfassung["rahmen"]:
        return ("Es kommen Daten an, aber keine RTCM3-Korrekturen - falscher Mountpoint "
                "oder ein anderes Format (z. B. RTCM2/CMR).")
    if not zusammenfassung["systeme"]:
        return "RTCM3 kommt an, aber ohne Beobachtungen (nur Stationsdaten)."
    if 1005 not in zusammenfassung["typen"] and 1006 not in zusammenfassung["typen"]:
        return "Beobachtungen kommen an, aber noch keine Basisposition (1005/1006) - kurz warten."
    km = zusammenfassung["basis_km"]
    if km is not None and km > 30:
        return f"Die Basis ist {km:.0f} km entfernt - über etwa 30 km wird RTK unzuverlässig."
    return ""


# --- Mountpoint-Liste ------------------------------------------------------

def sourcetable_lesen(text: str, position: Optional[tuple[float, float]] = None) -> list[dict]:
    """Die STR-Zeilen einer Sourcetable als Liste; nächste Basis zuerst, wenn die Position bekannt ist."""
    liste = []
    for zeile in text.splitlines():
        teile = zeile.strip().split(";")
        if len(teile) < 11 or teile[0] != "STR":
            continue

        def zahl(wert: str) -> Optional[float]:
            try:
                return float(wert)
            except ValueError:
                return None

        lat, lon = zahl(teile[9]), zahl(teile[10])
        eintrag = {
            "mountpoint": teile[1],
            "name": teile[2],
            "format": teile[3],
            "details": teile[4] if len(teile) > 4 else "",
            "systeme": teile[6] if len(teile) > 6 else "",
            "land": teile[8] if len(teile) > 8 else "",
            "lat": lat,
            "lon": lon,
            "nmea": len(teile) > 11 and teile[11] == "1",
            "netz": len(teile) > 12 and teile[12] == "1",
            "anmeldung": teile[15] if len(teile) > 15 else "",
            "gebuehr": len(teile) > 16 and teile[16] == "Y",
            "km": None,
        }
        if position and lat is not None and lon is not None and (lat or lon):
            eintrag["km"] = round(entfernung_km(position[0], position[1], lat, lon), 1)
        liste.append(eintrag)
    if position:
        liste.sort(key=lambda e: (e["km"] is None, e["km"] or 0.0, e["mountpoint"]))
    return liste
