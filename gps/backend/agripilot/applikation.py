"""Applikationskarten: teilflächenspezifisch düngen, säen, spritzen.

Eine Applikationskarte sagt für jeden Punkt des Feldes, wie viel dort
ausgebracht werden soll - 140 kg/ha auf dem guten Boden, 90 auf der Kuppe. Sie
kommt vom Berater, aus dem Satellitenbild oder aus der Ertragskarte des
Vorjahrs, und sie kommt in einem von drei Formaten:

* **Shapefile** mit einer Wertespalte - der übliche Weg aus den deutschen
  Beratungsprogrammen. Gelesen mit ``shapefile.py``, das dafür schon da ist.
* **GeoJSON** - was aus Web-Werkzeugen herausfällt.
* **ISO-XML** (``TASKDATA.XML`` plus Rasterdatei) - das Format der Terminals.

Im Feld wird daraus eine einzige Frage: *welcher Wert gilt hier?* Die muss
zehnmal je Sekunde beantwortet werden, also wird die Karte beim Laden einmal in
die lokale Meter-Ebene gerechnet (``binden``) und danach nur noch nachgeschlagen.

Warum die Zahl nicht blind übernommen wird
------------------------------------------

Beim Rasterformat steht im ISO-XML nicht die Menge, sondern eine ganze Zahl und
eine Kennung (DDI), die sagt, in welcher Einheit und mit welcher Auflösung sie
gemeint ist. Ein Faktor 100 daneben, und der Streuer legt das Hundertfache ab.

Deshalb zwei Vorkehrungen. Erstens steht der Umrechnungsfaktor in
``DDI_EINHEITEN`` offen da, mit Vermerk, welche Angabe geprüft ist und welche
angenommen. Zweitens prüft ``pruefen`` das Ergebnis gegen die Spanne, in der
sich eine Ausbringmenge bewegt: was weit daneben liegt, wird nicht abgelehnt -
die Karte kann recht haben -, aber es steht als Warnung beim Import, zusammen
mit der kleinsten, größten und mittleren Menge. Wer drei Zahlen sieht, erkennt
den Faktor 100 sofort; wer nur eine Erfolgsmeldung sieht, erkennt ihn auf dem
Feld.

Die Einheit lässt sich beim Import ausdrücklich vorgeben. Das ist der Ausweg,
wenn eine Datei eine Kennung benutzt, die hier nicht steht.
"""

from __future__ import annotations

import json
import math
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field as datenfeld
from typing import Any, Optional, Sequence

from . import shapefile
from .geo import LocalPlane, Point, point_in_polygon, polygon_area

# Gebräuchliche Einheiten. Die Karte rechnet nicht damit, sie trägt sie mit -
# gerechnet wird in der Einheit, in der die Datei geschrieben ist.
EINHEITEN = ("kg/ha", "l/ha", "1/m²", "Korn/m²", "Stück/ha", "")

# Plausible Spannen je Einheit, für die Warnung beim Import. Großzügig gefasst:
# hier soll der Faktor 100 auffallen, nicht die ungewöhnliche Kultur.
SPANNEN = {
    "kg/ha": (0.1, 2000.0),
    "l/ha": (0.1, 2000.0),
    "1/m²": (0.1, 2000.0),
    "Korn/m²": (0.1, 2000.0),
    "Stück/ha": (100.0, 2_000_000.0),
}


@dataclass(frozen=True)
class DdiEinheit:
    """Wie eine ISO-XML-Kennung in eine Menge je Hektar umgerechnet wird."""

    name: str
    einheit: str
    faktor: float      # ganze Zahl aus der Datei * faktor = Menge in `einheit`
    geprueft: bool     # steht die Auflösung nachgeschlagen fest oder ist sie angenommen?
    herkunft: str


# Die Kennungen aus dem ISO-11783-11-Wörterbuch, die auf Applikationskarten
# vorkommen.
#
# DDI 1 ist nachgeschlagen: Einheit mm³/m², Auflösung 0,01. Daraus wird l/ha
# über 1 mm³/m² = 0,01 l/ha, zusammen also Faktor 0,0001.
#
# Bei den übrigen ist die Einheit sicher (sie steht im Namen der Kennung), die
# Auflösung dagegen mit 1 angenommen. Steht in der Tabelle `geprueft=False`,
# warnt der Import ausdrücklich - und die Einheit lässt sich beim Import
# überschreiben.
DDI_EINHEITEN = {
    1: DdiEinheit("Sollwert Volumen je Fläche", "l/ha", 0.0001, True,
                  "isobus.net/isobus/dDEntity - mm³/m², Auflösung 0,01"),
    2: DdiEinheit("Istwert Volumen je Fläche", "l/ha", 0.0001, True,
                  "wie DDI 1"),
    6: DdiEinheit("Sollwert Masse je Fläche", "kg/ha", 0.01, False,
                  "mg/m² = 0,01 kg/ha; Auflösung 1 angenommen"),
    7: DdiEinheit("Istwert Masse je Fläche", "kg/ha", 0.01, False,
                  "wie DDI 6"),
    10: DdiEinheit("Sollwert Anzahl je Fläche", "1/m²", 1.0, False,
                   "Anzahl je m²; Auflösung 1 angenommen"),
    11: DdiEinheit("Istwert Anzahl je Fläche", "1/m²", 1.0, False,
                   "wie DDI 10"),
}


