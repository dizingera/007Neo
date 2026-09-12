"""Feldgrenzen aus Shapefiles lesen - ohne Zusatzpakete.

Die Flächen kommen aus dem Flächenantrag, aus dem Flurstückskataster oder aus
einem anderen Lenksystem, und sie kommen als Shapefile: drei Dateien, ``.shp``
mit der Geometrie, ``.dbf`` mit den Namen, ``.prj`` mit dem Koordinatensystem.

Gelesen wird hier von Hand. GeoPandas brächte auf dem Pi ohne Internet ein
halbes Dutzend Pakete mit, für ein Dateiformat aus dem Jahr 1998, dessen
Beschreibung auf zwölf Seiten passt. Was hier nicht verstanden wird, wird
klar abgelehnt - eine Grenze, die um zweihundert Meter daneben liegt, weil
ein Datum still falsch geraten wurde, ist schlimmer als keine.

Verstanden werden Polygone (Typ 5, 15 und 25) in WGS84-Grad oder in einer
transversalen Mercator-Abbildung auf GRS80/WGS84, also UTM - in Deutschland
ETRS89/UTM 32N und 33N, mit oder ohne Zonenvorsatz in der Ostkoordinate.
Gauß-Krüger auf Bessel (DHDN) wird abgelehnt: die Datumsverschiebung dahinter
ist ohne Tabellen nur auf Meter genau, und Meter sind hier zu viel.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

from . import geo

POLYGON_TYPEN = {5: "Polygon", 15: "PolygonZ", 25: "PolygonM"}

# Spaltennamen, in denen der Name des Feldes steht - in der Reihenfolge, in der
# sie genommen werden. Aus dem Flächenantrag heißt es SCHLAGNAME oder FLIK, aus
# dem Kataster FLURSTUECK; irgendjemand nennt es immer NAME.
NAMENSSPALTEN = ("SCHLAGNAME", "SCHLAG_NAME", "SCHLAGBEZ", "NAME", "FELDNAME",
                 "FELD", "SCHLAG", "BEZEICHN", "BEZ", "FLIK", "FLURSTUECK",
                 "FLURST", "FLURNAME", "FLUR", "ID")


class ShapefileFehler(ValueError):
    """Etwas an den Dateien wird nicht verstanden - mit Begründung."""


@dataclass
class Umriss:
    """Ein Feld aus der Datei: Name und Außenring in WGS84-Grad."""

    name: str
    ring: list[tuple[float, float]]           # (lat, lon), geschlossen nicht nötig
    attribute: dict = field(default_factory=dict)

    def als_feld(self) -> dict:
        """Ein Feld-Datensatz, wie storage.save_field ihn nimmt.

        Der Bezugspunkt ist der Schwerpunkt der Fläche: mitten im Feld sind die
        Meter der lokalen Ebene am ehrlichsten, und zwei Maschinen, die dieselbe
        Datei einlesen, landen auf demselben Bezug.
        """
        lat0 = sum(p[0] for p in self.ring) / len(self.ring)
        lon0 = sum(p[1] for p in self.ring) / len(self.ring)
        ebene = geo.LocalPlane(lat0, lon0)
        lokal = [ebene.to_local(lat, lon) for lat, lon in self.ring]
        lokal = geo.simplify(lokal, 0.2)
        return {
            "name": self.name,
            "datum_lat": lat0,
            "datum_lon": lon0,
            "boundary": [[round(x, 3), round(y, 3)] for x, y in lokal],
            "area_ha": geo.polygon_area(lokal) / 10_000.0,
            "note": "aus Shapefile",
        }


# --------------------------------------------------------------- Projektion


@dataclass
class TransversalMercator:
    """Umkehrung der transversalen Mercator-Abbildung (UTM, Gauß-Krüger).

    Die Reihen nach Snyder, "Map Projections - A Working Manual", Gleichungen
    8-9 bis 8-25. Für UTM-Entfernungen vom Mittelmeridian sind sie auf
    Millimeter genau; die Ellipsoidparameter kommen aus der .prj-Datei.
    """

    a: float = 6_378_137.0
    f_inv: float = 298.257222101         # GRS80
    lat0_deg: float = 0.0
    lon0_deg: float = 9.0
    k0: float = 0.9996
    false_e: float = 500_000.0
    false_n: float = 0.0

    def nach_wgs(self, ost: float, nord: float) -> tuple[float, float]:
        a, k0 = self.a, self.k0
        f = 1.0 / self.f_inv
        e2 = 2 * f - f * f
        ep2 = e2 / (1 - e2)
        x = ost - self.false_e
        y = nord - self.false_n
        lat0 = math.radians(self.lat0_deg)

        def meridian(phi: float) -> float:
            return a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
                        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
                        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
                        - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))

        m = meridian(lat0) + y / k0
        mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
        e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
        phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
                + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
                + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
                + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
        sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
        c1 = ep2 * cos1 ** 2
        t1 = tan1 ** 2
        n1 = a / math.sqrt(1 - e2 * sin1 ** 2)
        r1 = a * (1 - e2) / (1 - e2 * sin1 ** 2) ** 1.5
        d = x / (n1 * k0)
        lat = phi1 - (n1 * tan1 / r1) * (
            d ** 2 / 2
            - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
            + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
        lon = math.radians(self.lon0_deg) + (
            d - (1 + 2 * t1 + c1) * d ** 3 / 6
            + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
        ) / cos1
        return math.degrees(lat), math.degrees(lon)


def utm(zone: int, ellipsoid: str = "GRS80") -> TransversalMercator:
    f_inv = 298.257223563 if ellipsoid.upper().startswith("WGS") else 298.257222101
    return TransversalMercator(f_inv=f_inv, lon0_deg=zone * 6 - 183)


@dataclass
class Koordinatensystem:
    name: str
    projektion: TransversalMercator | None     # None = schon Grad
    zonenvorsatz: int = 0                      # 32 bei "32500000"-Ostwerten

    def nach_wgs(self, x: float, y: float) -> tuple[float, float]:
        if self.projektion is None:
            return y, x     # Shapefile: x = Länge, y = Breite
        if self.zonenvorsatz and x >= 1_000_000:
            x -= self.zonenvorsatz * 1_000_000
        return self.projektion.nach_wgs(x, y)


def _wkt_zahl(wkt: str, schluessel: str, standard: float) -> float:
    treffer = re.search(r'PARAMETER\["%s",\s*([-\d.eE+]+)' % schluessel, wkt, re.I)
    return float(treffer.group(1)) if treffer else standard


def koordinatensystem_aus_prj(prj: str | None, probe: tuple[float, float] | None = None
                              ) -> Koordinatensystem:
    """Das Koordinatensystem aus der .prj lesen - oder, ohne .prj, aus den Zahlen raten.

    Geraten wird nur, was sich nicht verwechseln lässt: Grad sehen aus wie
    Grad, und ein Ostwert mit Zonenvorsatz (32 500 000) verrät seine Zone
    selbst. Alles andere ist ein Fehler, kein Versuch.
    """
    if prj and prj.strip():
        wkt = prj.strip()
        kopf = wkt.split("[", 1)[0].upper()
        if kopf == "GEOGCS":
            return Koordinatensystem("WGS84-Grad", None)
        if kopf != "PROJCS":
            raise ShapefileFehler(f"Unbekanntes Koordinatensystem in der .prj: {kopf}")
        name = re.match(r'PROJCS\["([^"]*)"', wkt)
        name = name.group(1) if name else "Projektion"
        if not re.search(r'PROJECTION\["Transverse_Mercator"', wkt, re.I):
            raise ShapefileFehler(f"Projektion in „{name}“ wird nicht unterstützt - "
                                  "bitte als ETRS89/UTM oder WGS84 exportieren")
        spheroid = re.search(r'SPHEROID\["([^"]*)",\s*([\d.]+),\s*([\d.]+)', wkt, re.I)
        if not spheroid:
            raise ShapefileFehler(f"Kein Ellipsoid in der .prj von „{name}“")
        f_inv = float(spheroid.group(3))
        if abs(f_inv - 298.257) > 0.01:
            # Bessel (299.15) - Gauß-Krüger auf DHDN. Ohne Datumsübergang
            # läge alles hundert Meter daneben, mit dem 7-Parameter-Satz
            # immer noch Meter. Beides zu schlecht für eine Feldgrenze.
            raise ShapefileFehler(f"„{name}“ liegt auf dem Ellipsoid {spheroid.group(1)} - "
                                  "Gauß-Krüger/DHDN wird nicht umgerechnet. Bitte als "
                                  "ETRS89/UTM (EPSG:25832/25833) exportieren")
        projektion = TransversalMercator(
            a=float(spheroid.group(2)), f_inv=f_inv,
            lat0_deg=_wkt_zahl(wkt, "Latitude_Of_Origin", 0.0),
            lon0_deg=_wkt_zahl(wkt, "Central_Meridian", 9.0),
            k0=_wkt_zahl(wkt, "Scale_Factor", 0.9996),
            false_e=_wkt_zahl(wkt, "False_Easting", 500_000.0),
            false_n=_wkt_zahl(wkt, "False_Northing", 0.0),
        )
        zone = round((projektion.lon0_deg + 183) / 6)
        vorsatz = zone if projektion.false_e >= 1_000_000 else 0
        if vorsatz:
            # EPSG:4647/5652 tragen die Zone im Ostwert: 32 500 000 statt 500 000.
            projektion.false_e -= vorsatz * 1_000_000
        return Koordinatensystem(name, projektion, vorsatz)

    if probe is None:
        raise ShapefileFehler("Keine .prj-Datei und keine Koordinaten zum Raten")
    x, y = probe
    if abs(x) <= 180 and abs(y) <= 90:
        return Koordinatensystem("WGS84-Grad (geraten, keine .prj)", None)
    if 1_000_000 <= x < 61_000_000 and 0 <= y <= 10_000_000:
        zone = int(x // 1_000_000)
        if 1 <= zone <= 60:
            return Koordinatensystem(f"UTM {zone}N mit Zonenvorsatz (geraten, keine .prj)",
                                     utm(zone), zone)
    raise ShapefileFehler("Keine .prj-Datei - das Koordinatensystem lässt sich aus "
                          f"({x:.0f}, {y:.0f}) nicht sicher erkennen. Bitte die .prj mitgeben")


# ---------------------------------------------------------------- .dbf lesen


_CODEPAGES = {0x01: "cp437", 0x02: "cp850", 0x03: "cp1252", 0x57: "cp1252",
              0x64: "cp852", 0x65: "cp866", 0x66: "cp865", 0x67: "cp861",
              0x6A: "cp737", 0x6B: "cp857", 0xC8: "cp1250", 0xC9: "cp1251",
              0xCA: "cp1254", 0xCB: "cp1253"}


def dbf_lesen(daten: bytes) -> list[dict]:
    """Die Attribute je Datensatz, als Wörterbuch Spaltenname -> Text."""
    if len(daten) < 32:
        return []
    anzahl, kopf_laenge, satz_laenge = struct.unpack_from("<IHH", daten, 4)
    codepage = _CODEPAGES.get(daten[29], "cp1252")
    spalten: list[tuple[str, str, int]] = []
    pos = 32
    while pos + 32 <= kopf_laenge and daten[pos] != 0x0D:
        name = daten[pos:pos + 11].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        typ = chr(daten[pos + 11])
        laenge = daten[pos + 16]
        spalten.append((name, typ, laenge))
        pos += 32
    saetze = []
    pos = kopf_laenge
    for _ in range(anzahl):
        if pos + satz_laenge > len(daten):
            break
        satz = daten[pos:pos + satz_laenge]
        pos += satz_laenge
        if satz[:1] == b"*":       # gelöscht markiert
            saetze.append(None)
            continue
        werte, cursor = {}, 1
        for name, typ, laenge in spalten:
            roh = satz[cursor:cursor + laenge]
            cursor += laenge
            try:
                text = roh.decode("utf-8")
            except UnicodeDecodeError:
                text = roh.decode(codepage, "replace")
            werte[name] = text.strip("\x00 ").strip()
        saetze.append(werte)
    return saetze


def _name_aus(attribute: dict, nummer: int) -> str:
    gross = {k.upper(): v for k, v in attribute.items()}
    for spalte in NAMENSSPALTEN:
        wert = gross.get(spalte, "")
        if wert:
            return wert[:60]
    for wert in attribute.values():
        if wert and not re.fullmatch(r"[-\d.]+", wert):
            return wert[:60]
    return f"Feld {nummer}"


# ---------------------------------------------------------------- .shp lesen


def _ring_uhrzeigersinn(ring: list[tuple[float, float]]) -> bool:
    flaeche = 0.0
    for i in range(len(ring) - 1):
        (x1, y1), (x2, y2) = ring[i], ring[i + 1]
        flaeche += x1 * y2 - x2 * y1
    return flaeche < 0


def shp_lesen(daten: bytes) -> list[list[list[tuple[float, float]]]]:
    """Je Datensatz die Außenringe als Liste von (x, y).

    Löcher (Ringe gegen den Uhrzeigersinn) werden weggelassen - ein Feld mit
    einem Teich darin ist für die Spurführung ein Feld; die Grenze im Programm
    kennt keine Löcher.
    """
    if len(daten) < 100 or struct.unpack_from(">i", daten, 0)[0] != 9994:
        raise ShapefileFehler("Das ist keine .shp-Datei (Kennung fehlt)")
    typ = struct.unpack_from("<i", daten, 32)[0]
    if typ not in POLYGON_TYPEN:
        raise ShapefileFehler(f"Shapefile-Typ {typ} enthält keine Flächen - "
                              "gebraucht werden Polygone")
    datensaetze = []
    pos = 100
    while pos + 8 <= len(daten):
        laenge_worte = struct.unpack_from(">i", daten, pos + 4)[0]
        if laenge_worte <= 0:
            # Eine Satzlänge von null käme nie voran - beschädigte Datei, Schluss.
            raise ShapefileFehler("Die .shp-Datei ist beschädigt (Satzlänge 0)")
        inhalt = daten[pos + 8:pos + 8 + laenge_worte * 2]
        pos += 8 + laenge_worte * 2
        if len(inhalt) < 4:
            continue
        satz_typ = struct.unpack_from("<i", inhalt, 0)[0]
        if satz_typ == 0:           # Null-Shape: Platzhalter
            datensaetze.append([])
            continue
        if satz_typ not in POLYGON_TYPEN:
            datensaetze.append([])
            continue
        teile_anz, punkte_anz = struct.unpack_from("<ii", inhalt, 36)
        teile = list(struct.unpack_from(f"<{teile_anz}i", inhalt, 44))
        p0 = 44 + teile_anz * 4
        punkte = [struct.unpack_from("<dd", inhalt, p0 + i * 16) for i in range(punkte_anz)]
        ringe = []
        for i, start in enumerate(teile):
            ende = teile[i + 1] if i + 1 < teile_anz else punkte_anz
            ring = punkte[start:ende]
            if len(ring) >= 4 and _ring_uhrzeigersinn(ring):
                ringe.append(ring)
        if not ringe and teile:
            # Manche Programme schreiben den Umlaufsinn falsch herum - dann ist
            # der größte Ring der Außenring.
            kandidaten = []
            for i, start in enumerate(teile):
                ende = teile[i + 1] if i + 1 < teile_anz else punkte_anz
                ring = punkte[start:ende]
                if len(ring) >= 4:
                    kandidaten.append(ring)
            if kandidaten:
                ringe = [max(kandidaten, key=len)]
        datensaetze.append(ringe)
    return datensaetze


def lesen(shp: bytes, dbf: bytes | None = None, prj: str | None = None) -> list[Umriss]:
    """Alle Felder aus den drei Dateien - .dbf und .prj dürfen fehlen."""
    datensaetze = shp_lesen(shp)
    attribute = dbf_lesen(dbf) if dbf else []
    probe = None
    for ringe in datensaetze:
        if ringe:
            probe = ringe[0][0]
            break
    if probe is None:
        raise ShapefileFehler("Die Datei enthält keine Fläche")
    system = koordinatensystem_aus_prj(prj, probe)
    umrisse: list[Umriss] = []
    nummer = 0
    for index, ringe in enumerate(datensaetze):
        werte = attribute[index] if index < len(attribute) and attribute[index] else {}
        for k, ring in enumerate(ringe):
            nummer += 1
            name = _name_aus(werte, nummer)
            if len(ringe) > 1:
                name = f"{name} ({k + 1})"
            punkte = [system.nach_wgs(x, y) for x, y in ring]
            if punkte[0] == punkte[-1]:
                punkte = punkte[:-1]
            if len(punkte) < 3:
                continue
            umrisse.append(Umriss(name, punkte, werte))
    if not umrisse:
        raise ShapefileFehler("Keine verwertbare Fläche in der Datei")
    return umrisse