class KartenFehler(ValueError):
    """Die Datei wird nicht verstanden - mit Begründung."""


# ---------------------------------------------------------------------------
# Die Karte, wie sie gespeichert wird: in WGS84
# ---------------------------------------------------------------------------


@dataclass
class Zone:
    """Eine Teilfläche mit einem Sollwert, als Ring in WGS84-Grad."""

    wert: Optional[float]
    ring: list[tuple[float, float]]          # (lat, lon)
    name: str = ""

    def to_dict(self) -> dict:
        return {"wert": self.wert, "name": self.name,
                "ring": [[round(lat, 8), round(lon, 8)] for lat, lon in self.ring]}

    @classmethod
    def from_dict(cls, werte: dict) -> "Zone":
        return cls(wert=(None if werte.get("wert") is None else float(werte["wert"])),
                   ring=[(float(a), float(b)) for a, b in werte.get("ring", [])],
                   name=str(werte.get("name", "")))


@dataclass
class Raster:
    """Ein regelmäßiges Gitter von Sollwerten, verankert in WGS84-Grad.

    Gezählt wird von der Südwestecke: Zeile 0 ganz unten, Spalte 0 ganz links,
    zeilenweise nach Osten - so, wie ISO-XML die Rasterdatei schreibt.
    """

    lat_min: float
    lon_min: float
    lat_schritt: float
    lon_schritt: float
    spalten: int
    zeilen: int
    werte: list[Optional[float]]

    def wert(self, zeile: int, spalte: int) -> Optional[float]:
        if not (0 <= zeile < self.zeilen and 0 <= spalte < self.spalten):
            return None
        return self.werte[zeile * self.spalten + spalte]

    def to_dict(self) -> dict:
        return {
            "lat_min": self.lat_min, "lon_min": self.lon_min,
            "lat_schritt": self.lat_schritt, "lon_schritt": self.lon_schritt,
            "spalten": self.spalten, "zeilen": self.zeilen,
            "werte": self.werte,
        }

    @classmethod
    def from_dict(cls, werte: dict) -> "Raster":
        return cls(
            lat_min=float(werte["lat_min"]), lon_min=float(werte["lon_min"]),
            lat_schritt=float(werte["lat_schritt"]),
            lon_schritt=float(werte["lon_schritt"]),
            spalten=int(werte["spalten"]), zeilen=int(werte["zeilen"]),
            werte=[None if w is None else float(w) for w in werte["werte"]],
        )


@dataclass
class Applikationskarte:
    """Eine Karte, so wie sie aus der Datei kommt und gespeichert wird."""

    name: str
    einheit: str = "kg/ha"
    quelle: str = ""
    zonen: list[Zone] = datenfeld(default_factory=list)
    raster: Optional[Raster] = None
    standardwert: Optional[float] = None     # gilt, wo keine Zone greift
    hinweise: list[str] = datenfeld(default_factory=list)

    @property
    def art(self) -> str:
        return "raster" if self.raster is not None else "zonen"

    def werte(self) -> list[float]:
        """Alle vorkommenden Sollwerte - für Kennzahlen und Plausibilität."""
        if self.raster is not None:
            return [w for w in self.raster.werte if w is not None]
        return [z.wert for z in self.zonen if z.wert is not None]

    def spanne(self) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Kleinster, größter und mittlerer Sollwert."""
        werte = self.werte()
        if not werte:
            return None, None, None
        return min(werte), max(werte), sum(werte) / len(werte)

    def binden(self, ebene: LocalPlane) -> "LokaleKarte":
        return LokaleKarte(self, ebene)

    def to_dict(self) -> dict:
        klein, gross, mittel = self.spanne()
        return {
            "name": self.name,
            "einheit": self.einheit,
            "quelle": self.quelle,
            "art": self.art,
            "standardwert": self.standardwert,
            "hinweise": list(self.hinweise),
            "zonen": [z.to_dict() for z in self.zonen],
            "raster": self.raster.to_dict() if self.raster else None,
            "min": klein, "max": gross, "mittel": mittel,
            "zonen_anzahl": (len(self.zonen) if self.raster is None
                             else len({w for w in self.raster.werte if w is not None})),
        }

    @classmethod
    def from_dict(cls, werte: dict) -> "Applikationskarte":
        return cls(
            name=str(werte.get("name", "Karte")),
            einheit=str(werte.get("einheit", "kg/ha")),
            quelle=str(werte.get("quelle", "")),
            zonen=[Zone.from_dict(z) for z in werte.get("zonen") or []],
            raster=(Raster.from_dict(werte["raster"]) if werte.get("raster") else None),
            standardwert=(None if werte.get("standardwert") is None
                          else float(werte["standardwert"])),
            hinweise=[str(h) for h in werte.get("hinweise") or []],
        )


# ---------------------------------------------------------------------------
# Die gebundene Karte: in lokalen Metern, zum Nachschlagen im Fahren
# ---------------------------------------------------------------------------


class LokaleKarte:
    """Die Karte in der Meter-Ebene des Feldes - nur noch nachschlagen.

    Der Zonenfall bekommt je Zone ein umschließendes Rechteck vorweg: die
    Prüfung, ob ein Punkt in einem Vieleck liegt, kostet über alle Kanten, das
    Rechteck kostet vier Vergleiche und wirft die allermeisten Zonen sofort
    hinaus. Bei einer Karte mit dreißig Zonen ist das der Unterschied zwischen
    spürbar und unmerklich.
    """

    def __init__(self, karte: Applikationskarte, ebene: LocalPlane) -> None:
        self.karte = karte
        self.ebene = ebene
        self.einheit = karte.einheit
        self.standardwert = karte.standardwert

        self.zonen: list[tuple[Optional[float], list[Point],
                               tuple[float, float, float, float]]] = []
        for zone in karte.zonen:
            ring = [ebene.to_local(lat, lon) for lat, lon in zone.ring]
            if len(ring) < 3:
                continue
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            self.zonen.append((zone.wert, ring,
                               (min(xs), min(ys), max(xs), max(ys))))

        self.raster = karte.raster
        if self.raster is not None:
            r = self.raster
            ost0, nord0 = ebene.to_local(r.lat_min, r.lon_min)
            ost1, _ = ebene.to_local(r.lat_min, r.lon_min + r.lon_schritt)
            _, nord1 = ebene.to_local(r.lat_min + r.lat_schritt, r.lon_min)
            self._ursprung = (ost0, nord0)
            # Die Projektion ist über ein Feld hinweg linear, also bleibt das
            # Gitter ein Gitter: aus Grad-Schritten werden feste Meter-Schritte.
            self._schritt = (ost1 - ost0, nord1 - nord0)

    def wert_bei(self, punkt: Point) -> Optional[float]:
        """Der Sollwert an dieser Stelle, oder None außerhalb der Karte."""
        if self.raster is not None:
            ost, nord = punkt
            breite, hoehe = self._schritt
            if breite == 0.0 or hoehe == 0.0:
                return self.standardwert
            spalte = int(math.floor((ost - self._ursprung[0]) / breite))
            zeile = int(math.floor((nord - self._ursprung[1]) / hoehe))
            wert = self.raster.wert(zeile, spalte)
            return self.standardwert if wert is None else wert

        for wert, ring, (x0, y0, x1, y1) in self.zonen:
            if x0 <= punkt[0] <= x1 and y0 <= punkt[1] <= y1 and \
                    point_in_polygon(punkt, ring):
                return self.standardwert if wert is None else wert
        return self.standardwert


# ---------------------------------------------------------------------------
# Kennzahlen: wie viel wird auf diesem Feld gebraucht
# ---------------------------------------------------------------------------

# Kantenlänge der Stichprobe für die Mengenrechnung. Fünf Meter sind bei einer
# Applikationskarte, deren Zonen selten unter 20 m messen, deutlich feiner als
# die Karte selbst - und ein 20-ha-Schlag ist in 8000 Stichproben abgezählt.
PROBE_M = 5.0


def kennzahlen(lokal: LokaleKarte, grenze: Sequence[Point],
               raster_m: float = PROBE_M) -> dict:
    """Fläche und Menge je Sollwert, begrenzt auf das Feld.

    Abgezählt, nicht aus den Zonenflächen aufsummiert: eine Zone ragt fast
    immer über die Feldgrenze hinaus, und was draußen liegt, wird auch nicht
    ausgebracht. Wer die Menge bestellt, will die für sein Feld.
    """
    if len(grenze) < 3:
        return {"einheit": lokal.einheit, "zonen": [], "flaeche_ha": 0.0,
                "menge": 0.0, "ohne_wert_ha": 0.0}

    xs = [p[0] for p in grenze]
    ys = [p[1] for p in grenze]
    zellflaeche = raster_m * raster_m
    nach_wert: dict[Optional[float], float] = {}

    nord = min(ys) + raster_m / 2.0
    while nord <= max(ys):
        ost = min(xs) + raster_m / 2.0
        while ost <= max(xs):
            punkt = (ost, nord)
            if point_in_polygon(punkt, grenze):
                wert = lokal.wert_bei(punkt)
                nach_wert[wert] = nach_wert.get(wert, 0.0) + zellflaeche
            ost += raster_m
        nord += raster_m

    zonen = []
    menge = 0.0
    for wert, flaeche in sorted(((w, f) for w, f in nach_wert.items()
                                 if w is not None), key=lambda p: p[0]):
        ha = flaeche / 10_000.0
        zonen.append({"wert": wert, "flaeche_ha": round(ha, 3),
                      "menge": round(wert * ha, 1)})
        menge += wert * ha

    abgetastet = sum(nach_wert.values()) / 10_000.0
    return {
        "einheit": lokal.einheit,
        "zonen": zonen,
        # Die abgetastete Fläche weicht um den Rand einer halben Stichprobe von
        # der Feldfläche ab; ausgewiesen wird die echte, damit die Zahl neben
        # der Feldfläche steht und nicht daneben.
        "flaeche_ha": round(polygon_area(grenze) / 10_000.0, 3),
        "abgetastet_ha": round(abgetastet, 3),
        "menge": round(menge, 1),
        "ohne_wert_ha": round(nach_wert.get(None, 0.0) / 10_000.0, 3),
    }


def pruefen(karte: Applikationskarte) -> list[str]:
    """Warnungen zu einer frisch eingelesenen Karte.

    Abgelehnt wird nichts - eine ungewöhnliche Menge kann richtig sein. Aber
    sie steht dem Fahrer vor Augen, bevor der Streuer läuft.
    """
    warnungen: list[str] = []
    klein, gross, mittel = karte.spanne()
    if klein is None:
        return ["Die Karte enthält keinen einzigen Sollwert"]

    spanne = SPANNEN.get(karte.einheit)
    if spanne and (klein < spanne[0] or gross > spanne[1]):
        warnungen.append(
            f"Sollwerte von {klein:g} bis {gross:g} {karte.einheit} - "
            f"üblich sind {spanne[0]:g} bis {spanne[1]:g}. Bitte die Einheit "
            f"prüfen, bevor ausgebracht wird.")
    if gross == klein:
        warnungen.append(f"Die ganze Karte hat denselben Wert ({klein:g} "
                         f"{karte.einheit}) - eine Teilfläche ist das nicht.")
    return warnungen


# ---------------------------------------------------------------------------
# Import: Shapefile
# ---------------------------------------------------------------------------


def wertespalten(dbf: bytes) -> list[str]:
    """Spalten der .dbf, die als Sollwert taugen - also Zahlen enthalten."""
    datensaetze = shapefile.dbf_lesen(dbf)
    if not datensaetze:
        return []
    tauglich = []
    for spalte in datensaetze[0].keys():
        zahlen = 0
        for satz in datensaetze:
            if _zahl(satz.get(spalte)) is not None:
                zahlen += 1
        if zahlen >= max(1, len(datensaetze) // 2):
            tauglich.append(spalte)
    return tauglich


def _zahl(wert: Any) -> Optional[float]:
    """Aus einem .dbf-Feld eine Zahl machen - oder None."""
    if wert is None or isinstance(wert, bool):
        return None
    if isinstance(wert, (int, float)):
        return float(wert)
    text = str(wert).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def aus_shapefile(shp: bytes, dbf: bytes, prj: Optional[str] = None,
                  spalte: Optional[str] = None, name: str = "",
                  einheit: str = "kg/ha") -> Applikationskarte:
    """Eine Zonenkarte aus Shapefile plus Attributtabelle.

    Ohne ``spalte`` wird die erste Spalte genommen, die durchgehend Zahlen
    enthält. Das trifft meistens, aber nicht immer - deshalb liefert
    ``wertespalten`` die Auswahl, aus der die Oberfläche wählen lässt.
    """
    if not dbf:
        raise KartenFehler("Für eine Applikationskarte wird die .dbf mit den "
                           "Sollwerten gebraucht - ohne sie ist es nur eine Fläche")
    umrisse = shapefile.lesen(shp, dbf, prj)
    if spalte is None:
        moeglich = wertespalten(dbf)
        if not moeglich:
            raise KartenFehler("Keine Spalte der .dbf enthält Zahlen - "
                               "welche soll der Sollwert sein?")
        spalte = moeglich[0]

    zonen = []
    for umriss in umrisse:
        wert = _zahl(umriss.attribute.get(spalte))
        zonen.append(Zone(wert=wert, ring=list(umriss.ring), name=umriss.name))
    if not any(z.wert is not None for z in zonen):
        raise KartenFehler(f"In der Spalte '{spalte}' steht keine einzige Zahl")

    karte = Applikationskarte(
        name=name or f"Applikationskarte ({spalte})",
        einheit=einheit,
        quelle=f"Shapefile, Spalte {spalte}",
        zonen=zonen,
    )
    karte.hinweise = pruefen(karte)
    return karte


# ---------------------------------------------------------------------------
# Import: GeoJSON
# ---------------------------------------------------------------------------


def aus_geojson(text: str, feldname: Optional[str] = None, name: str = "",
                einheit: str = "kg/ha") -> Applikationskarte:
    """Eine Zonenkarte aus GeoJSON.

    GeoJSON ist immer WGS84 und schreibt die Koordinaten als [lon, lat] - genau
    andersherum als alles andere hier. Genau daran gehen Importe kaputt, und
    zwar lautlos: die Karte liegt dann irgendwo im Indischen Ozean, und der
    Sollwert ist überall None.
    """
    try:
        daten = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise KartenFehler(f"Kein lesbares GeoJSON: {exc}") from exc

    merkmale = (daten.get("features") if isinstance(daten, dict) else None)
    if merkmale is None:
        raise KartenFehler("Erwartet wird eine FeatureCollection mit 'features'")

    zonen: list[Zone] = []
    for merkmal in merkmale:
        geometrie = (merkmal or {}).get("geometry") or {}
        eigenschaften = (merkmal or {}).get("properties") or {}
        typ = geometrie.get("type")
        if typ == "Polygon":
            ringe = [geometrie.get("coordinates", [[]])[0]]
        elif typ == "MultiPolygon":
            ringe = [teil[0] for teil in geometrie.get("coordinates", []) if teil]
        else:
            continue

        if feldname is None:
            feldname = _erste_zahlenspalte(eigenschaften)
        wert = _zahl(eigenschaften.get(feldname)) if feldname else None
        for ring in ringe:
            punkte = [(float(paar[1]), float(paar[0])) for paar in ring
                      if isinstance(paar, (list, tuple)) and len(paar) >= 2]
            if len(punkte) >= 2 and punkte[0] == punkte[-1]:
                punkte = punkte[:-1]
            if len(punkte) >= 3:
                zonen.append(Zone(wert=wert, ring=punkte,
                                  name=str(eigenschaften.get("name", ""))))

    if not zonen:
        raise KartenFehler("Keine Fläche im GeoJSON gefunden")
    if not any(z.wert is not None for z in zonen):
        raise KartenFehler("Keine Eigenschaft mit Zahlen gefunden - "
                           "welcher Wert soll der Sollwert sein?")

    karte = Applikationskarte(
        name=name or "Applikationskarte",
        einheit=einheit,
        quelle=f"GeoJSON, Eigenschaft {feldname}",
        zonen=zonen,
    )
    karte.hinweise = pruefen(karte)
    return karte


def _erste_zahlenspalte(eigenschaften: dict) -> Optional[str]:
    for schluessel, wert in eigenschaften.items():
        if _zahl(wert) is not None:
            return schluessel
    return None


# ---------------------------------------------------------------------------
# Import: ISO-XML
# ---------------------------------------------------------------------------


def aus_isoxml(taskdata: bytes, raster_dateien: dict[str, bytes],
               name: str = "", einheit: Optional[str] = None,
               aufgabe: Optional[str] = None) -> Applikationskarte:
    """Eine Rasterkarte aus ``TASKDATA.XML`` und der zugehörigen Rasterdatei.

    ISO-XML kennt zwei Rasterarten, und beide kommen vor:

    * **Art 1** - ein Byte je Zelle, und das Byte nennt die Behandlungszone.
      Der Wert steht dann im ``TZN``-Eintrag dieser Zone.
    * **Art 2** - vier Bytes je Zelle, und die sind der Wert selbst. Welche
      Kennung (DDI) gemeint ist, sagt die im ``GRD`` genannte Zone.

    Gezählt wird von der Südwestecke zeilenweise nach Osten. Die Null bedeutet
    in beiden Arten "hier nicht ausbringen" und wird zu ``None``, nicht zu 0 -
    ein Loch in der Karte ist etwas anderes als die Anweisung, nichts abzulegen,
    auch wenn der Streuer am Ende dasselbe tut.
    """
    try:
        wurzel = ET.fromstring(taskdata)
    except ET.ParseError as exc:
        raise KartenFehler(f"TASKDATA.XML ist nicht lesbar: {exc}") from exc

    aufgaben = wurzel.findall(".//TSK")
    if not aufgaben:
        raise KartenFehler("Keine Aufgabe (TSK) in der TASKDATA.XML")
    gewaehlt = None
    for tsk in aufgaben:
        if tsk.find("GRD") is None:
            continue
        if aufgabe is None or tsk.get("A") == aufgabe or tsk.get("B") == aufgabe:
            gewaehlt = tsk
            break
    if gewaehlt is None:
        raise KartenFehler("Keine Aufgabe mit einem Raster (GRD) gefunden")

    grd = gewaehlt.find("GRD")
    try:
        lat_min = float(grd.get("A"))
        lon_min = float(grd.get("B"))
        lat_schritt = float(grd.get("C"))
        lon_schritt = float(grd.get("D"))
        spalten = int(grd.get("E"))
        zeilen = int(grd.get("F"))
        art = int(grd.get("H", "1"))
    except (TypeError, ValueError) as exc:
        raise KartenFehler(f"Das Raster (GRD) ist unvollständig: {exc}") from exc

    if spalten <= 0 or zeilen <= 0:
        raise KartenFehler("Das Raster hat keine Zellen")
    if spalten * zeilen > 2_000_000:
        raise KartenFehler(f"Das Raster hat {spalten * zeilen} Zellen - "
                           "das ist keine Applikationskarte für ein Feld")

    rohdaten = _rasterdatei(grd.get("G"), raster_dateien)
    zonenwerte, ddi_je_zone = _behandlungszonen(gewaehlt)

    if art == 1:
        werte, ddi = _raster_art1(rohdaten, spalten, zeilen, zonenwerte, ddi_je_zone)
    elif art == 2:
        zone = grd.get("I")
        ddi = ddi_je_zone.get(zone) or (next(iter(ddi_je_zone.values()), None))
        werte = _raster_art2(rohdaten, spalten, zeilen)
    else:
        raise KartenFehler(f"Rasterart {art} wird nicht verstanden - "
                           "bekannt sind 1 (Zonennummer) und 2 (Wert je Zelle)")

    hinweise: list[str] = []
    kennung = DDI_EINHEITEN.get(ddi) if ddi is not None else None
    if einheit is not None:
        gewaehlte_einheit, faktor = einheit, 1.0
        hinweise.append(f"Einheit auf '{einheit}' vorgegeben, "
                        "die Zahlen der Datei bleiben unverändert.")
    elif kennung is not None:
        gewaehlte_einheit, faktor = kennung.einheit, kennung.faktor
        if not kennung.geprueft:
            hinweise.append(
                f"DDI {ddi} ({kennung.name}): umgerechnet mit Faktor "
                f"{kennung.faktor:g} nach {kennung.einheit} - {kennung.herkunft}. "
                "Bitte die Mengen unten gegen die Karte des Beraters halten.")
    else:
        gewaehlte_einheit, faktor = "", 1.0
        hinweise.append(
            f"Kennung DDI {ddi} ist hier nicht hinterlegt - die Zahlen stehen "
            "unverändert aus der Datei. Einheit beim Import vorgeben.")

    if faktor != 1.0:
        werte = [None if w is None else w * faktor for w in werte]

    karte = Applikationskarte(
        name=name or (gewaehlt.get("B") or "Applikationskarte"),
        einheit=gewaehlte_einheit,
        quelle=f"ISO-XML, Raster Art {art}" + (f", DDI {ddi}" if ddi else ""),
        raster=Raster(lat_min=lat_min, lon_min=lon_min,
                      lat_schritt=lat_schritt, lon_schritt=lon_schritt,
                      spalten=spalten, zeilen=zeilen, werte=werte),
        hinweise=hinweise,
    )
    karte.hinweise = hinweise + pruefen(karte)
    return karte


def _rasterdatei(dateiname: Optional[str],
                 dateien: dict[str, bytes]) -> bytes:
    """Die Rasterdatei heraussuchen - Groß- und Kleinschreibung ist egal.

    Im ``GRD`` steht der Name ohne Endung (``GRD00001``), auf dem Datenträger
    liegt ``GRD00001.BIN``. Je nach Terminal auch ``.bin``, und je nach
    Betriebssystem, das die Karte kopiert hat, komplett kleingeschrieben.
    """
    if not dateiname:
        raise KartenFehler("Im Raster (GRD) fehlt der Dateiname")
    gesucht = dateiname.upper()
    for schluessel, inhalt in dateien.items():
        kurz = schluessel.replace("\\", "/").split("/")[-1].upper()
        if kurz in (gesucht, gesucht + ".BIN") or kurz.split(".")[0] == gesucht:
            return inhalt
    raise KartenFehler(f"Die Rasterdatei '{dateiname}.BIN' fehlt - "
                       "mit der TASKDATA.XML zusammen auswählen")


def _behandlungszonen(tsk: ET.Element) -> tuple[dict[str, Optional[float]],
                                                dict[str, Optional[int]]]:
    """Sollwert und Kennung je Behandlungszone (TZN) der Aufgabe."""
    werte: dict[str, Optional[float]] = {}
    ddis: dict[str, Optional[int]] = {}
    for tzn in tsk.findall("TZN"):
        kennung = tzn.get("A")
        if kennung is None:
            continue
        pdv = tzn.find("PDV")
        if pdv is None:
            werte[kennung] = None
            continue
        werte[kennung] = _zahl(pdv.get("B"))
        ddis[kennung] = _ddi(pdv.get("A"))
    return werte, ddis


def _ddi(text: Optional[str]) -> Optional[int]:
    """Die Kennung aus dem ``PDV`` - hexadezimal, wie ISO-XML sie schreibt.

    ``"0006"`` ist DDI 6, ``"000A"`` ist DDI 10. Bis neun sind beide Lesarten
    gleich, ab zehn nicht mehr: ``"0010"`` sind sechzehn, nicht zehn.

    Hexadezimal zu lesen ist nicht nur das, was der Standard vorschreibt,
    sondern auch die Lesart, die im Zweifel sicher scheitert. Wer ``"0010"``
    dezimal läse, bekäme DDI 10 - eine Kennung, die in ``DDI_EINHEITEN`` steht
    und still mit deren Einheit umgerechnet würde. Hexadezimal gelesen kommt
    sechzehn heraus, die dort nicht steht, und der Import sagt das.
    """
    if not text:
        return None
    try:
        return int(text.strip(), 16)
    except ValueError:
        return None


def _raster_art1(rohdaten: bytes, spalten: int, zeilen: int,
                 zonenwerte: dict[str, Optional[float]],
                 ddi_je_zone: dict[str, Optional[int]]
                 ) -> tuple[list[Optional[float]], Optional[int]]:
    """Ein Byte je Zelle: die Nummer der Behandlungszone."""
    erwartet = spalten * zeilen
    if len(rohdaten) < erwartet:
        raise KartenFehler(f"Die Rasterdatei ist zu kurz: {len(rohdaten)} Bytes "
                           f"für {erwartet} Zellen")
    werte: list[Optional[float]] = []
    for byte in rohdaten[:erwartet]:
        if byte == 0:
            werte.append(None)
        else:
            werte.append(zonenwerte.get(str(byte)))
    ddi = next((d for d in ddi_je_zone.values() if d is not None), None)
    return werte, ddi


def _raster_art2(rohdaten: bytes, spalten: int,
                 zeilen: int) -> list[Optional[float]]:
    """Vier Bytes je Zelle, kleinstes Byte zuerst: der Wert selbst."""
    erwartet = spalten * zeilen
    if len(rohdaten) < erwartet * 4:
        raise KartenFehler(f"Die Rasterdatei ist zu kurz: {len(rohdaten)} Bytes "
                           f"für {erwartet} Zellen à 4 Bytes")
    zahlen = struct.unpack_from(f"<{erwartet}i", rohdaten, 0)
    return [None if z == 0 else float(z) for z in zahlen]


# ---------------------------------------------------------------------------
# Dokumentation: was wirklich ausgebracht wurde
# ---------------------------------------------------------------------------


@dataclass
class Ausbringung:
    """Mitschrift der Ausbringung, Fläche je Sollwert.

    Gebucht wird beim Fahren: für jede neu bearbeitete Zelle der bearbeiteten
    Fläche wird nachgeschlagen, welcher Sollwert dort galt, und die Fläche auf
    diesen Wert gebucht. Am Ende steht, auf wie viel Hektar welche Menge lag -
    und daraus die Gesamtmenge.

    Was hier steht, ist ehrlich beschränkt: das System weiß, wo die Maschine
    gefahren ist und was die Karte an dieser Stelle verlangt hat. Ob der Streuer
    die Menge auch wirklich abgelegt hat, weiß es nur, wenn die Maschine es
    zurückmeldet - dann wird der gemeldete Istwert gebucht statt des Sollwerts,
    und ``rueckmeldung`` sagt, dass es so war. Ohne Rückmeldung ist das hier
    eine Soll-Dokumentation, und sie heißt in der Ausgabe auch so.
    """

    einheit: str = ""
    nach_wert: dict[float, float] = datenfeld(default_factory=dict)  # Wert -> m²
    ohne_wert_m2: float = 0.0
    rueckmeldung: bool = False

    def buchen(self, wert: Optional[float], flaeche_m2: float,
               ist_wert: Optional[float] = None) -> None:
        """Eine bearbeitete Fläche auf ihren Sollwert buchen."""
        if flaeche_m2 <= 0.0:
            return
        if ist_wert is not None:
            self.rueckmeldung = True
            wert = ist_wert
        if wert is None:
            self.ohne_wert_m2 += flaeche_m2
            return
        self.nach_wert[wert] = self.nach_wert.get(wert, 0.0) + flaeche_m2

    @property
    def flaeche_ha(self) -> float:
        return sum(self.nach_wert.values()) / 10_000.0

    @property
    def menge(self) -> float:
        return sum(wert * flaeche / 10_000.0
                   for wert, flaeche in self.nach_wert.items())

    @property
    def mittelwert(self) -> Optional[float]:
        flaeche = self.flaeche_ha
        return self.menge / flaeche if flaeche > 0 else None

    def to_dict(self) -> dict:
        return {
            "einheit": self.einheit,
            "art": "Istwert der Maschine" if self.rueckmeldung else "Sollwert der Karte",
            "rueckmeldung": self.rueckmeldung,
            "zonen": [{"wert": wert, "flaeche_ha": round(flaeche / 10_000.0, 3),
                       "menge": round(wert * flaeche / 10_000.0, 1)}
                      for wert, flaeche in sorted(self.nach_wert.items())],
            "flaeche_ha": round(self.flaeche_ha, 3),
            "menge": round(self.menge, 1),
            "mittelwert": (round(self.mittelwert, 1)
                           if self.mittelwert is not None else None),
            "ohne_karte_ha": round(self.ohne_wert_m2 / 10_000.0, 3),
        }

    @classmethod
    def from_dict(cls, werte: Optional[dict]) -> "Ausbringung":
        werte = dict(werte or {})
        nach_wert = {}
        for eintrag in werte.get("zonen") or []:
            nach_wert[float(eintrag["wert"])] = float(eintrag["flaeche_ha"]) * 10_000.0
        return cls(
            einheit=str(werte.get("einheit", "")),
            nach_wert=nach_wert,
            ohne_wert_m2=float(werte.get("ohne_karte_ha", 0.0)) * 10_000.0,
            rueckmeldung=bool(werte.get("rueckmeldung", False)),
        )


def abgleich(geplant: dict, ausgebracht: Ausbringung) -> dict:
    """Soll aus der Karte gegen Ist aus der Fahrt.

    ``geplant`` ist das Ergebnis von ``kennzahlen``. Verglichen werden Fläche
    und Menge; die Abweichung in Prozent ist die Zahl, nach der in der
    Schlagkartei gefragt wird.
    """
    soll_menge = float(geplant.get("menge") or 0.0)
    ist_menge = ausgebracht.menge
    soll_flaeche = float(geplant.get("flaeche_ha") or 0.0)
    ist_flaeche = ausgebracht.flaeche_ha
    return {
        "einheit": ausgebracht.einheit or geplant.get("einheit", ""),
        "soll_menge": round(soll_menge, 1),
        "ist_menge": round(ist_menge, 1),
        "soll_flaeche_ha": round(soll_flaeche, 3),
        "ist_flaeche_ha": round(ist_flaeche, 3),
        "menge_abweichung": round(ist_menge - soll_menge, 1),
        "menge_abweichung_prozent": (round((ist_menge - soll_menge) / soll_menge * 100.0, 1)
                                     if soll_menge > 0 else None),
        "flaeche_abweichung_ha": round(ist_flaeche - soll_flaeche, 3),
        "ohne_karte_ha": round(ausgebracht.ohne_wert_m2 / 10_000.0, 3),
    }


def ausbringung_csv(ausgebracht: Ausbringung, feldname: str = "",
                    kartenname: str = "") -> str:
    """Die Ausbringung als CSV für die Schlagkartei."""
    zeilen = [f"Feld;{feldname}", f"Karte;{kartenname}",
              f"Grundlage;{ausgebracht.to_dict()['art']}", ""]
    zeilen.append(f"Sollwert [{ausgebracht.einheit}];Flaeche [ha];Menge")
    for wert, flaeche in sorted(ausgebracht.nach_wert.items()):
        ha = flaeche / 10_000.0
        zeilen.append(f"{wert:g};{ha:.3f};{wert * ha:.1f}".replace(".", ","))
    if ausgebracht.ohne_wert_m2 > 0:
        zeilen.append(
            f"ohne Karte;{ausgebracht.ohne_wert_m2 / 10_000.0:.3f};".replace(".", ","))
    zeilen.append("")
    zeilen.append(f"Summe;{ausgebracht.flaeche_ha:.3f};{ausgebracht.menge:.1f}"
                  .replace(".", ","))
    return "\r\n".join(zeilen) + "\r\n"
