"""Vorgewende: der Rand des Feldes - und das Wenden darin.

Das Vorgewende ist kein eigenes Feld, sondern eine Tiefe: so viele Arbeitsbreiten
vom Rand nach innen, wie zum Wenden gebraucht werden. Die Engine übergibt die
aktive Arbeitsgrenze, sofern vorhanden, sonst die ursprüngliche Feldgrenze.
Die flächige Gespannprüfung ergänzt gespann.py.

Drei Dinge stehen hier:

* **Wie weit ist es noch?** Ein Strahl in Fahrtrichtung bis zur Feldgrenze,
  abzüglich der Vorgewendetiefe. Das ist die Zahl, die der Fahrer wirklich
  braucht, und sie kommt ohne Versatzpolygon aus.
* **Wendemuster.** Ω-Wende, U-Wende und Dubins-Bögen werden vor der Fahrt
  geplant und zusammen mit der Gespannhülle gegen die Grenze geprüft.
* **Das Nachfahren der geplanten Route.** Mit derselben Ehrlichkeit wie die
  Spurführung: die gemeldete Abweichung ist die Abweichung von der *Wenderoute*,
  nicht von der verlassenen Spur. Nur so bleibt die Sicherheitsgrenze der
  Lenkung (``max_cross_track_m``) während der Wende eine echte Grenze und nicht
  eine, die ohnehin sofort reißt.

Was hier ausdrücklich **nicht** steht: automatisierte Rückwärtsfahrt. AgriPilot
steuert das Lenkrad, nicht Fahrstufe und nicht Gas. Ein Wendemuster, das ohne
Rückwärtsgang nicht funktioniert, wäre eine Route, die die Maschine nicht fahren
kann - also gibt es sie hier nicht.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .geo import (
    Point,
    angle_difference,
    distance,
    heading_deg,
    normalize_heading,
    point_in_polygon,
    project_on_segment,
)
from .guidance import GuidanceState, VehicleProfile, lightbar_offset

# Muster und Richtungen, wie sie in der Oberfläche stehen.
MUSTER = ("omega", "u", "dubins")
RICHTUNGEN = ("links", "rechts")
REIHENFOLGEN = ("egal", "zuerst", "zuletzt")


def _forward(heading: float) -> Point:
    h = math.radians(heading)
    return math.sin(h), math.cos(h)


def _right(heading: float) -> Point:
    h = math.radians(heading)
    return math.cos(h), -math.sin(h)


def _side(heading: float, turn_left: bool) -> Point:
    """Einheitsvektor zur Kurveninnenseite."""
    rx, ry = _right(heading)
    return (-rx, -ry) if turn_left else (rx, ry)


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


@dataclass
class HeadlandSettings:
    """Alles, was das Vorgewende und die Wende beschreibt.

    Die Tiefe steht bewusst in **Arbeitsbreiten**, nicht in Metern: sie ist eine
    Zahl von Spuren, die am Rand quer gefahren werden, und ändert sich mit dem
    Gerät mit. Wer sie doch in Metern braucht - ein Vorgewende, das breiter ist
    als die Maschine -, trägt ``breite_m`` ein und überschreibt damit.
    """

    aktiv: bool = True
    spuren: int = 2                 # Anzahl Vorgewendespuren
    breite_m: float = 0.0           # 0 = Arbeitsbreite des Geräts nehmen
    alarm_aktiv: bool = True
    alarm_abstand_m: float = 20.0
    muster: str = "omega"           # "omega" | "u"
    richtung: str = "links"         # "links" | "rechts"
    ueberspringen: int = 1          # Wiedereinfahrt: um so viele Spuren versetzt
    grenzabstand_m: float = 0.5     # zusätzliche Reserve um die gemessene Gespannfläche
    radius_m: float = 6.0           # kleinster Wendekreis der Maschine
    nur_im_feld: bool = True        # Sicherheitsprüfung erzwingen
    reihenfolge: str = "egal"       # "zuerst" | "zuletzt" | "egal" - wann das Vorgewende dran ist

    def tiefe_m(self, arbeitsbreite_m: float) -> float:
        breite = self.breite_m if self.breite_m > 0.0 else max(0.5, arbeitsbreite_m)
        return max(0.0, self.spuren) * breite

    def normalisiert(self) -> "HeadlandSettings":
        """Werte in ihre Grenzen holen - eine Wende plant man nicht auf Zuruf."""
        return HeadlandSettings(
            aktiv=bool(self.aktiv),
            spuren=int(max(0, min(12, self.spuren))),
            breite_m=float(max(0.0, min(80.0, self.breite_m))),
            alarm_aktiv=bool(self.alarm_aktiv),
            alarm_abstand_m=float(max(1.0, min(200.0, self.alarm_abstand_m))),
            muster=self.muster if self.muster in MUSTER else "omega",
            richtung=self.richtung if self.richtung in RICHTUNGEN else "links",
            ueberspringen=int(max(1, min(8, self.ueberspringen))),
            grenzabstand_m=float(max(0.0, min(20.0, self.grenzabstand_m))),
            radius_m=float(max(1.5, min(30.0, self.radius_m))),
            nur_im_feld=bool(self.nur_im_feld),
            reihenfolge=self.reihenfolge if self.reihenfolge in REIHENFOLGEN else "egal",
        )

    def to_dict(self) -> dict:
        return {
            "aktiv": self.aktiv,
            "spuren": self.spuren,
            "breite_m": self.breite_m,
            "alarm_aktiv": self.alarm_aktiv,
            "alarm_abstand_m": self.alarm_abstand_m,
            "muster": self.muster,
            "richtung": self.richtung,
            "ueberspringen": self.ueberspringen,
            "radius_m": self.radius_m,
            "nur_im_feld": self.nur_im_feld,
            "reihenfolge": self.reihenfolge,
        }

    @classmethod
    def from_dict(cls, werte: Optional[dict]) -> "HeadlandSettings":
        if not werte:
            return cls()
        felder = cls.__dataclass_fields__
        bekannt = {k: v for k, v in werte.items() if k in felder}
        return cls(**bekannt).normalisiert()


# ---------------------------------------------------------------------------
# Geometrie am Feldrand
# ---------------------------------------------------------------------------


def ray_to_boundary(origin: Point, heading: float,
                    boundary: Sequence[Point]) -> Optional[float]:
    """Strecke von ``origin`` in Fahrtrichtung bis zur Feldgrenze, in Metern.

    ``None``, wenn der Strahl die Grenze nicht trifft - der übliche Grund dafür
    ist, dass die Maschine gar nicht im Feld steht. Getroffen wird die
    *nächstgelegene* Kante voraus; bei einem eingebuchteten Schlag ist das die
    richtige Antwort, denn dort endet die Fahrt.
    """
    if len(boundary) < 3:
        return None
    dx, dy = _forward(heading)
    naechste: Optional[float] = None
    for i in range(len(boundary)):
        ax, ay = boundary[i]
        bx, by = boundary[(i + 1) % len(boundary)]
        ex, ey = bx - ax, by - ay
        nenner = dx * ey - dy * ex
        if abs(nenner) < 1e-12:
            continue        # Kante liegt parallel zur Fahrtrichtung
        # t = Strecke entlang des Strahls, u = Anteil auf der Kante
        t = (ex * (origin[1] - ay) - ey * (origin[0] - ax)) / nenner
        u = (dx * (origin[1] - ay) - dy * (origin[0] - ax)) / nenner
        if t <= 0.01 or not (0.0 <= u <= 1.0):
            continue
        if naechste is None or t < naechste:
            naechste = t
    return naechste


def distance_to_boundary(point: Point, boundary: Sequence[Point]) -> Optional[float]:
    """Kürzester Abstand zur Feldgrenze, ohne Rücksicht auf die Fahrtrichtung."""
    if len(boundary) < 2:
        return None
    kleinster = float("inf")
    for i in range(len(boundary)):
        a = boundary[i]
        b = boundary[(i + 1) % len(boundary)]
        fuss, _, _ = project_on_segment(point, a, b)
        kleinster = min(kleinster, distance(point, fuss))
    return kleinster


@dataclass
class HeadlandStatus:
    """Was die Kabine über das Vorgewende anzeigt."""

    tiefe_m: float = 0.0
    rest_m: Optional[float] = None      # bis zum Beginn des Vorgewendes
    bis_grenze_m: Optional[float] = None
    im_vorgewende: bool = False
    alarm: bool = False
    abdeckung: Optional[float] = None   # Anteil des Vorgewendes, der bearbeitet ist
    hinweis: str = ""                   # Reihenfolge: was jetzt dran wäre

    def to_dict(self) -> dict:
        return {
            "tiefe_m": self.tiefe_m,
            "rest_m": self.rest_m,
            "bis_grenze_m": self.bis_grenze_m,
            "im_vorgewende": self.im_vorgewende,
            "alarm": self.alarm,
            "abdeckung": self.abdeckung,
            "hinweis": self.hinweis,
        }


def status(position: Optional[Point], heading: Optional[float],
           boundary: Sequence[Point], settings: HeadlandSettings,
           arbeitsbreite_m: float) -> HeadlandStatus:
    """Restdistanz, Vorgewendelage und Annäherungsalarm in einem Durchgang.

    ``rest_m`` ist die Strecke bis zum *Beginn* des Vorgewendes, nicht bis zur
    Feldgrenze: dort muss die Arbeit enden und die Wende beginnen. Sie wird
    negativ, sobald die Maschine im Vorgewende steht - eine ehrlichere Anzeige
    als eine bei null abgeschnittene, denn sie sagt auch, wie weit man schon
    drin ist.
    """
    tiefe = settings.tiefe_m(arbeitsbreite_m)
    ergebnis = HeadlandStatus(tiefe_m=tiefe)
    if position is None or heading is None or len(boundary) < 3:
        return ergebnis

    ergebnis.bis_grenze_m = distance_to_boundary(position, boundary)
    if ergebnis.bis_grenze_m is not None:
        ergebnis.im_vorgewende = (point_in_polygon(position, boundary)
                                  and ergebnis.bis_grenze_m <= tiefe)

    voraus = ray_to_boundary(position, heading, boundary)
    if voraus is not None:
        ergebnis.rest_m = voraus - tiefe
        if settings.aktiv and settings.alarm_aktiv:
            ergebnis.alarm = ergebnis.rest_m <= settings.alarm_abstand_m
    return ergebnis


def abdeckung(ist_bearbeitet, boundary: Sequence[Point], tiefe_m: float,
              schritt_m: float = 3.0) -> Optional[float]:
    """Wie viel vom Vorgewende schon bearbeitet ist, als Anteil 0..1.

    Abgetastet wird die Mittellinie des Vorgewendes - der Ring in halber Tiefe -
    alle paar Meter. Das ist keine Flächenmessung, aber die Frage ist auch keine
    Flächenfrage: "ist das Vorgewende schon dran gewesen?" beantwortet eine Linie
    mittendurch ehrlich genug, und sie kostet ein paar Dutzend Abfragen statt
    Tausender.

    ``ist_bearbeitet`` ist die Frage an die Fläche (``CoverageMap.is_covered``).
    ``None``, wenn es kein Vorgewende gibt oder der Ring nicht zu bilden ist.
    """
    if tiefe_m <= 0.0 or len(boundary) < 3:
        return None
    mitte = ring(boundary, tiefe_m / 2.0)
    if len(mitte) < 3:
        return None
    proben = 0
    getroffen = 0
    n = len(mitte)
    for i in range(n):
        a, b = mitte[i], mitte[(i + 1) % n]
        laenge = distance(a, b)
        stuecke = max(1, int(laenge / schritt_m))
        for k in range(stuecke):
            t = (k + 0.5) / stuecke
            punkt = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            proben += 1
            if ist_bearbeitet(punkt):
                getroffen += 1
    return getroffen / proben if proben else None


def reihenfolge_hinweis(settings: HeadlandSettings, abdeckung_anteil: Optional[float],
                        modus: Optional[str], ring_nr: Optional[int]) -> str:
    """Ein Satz dazu, ob die eingestellte Reihenfolge gerade eingehalten wird.

    Das Programm erzwingt nichts - es kann nicht wissen, warum der Fahrer heute
    anders herum fährt. Aber es kann sagen, was es sieht: das Vorgewende ist
    noch nicht dran gewesen, und die Einstellung sagt "zuerst". Der Rest ist
    Sache des Menschen auf dem Sitz.
    """
    if settings.reihenfolge == "egal" or abdeckung_anteil is None:
        return ""
    fertig = abdeckung_anteil >= 0.8
    prozent = f"{abdeckung_anteil * 100:.0f} %"
    auf_vorgewende = modus == "contour" and ring_nr is not None and ring_nr < settings.spuren
    if settings.reihenfolge == "zuerst":
        if auf_vorgewende:
            return f"Vorgewende zuerst: Ring {ring_nr + 1} von {settings.spuren} · {prozent}"
        if not fertig and modus in ("ab", "curve"):
            return f"Vorgewende zuerst - erst die Kontur fahren ({prozent} bearbeitet)"
        return ""
    # "zuletzt": ob das Innere schon fertig ist, weiß die Fläche nicht als eine
    # Zahl - gesagt wird nur, wie weit das Vorgewende ist, wenn man darauf fährt.
    if auf_vorgewende:
        return f"Vorgewende zuletzt: Ring {ring_nr + 1} von {settings.spuren} · {prozent}"
    return ""


def ring(boundary: Sequence[Point], inset_m: float) -> list[Point]:
    """Die Feldgrenze um ``inset_m`` nach innen versetzt - der größte Ring.

    Zerfällt der Schlag bei diesem Abstand in mehrere Teile (enger Hals, tiefe
    Einbuchtung), liefert ``ringe`` alle; hier kommt der größte.
    """
    alle = ringe(boundary, inset_m)
    if not alle:
        return [] if len(boundary) >= 3 and inset_m > 0.0 else [tuple(p) for p in boundary]
    return max(alle, key=lambda r: abs(_flaeche(r)))


SPITZE_HOECHSTENS = 3.0   # weiter als so viele Rundenabstände von der Ecke: abschrägen
MIN_PUNKTABSTAND_M = 1e-6  # näher beieinander: ein Punkt
ZACKEN_M = 5.0       # Haken unter dieser Länge mit scharfem Knick sind Rechenreste, keine Ecken


def ringe(boundary: Sequence[Point], inset_m: float) -> list[list[Point]]:
    """Alle Ringe der um ``inset_m`` nach innen versetzten Feldgrenze.

    Jede Kante wird nach innen geschoben und mit ihren Nachbarn geschnitten -
    das hält Ecken spitz. An einspringenden Ecken (Einbuchtung, Stufe) und an
    schmalen Zipfeln schneidet sich das Ergebnis dann selbst und bildet
    Schleifen, die näher als ``inset_m`` an der Grenze liegen oder draußen.
    Auf diesen Ringen wird gelenkt (Saatplan): die Schleifen werden deshalb an
    den Schnittpunkten herausgeschnitten, nicht nur einzelne Punkte
    verworfen - sonst blieben Zacken quer durchs Feld (Stelle 2, 23.10.2026).
    Übrig bleibt, was überall mindestens ``inset_m`` von der Grenze entfernt
    ist, als ein oder mehrere geschlossene Ringe.
    """
    if len(boundary) < 3 or inset_m <= 0.0:
        return [[tuple(p) for p in boundary]] if len(boundary) >= 3 else []
    punkte = [tuple(map(float, p)) for p in boundary]
    roh = _versatz_roh(punkte, inset_m)
    if len(roh) < 3:
        return []
    toleranz = 0.05                 # die Übergänge werden auf 1 cm genau gesucht

    def gueltig(p: Point) -> bool:
        return point_in_polygon(p, punkte) and (distance_to_boundary(p, punkte) or 0.0) >= inset_m - toleranz

    stuecke = []
    for a, b in _an_schnitten_teilen(roh):
        if distance(a, b) > 1e-9:
            stuecke.extend(_gueltige_teile(a, b, gueltig))
    def abruecken(p: Point) -> Point:
        # Zu nah an der Grenze: vom nächsten Grenzpunkt weg auf genau inset_m.
        fuss = _naechster_grenzpunkt(p, punkte)
        d = distance(p, fuss)
        if d >= inset_m - toleranz or d < 1e-9:
            return p
        return (fuss[0] + (p[0] - fuss[0]) * inset_m / d, fuss[1] + (p[1] - fuss[1]) * inset_m / d)

    geschlossen, offen = _zu_ringen(stuecke)
    def auf_abstand(p: Point) -> bool:
        # Gültig und nicht weiter drinnen als die Runde: eine Verbindung quer durchs Feld
        # (zu einem anderen Teil des Schlags) wäre sonst auch "gültig".
        return gueltig(p) and (distance_to_boundary(p, punkte) or 0.0) <= inset_m + 1.0

    geschlossen += _offene_verbinden(offen, len(stuecke), 10.0 * inset_m + 30.0, abruecken, auf_abstand)
    ergebnis = []
    for schleife in geschlossen:
        schleife = _ohne_zacken(_ohne_doppelte(schleife), ZACKEN_M, abruecken, gueltig)
        schleife = _ohne_kreuzungen(schleife)
        if len(schleife) >= 3 and abs(_flaeche(schleife)) > inset_m * inset_m:
            ergebnis.append(schleife)
    return ergebnis


def _flaeche(punkte: Sequence[Point]) -> float:
    n = len(punkte)
    return 0.5 * sum(punkte[i][0] * punkte[(i + 1) % n][1] - punkte[(i + 1) % n][0] * punkte[i][1]
                     for i in range(n))


def _versatz_roh(punkte: list[Point], inset_m: float) -> list[Point]:
    """Jede Kante nach innen, mit den Nachbarn geschnitten - noch mit Schleifen."""
    n = len(punkte)
    vorzeichen = 1.0 if _flaeche(punkte) > 0 else -1.0   # gegen den Uhrzeigersinn: links ist innen
    kanten = []
    for i in range(n):
        a, b = punkte[i], punkte[(i + 1) % n]
        laenge = distance(a, b)
        if laenge < 1e-9:
            continue
        ex, ey = (b[0] - a[0]) / laenge, (b[1] - a[1]) / laenge
        nx, ny = -ey * vorzeichen, ex * vorzeichen
        kanten.append(((a[0] + nx * inset_m, a[1] + ny * inset_m), (ex, ey), b, (nx, ny)))
    if len(kanten) < 3:
        return []
    roh: list[Point] = []
    for i in range(len(kanten)):
        (px, py), (ex, ey), ecke, (nx, ny) = kanten[i]
        (qx, qy), (fx, fy), _, (mx, my) = kanten[(i + 1) % len(kanten)]
        # Am Ende von Kante i: wo sie, versetzt, aufhört - und wo Kante i+1 anfängt.
        ende_i = (ecke[0] + nx * inset_m, ecke[1] + ny * inset_m)
        nenner = ex * fy - ey * fx
        if abs(nenner) < 1e-9:
            roh.append((qx, qy))      # fast parallel: der Versatzpunkt genügt
            continue
        if (ex * fy - ey * fx) * vorzeichen < 0:
            # Einspringende Ecke: hier ist die Runde ein Bogen um die Ecke, überall genau
            # inset_m von ihr. Der Schnittpunkt der verlängerten Kanten läge tiefer im
            # Feld (rechtwinklig: 1,4-mal so tief) - die Hauptfeldspuren kreuzten die Runde.
            a0, a1 = math.atan2(ny, nx), math.atan2(my, mx)
            schwenk = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
            schritte = max(1, int(abs(schwenk) / math.radians(10.0)))
            roh.extend((ecke[0] + math.cos(a0 + schwenk * k / schritte) * inset_m,
                        ecke[1] + math.sin(a0 + schwenk * k / schritte) * inset_m)
                       for k in range(schritte + 1))
            continue
        t = ((qx - px) * fy - (qy - py) * fx) / nenner
        spitze = (px + ex * t, py + ey * t)
        if distance(spitze, ecke) > SPITZE_HOECHSTENS * inset_m:
            # Sehr spitze Ecke (oder eine kurze Kante fast zurück): der Schnittpunkt
            # läge weit draußen. Abgeschrägt statt spitz - die Schleife, die dabei
            # entsteht, schneidet ringe() heraus.
            roh.extend([ende_i, (qx, qy)])
            continue
        roh.append(spitze)
    return roh


def _an_schnitten_teilen(roh: list[Point]) -> list[tuple[Point, Point]]:
    """Den geschlossenen Zug an jedem Punkt teilen, an dem er sich selbst kreuzt.

    Der Schnittpunkt wird einmal berechnet und in beide Kanten eingesetzt -
    so passen die Teilstücke beim Zusammensetzen exakt aneinander.
    """
    n = len(roh)
    teile: list[list[tuple[float, Point]]] = [[] for _ in range(n)]
    for i in range(n):
        a, b = roh[i], roh[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue                         # Nachbarn: teilen sich nur den Eckpunkt
            c, d = roh[j], roh[(j + 1) % n]
            treffer = _schnitt(a, b, c, d)
            if treffer is not None:
                s, t, punkt = treffer
                teile[i].append((s, punkt))
                teile[j].append((t, punkt))
    stuecke = []
    for i in range(n):
        folge = [roh[i]] + [p for _, p in sorted(teile[i])] + [roh[(i + 1) % n]]
        stuecke.extend(zip(folge, folge[1:]))
    return stuecke


def _schnitt(a: Point, b: Point, c: Point, d: Point) -> Optional[tuple[float, float, Point]]:
    """Echter Schnitt zweier Strecken im Inneren (nicht an den Enden)."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    nenner = rx * sy - ry * sx
    if abs(nenner) < 1e-12:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    s = (qx * sy - qy * sx) / nenner
    t = (qx * ry - qy * rx) / nenner
    if 1e-9 < s < 1.0 - 1e-9 and 1e-9 < t < 1.0 - 1e-9:
        return s, t, (a[0] + rx * s, a[1] + ry * s)
    return None


def _zu_ringen(stuecke: list[tuple[Point, Point]]) -> tuple[list[list[Point]], list[tuple[int, int, list[Point]]]]:
    """Die übrigen Teilstücke wieder verketten: geschlossene Ringe und offene Züge.

    Ein offener Zug kommt mit der Nummer seines ersten und letzten Stücks -
    die Stücke liegen in der Reihenfolge entlang der Grenze.
    """
    def schluessel(p: Point) -> tuple[int, int]:
        return (round(p[0] * 1e6), round(p[1] * 1e6))

    ab: dict[tuple[int, int], list[int]] = {}
    for k, (a, _) in enumerate(stuecke):
        ab.setdefault(schluessel(a), []).append(k)
    benutzt = [False] * len(stuecke)
    ringe_, offen = [], []
    for anfang in range(len(stuecke)):
        if benutzt[anfang]:
            continue
        ziel = schluessel(stuecke[anfang][0])
        zug, k, geschlossen = [], anfang, False
        while True:
            benutzt[k] = True
            a, b = stuecke[k]
            zug.append(a)
            if schluessel(b) == ziel:
                geschlossen = True
                break
            weiter = [m for m in ab.get(schluessel(b), []) if not benutzt[m]]
            if not weiter:
                break
            k = weiter[0]
        if geschlossen and len(zug) >= 3:
            ringe_.append(zug)
        elif not geschlossen:
            offen.append((anfang, k, zug + [b]))
    return ringe_, offen


def _gueltige_teile(a: Point, b: Point, gueltig) -> list[tuple[Point, Point]]:
    """Die Teile der Strecke a-b, die gültig liegen - mit genauen Übergängen.

    Nicht jede Schleife zeigt sich als Selbstschnitt: verschwindet beim
    Versetzen eine kurze Kante ganz, läuft die Nachbarkante einfach durch den
    zu nahen Bereich. Deshalb wird entlang der Strecke geprüft (alle halben
    Meter) und jeder Übergang auf einen Zentimeter genau eingegrenzt.
    """
    laenge = distance(a, b)
    schritte = max(2, int(laenge / 0.5) + 1)
    def punkt(t: float) -> Point:
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    def uebergang(t0: float, t1: float) -> float:
        g0 = gueltig(punkt(t0))
        for _ in range(30):
            if (t1 - t0) * laenge < 0.01:
                break
            m = (t0 + t1) / 2.0
            if gueltig(punkt(m)) == g0:
                t0 = m
            else:
                t1 = m
        return (t0 + t1) / 2.0
    ts = [i / schritte for i in range(schritte + 1)]
    werte = [gueltig(punkt(t)) for t in ts]
    # Die Enden liegen oft genau auf der Grenze des Bereichs: dort gilt die Mitte daneben.
    werte[0] = werte[0] or werte[1]
    werte[-1] = werte[-1] or werte[-2]
    teile, anfang = [], (0.0 if werte[0] else None)
    for i in range(1, len(ts)):
        if werte[i] != werte[i - 1]:
            t = uebergang(ts[i - 1], ts[i])
            if werte[i]:
                anfang = t
            elif anfang is not None:
                teile.append((anfang, t))
                anfang = None
    if anfang is not None:
        teile.append((anfang, 1.0))
    return [(a if t0 == 0.0 else punkt(t0), b if t1 == 1.0 else punkt(t1))
            for t0, t1 in teile if (t1 - t0) * laenge > 1e-6]


def _naechster_grenzpunkt(p: Point, grenze: Sequence[Point]) -> Point:
    bester, beste_d = grenze[0], float("inf")
    for i in range(len(grenze)):
        fuss, _, _ = project_on_segment(p, grenze[i], grenze[(i + 1) % len(grenze)])
        d = distance(p, fuss)
        if d < beste_d:
            bester, beste_d = fuss, d
    return bester


def _bruecke(a: Point, b: Point, abruecken, davor: Optional[Point] = None,
            danach: Optional[Point] = None, gueltig=None) -> list[Point]:
    """Was zwischen dem Ende a und dem Anfang b fehlt.

    An einer spitzen Ecke ist das die Spitze selbst: die beiden Stücke
    verlängert, bis sie sich treffen (wie es der Versatz ohne die kurze,
    umgeklappte Kante dort getan hätte). Sonst die Punkte der geraden
    Verbindung, jeder auf Rundenabstand von der Grenze gerückt - um eine
    einspringende Ecke ist die richtige Runde ein Bogen, die Gerade schnitte
    ihn ab.
    """
    if davor is not None and danach is not None and gueltig is not None:
        rx, ry = a[0] - davor[0], a[1] - davor[1]
        sx, sy = b[0] - danach[0], b[1] - danach[1]
        nenner = rx * sy - ry * sx
        if abs(nenner) > 1e-9:
            qx, qy = b[0] - a[0], b[1] - a[1]
            s_ = (qx * sy - qy * sx) / nenner        # vor a hinaus
            t_ = (qx * ry - qy * rx) / nenner        # vor b zurück
            spitze = (a[0] + rx * s_, a[1] + ry * s_)

            def strecke_gueltig(von: Point, bis: Point) -> bool:
                return all(gueltig((von[0] + (bis[0] - von[0]) * k / 4.0, von[1] + (bis[1] - von[1]) * k / 4.0))
                           for k in range(1, 4))

            nicht_zu_weit = distance(a, spitze) + distance(spitze, b) < 4.0 * distance(a, b) + 1.0
            if s_ > 0 and t_ > 0 and nicht_zu_weit and gueltig(spitze) \
                    and strecke_gueltig(a, spitze) and strecke_gueltig(spitze, b):
                return [spitze]
    schritte = max(1, int(distance(a, b) / 0.5))
    return [abruecken((a[0] + (b[0] - a[0]) * k / schritte, a[1] + (b[1] - a[1]) * k / schritte))
            for k in range(1, schritte)]


def _ohne_zacken(punkte: list[Point], kurz_m: float, abruecken=None, gueltig=None) -> list[Point]:
    """Zickzack heraus: scharfe Knicke (über 105°) zwischen kurzen Stücken.

    Streift eine versetzte Kante den zu nahen Bereich mehrmals knapp, bleiben
    Haken von wenigen Metern. Eine echte Ecke im Feld hat lange Schenkel.
    """
    punkte = list(punkte)
    geaendert = True
    while geaendert and len(punkte) > 3:
        geaendert = False
        for i in range(len(punkte)):
            a, p, b = punkte[i - 1], punkte[i], punkte[(i + 1) % len(punkte)]
            da, db = distance(a, p), distance(p, b)
            if da < 1e-9 or db < 1e-9:
                continue
            cos = ((p[0] - a[0]) * (b[0] - p[0]) + (p[1] - a[1]) * (b[1] - p[1])) / (da * db)
            if cos < -0.26 and max(da, db) < kurz_m:       # beide Schenkel kurz: ein Haken
                # Die Abkürzung a-b darf der Grenze nicht näher kommen als die Runde:
                # sonst ihre Punkte auf Rundenabstand rücken.
                ersatz: list[Point] = []
                if gueltig is not None and abruecken is not None:
                    schritte = max(1, int(distance(a, b) / 0.5))
                    probe = [(a[0] + (b[0] - a[0]) * k / schritte, a[1] + (b[1] - a[1]) * k / schritte)
                             for k in range(1, schritte)]
                    if not all(gueltig(q) for q in probe):
                        ersatz = _bruecke(a, b, abruecken)
                punkte[i:i + 1] = ersatz
                geaendert = True
                break
    return punkte


def _offene_verbinden(offen: list[tuple[int, int, list[Point]]], anzahl: int, reichweite: float,
                      abruecken, gueltig=None) -> list[list[Point]]:
    """Offene Züge schließen, in ihrer Reihenfolge entlang der Grenze.

    Wo eine Schleife oder eine umgeklappte kurze Kante herausgeschnitten wurde,
    fehlt ein Stück. Der Zug geht dort mit dem Zug weiter, der entlang der
    Grenze als nächster beginnt (der eigene Anfang zählt mit) - nicht mit dem,
    dessen Anfang zufällig am nächsten liegt: das wäre manchmal der eigene, und
    aus der Runde würde ein schmaler Streifen (Langer Bauer, 23.10.2026).
    Eine Verbindung muss dabei selbst überall Rundenabstand halten können;
    über einen zu engen Hals geht keine - dann schließt sich jeder Teil für
    sich, und der Schlag hat dort zwei Runden (Roggental).
    """
    zuege = sorted((z for z in offen if len(z[2]) >= 2), key=lambda z: z[0])
    fertig = []

    def verbindung(zug: list[Point], ziel: list[Point]) -> Optional[list[Point]]:
        ende, anfang = zug[-1], ziel[0]
        if distance(ende, anfang) > reichweite:
            return None
        bruecke = _bruecke(ende, anfang, abruecken, zug[-2] if len(zug) > 1 else None,
                           ziel[1] if len(ziel) > 1 else None, gueltig)
        if gueltig is not None and not all(gueltig(p) for p in bruecke):
            return None
        return bruecke

    while zuege:
        erster, letzter, zug = zuege.pop(0)
        zug = list(zug)
        while True:
            def hinter(nummer: int) -> int:
                return (nummer - letzter) % anzahl
            reihe = sorted([(hinter(z[0]), i) for i, z in enumerate(zuege)] + [(hinter(erster), -1)])
            weiter = None
            for _, i in reihe:
                bruecke = verbindung(zug, zug if i < 0 else zuege[i][2])
                if bruecke is not None:
                    weiter = (i, bruecke)
                    break
            if weiter is None:
                break                          # nirgends sauber anzuschließen: der Zug entfällt
            i, bruecke = weiter
            if i < 0:
                if len(zug) >= 3:
                    fertig.append(zug + bruecke)
                break
            _, letzter, naechster = zuege.pop(i)
            zug.extend(bruecke)
            zug.extend(naechster)
    return fertig


def _ohne_kreuzungen(ring: list[Point]) -> list[Point]:
    """Kreuzt sich der fertige Ring noch irgendwo (kleine Schleife an einer Verbindung),
    zerfällt er dort in zwei Schleifen - die kleinere fällt weg."""
    for _ in range(50):
        n = len(ring)
        treffer = None
        for i in range(n):
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue
                schnitt = _schnitt(ring[i], ring[(i + 1) % n], ring[j], ring[(j + 1) % n])
                if schnitt is not None:
                    treffer = (i, j, schnitt[2])
                    break
            if treffer:
                break
        if treffer is None:
            return ring
        i, j, x = treffer
        innen = [x] + ring[i + 1:j + 1]
        aussen = ring[:i + 1] + [x] + ring[j + 1:]
        ring = max((innen, aussen), key=lambda r: abs(_flaeche(r)) if len(r) >= 3 else -1.0)
    return ring


def _ohne_doppelte(punkte: list[Point]) -> list[Point]:
    """Doppelte und gerade durchlaufene Punkte heraus - der Ring bleibt derselbe."""
    sauber: list[Point] = []
    for p in punkte:
        if not sauber or distance(p, sauber[-1]) > MIN_PUNKTABSTAND_M:
            sauber.append(p)
    if len(sauber) > 1 and distance(sauber[0], sauber[-1]) <= MIN_PUNKTABSTAND_M:
        sauber.pop()
    ergebnis: list[Point] = []
    n = len(sauber)
    for i, p in enumerate(sauber):
        a, b = sauber[i - 1], sauber[(i + 1) % n]
        kreuz = (p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0])
        if abs(kreuz) > 1e-9 * max(1.0, distance(a, b)):
            ergebnis.append(p)
    return ergebnis if len(ergebnis) >= 3 else sauber


# ---------------------------------------------------------------------------
# Wendemuster
# ---------------------------------------------------------------------------


def _arc(start: Point, heading: float, radius: float, sweep_deg: float,
         turn_left: bool, samples: int = 24) -> list[Point]:
    """Kreisbogen konstanten Radius aus Startpunkt und Startkurs."""
    vorzeichen = -1.0 if turn_left else 1.0
    sx, sy = _side(heading, turn_left)
    mitte = (start[0] + sx * radius, start[1] + sy * radius)
    punkte: list[Point] = []
    for i in range(samples + 1):
        kurs = heading + vorzeichen * sweep_deg * (i / samples)
        vx, vy = _side(kurs, turn_left)
        punkte.append((mitte[0] - vx * radius, mitte[1] - vy * radius))
    return punkte


# Wie schnell sich die Krümmung einer Route ändern darf (1/m je Meter). Die
# Lenkung braucht Zeit, um von einem Einschlag zum anderen zu kommen: bei
# 25 Grad/s, 2,6 m Radstand und Wendetempo um 1,6 m/s sind das rund 0,1.
KRUEMMUNG_RATE = 0.10
_SCHRITT_M = 0.25


def _integriere(start: Point, heading: float, phasen: Sequence[tuple[float, float]],
                rate: float = KRUEMMUNG_RATE) -> list[Point]:
    """Route aus Krümmungsphasen: Krümmung rampt, statt zu springen.

    ``phasen`` sind (Zielkrümmung, Kursänderung in Grad); Krümmung positiv =
    nach rechts. Eine Phase endet, wenn ihre Kursänderung erreicht ist - die
    Rampe zählt mit. Am Ende läuft die Krümmung auf null zurück.
    """
    x, y = start
    psi = math.radians(heading)
    kappa = 0.0
    punkte: list[Point] = [(x, y)]

    def schritt() -> None:
        nonlocal x, y, psi
        psi += kappa * _SCHRITT_M
        x += math.sin(psi) * _SCHRITT_M
        y += math.cos(psi) * _SCHRITT_M
        punkte.append((x, y))

    for ziel, grad in phasen:
        soll = math.radians(abs(grad))
        gedreht = 0.0
        while gedreht < soll:
            kappa += max(-rate * _SCHRITT_M, min(rate * _SCHRITT_M, ziel - kappa))
            # Mit Vorzeichen: beim Umschwenken von rechts nach links dreht der
            # erste Teil der Rampe noch in die alte Richtung.
            gedreht += kappa * (1.0 if ziel > 0 else -1.0) * _SCHRITT_M
            schritt()
    while abs(kappa) > 1e-6:
        kappa -= max(-rate * _SCHRITT_M, min(rate * _SCHRITT_M, kappa))
        schritt()
    return punkte


def plan_omega(start: Point, heading: float, versatz_m: float,
               radius_m: float, tiefe_m: float, turn_left: bool,
               samples: int = 24) -> list[Point]:
    """Ω-Wende: erst von der Zielseite weg, dann im großen Bogen zurück.

    Zwei Bögen mit dem Wendekreis als engstem Radius: erst ein Stück von der
    Zielseite weg (Winkel α), dann ``180 + α`` Grad zur Zielseite herum. Die
    Maschine endet gegenläufig auf der Nachbarspur, ohne Rückwärtsgang. α wird
    so gesucht, dass der seitliche Versatz stimmt (ohne Übergänge:
    ``Versatz = 2 · Radius · cos α``).

    Zwei Entwurfsfehler, die der Dauerlauf (scripts/dauerlauf.py) aufgedeckt hat:
    eine Bézierkurve hatte im Scheitel Bögen unter einem Meter Radius, die keine
    Maschine fahren kann; und aneinandergesetzte Kreisbögen springen in der
    Krümmung von rechts nach links - die Lenkung braucht dafür Strecke, und die
    Maschine hat die Route verlassen, bevor sie da war. Deshalb rampt die
    Krümmung hier (Klothoide).

    Ist der Versatz mindestens zwei Wendekreise, braucht es kein Ausholen: dann
    ist die U-Wende die richtige Form. ``tiefe_m`` bleibt in der Signatur, ob die
    Wende ins Vorgewende passt, prüft ``route_im_feld``.
    """
    radius = max(1.5, radius_m)
    versatz = abs(versatz_m)
    if versatz >= 2.0 * radius:
        return plan_u_turn(start, heading, versatz, radius, turn_left, samples)
    richtung = 1.0 if turn_left else -1.0      # Vorzeichen der Wegfahrt: links -> erst rechts
    kappa = 1.0 / radius

    def route(alpha: float) -> list[Point]:
        # Das Ausrampen am Ende dreht noch ein Stück weiter: von der zweiten Phase abziehen.
        ausrampen = math.degrees(kappa * kappa / (2.0 * KRUEMMUNG_RATE))
        return _integriere(start, heading, [
            (richtung * kappa, alpha),
            (-richtung * kappa, max(1.0, 180.0 + alpha - ausrampen)),
        ])

    def versatz_zur_ziel_seite(pfad: list[Point]) -> float:
        sx, sy = _side(heading, turn_left)
        return (pfad[-1][0] - start[0]) * sx + (pfad[-1][1] - start[1]) * sy

    unten, oben = 0.0, 89.0
    if versatz_zur_ziel_seite(route(unten)) <= versatz:
        return route(unten)                     # schon ohne Ausholen nicht weiter als gewünscht
    for _ in range(40):
        mitte = 0.5 * (unten + oben)
        if versatz_zur_ziel_seite(route(mitte)) > versatz:
            unten = mitte
        else:
            oben = mitte
    return route(0.5 * (unten + oben))


def engster_radius(pfad: Sequence[Point], abstand_m: float = 0.6) -> float:
    """Kleinster Krümmungsradius entlang der Route (Umkreis dreier Punkte).

    Punkte dichter als ``abstand_m`` werden übersprungen, sonst macht das
    Rauschen der Stützpunkte aus einer Geraden einen Kreis. Eine gerade Route
    hat keinen engsten Bogen: ``inf``.
    """
    punkte = [pfad[0]] if pfad else []
    for p in pfad[1:]:
        if distance(p, punkte[-1]) >= abstand_m:
            punkte.append(p)
    kleinster = float("inf")
    for a, b, c in zip(punkte, punkte[1:], punkte[2:]):
        kreuz = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        if kreuz < 1e-9:
            continue
        kleinster = min(kleinster, distance(a, b) * distance(b, c) * distance(c, a)
                        / (2.0 * kreuz))
    return kleinster


def plan_u_turn(start: Point, heading: float, versatz_m: float,
                radius_m: float, turn_left: bool,
                samples: int = 24) -> list[Point]:
    """U-Wende: zwei Viertelkreise, dazwischen eine Gerade quer zur Spur.

    Braucht deutlich weniger Vorgewendetiefe als die Ω-Wende, weil nicht nach
    vorn ausgeholt wird - dafür wächst die Zwischengerade mit dem Spurversatz.
    Ist der Versatz kleiner als zwei Wendekreise, entfällt sie; die Maschine
    kommt dann enger heraus, als der Spurabstand wäre, und die Führung zieht
    den Rest auf der neuen Spur zurecht.
    """
    radius = max(1.5, radius_m)
    gerade = max(0.0, abs(versatz_m) - 2.0 * radius)

    bogen1 = _arc(start, heading, radius, 90.0, turn_left, samples)
    pfad = list(bogen1)

    zwischenkurs = normalize_heading(heading + (-90.0 if turn_left else 90.0))
    ende1 = bogen1[-1]
    start2 = ende1
    if gerade > 0.05:
        fx, fy = _forward(zwischenkurs)
        start2 = (ende1[0] + fx * gerade, ende1[1] + fy * gerade)
        pfad.append(start2)

    bogen2 = _arc(start2, zwischenkurs, radius, 90.0, turn_left, samples)
    pfad.extend(bogen2[1:])
    return pfad


def plan(start: Point, heading: float, settings: HeadlandSettings,
         spurabstand_m: float, tiefe_m: float, boundary=None) -> list[Point]:
    """Das eingestellte Muster planen - Ω oder U."""
    versatz = max(0.5, spurabstand_m) * max(1, settings.ueberspringen)
    links = settings.richtung == "links"
    if settings.muster == "dubins":
        from .dubins import kandidaten
        sx,sy=_side(heading,links)
        ziel=(start[0]+sx*versatz,start[1]+sy*versatz,(heading+180)%360)
        pfade=kandidaten((*start,heading),ziel,settings.radius_m)
        if boundary and settings.nur_im_feld:
            innen=[p for p in pfade if route_im_feld(p.punkte,boundary)]
            if innen:pfade=innen
        return pfade[0].punkte if pfade else []
    if settings.muster == "u":
        return plan_u_turn(start, heading, versatz, settings.radius_m, links)
    return plan_omega(start, heading, versatz, settings.radius_m,
                      tiefe_m, links)


def route_im_feld(pfad: Sequence[Point], boundary: Sequence[Point]) -> bool:
    """Liegt die geplante Route vollständig innerhalb der Feldgrenze?

    Vollständig heißt: jeder Punkt. Eine Route, die auch nur mit einem Stück
    hinausragt, führt die Maschine in den Graben, in den Zaun oder auf die
    Straße - dort hilft es nichts, dass der Rest sauber im Feld lag.
    """
    if len(boundary) < 3 or len(pfad) < 2:
        return False
    return all(point_in_polygon(p, boundary) for p in pfad)


# ---------------------------------------------------------------------------
# Die Wende fahren
# ---------------------------------------------------------------------------

WENDE_LENKFAKTOR = 2.1      # Grad Einschlag je Grad Kursfehler auf der Route
VORAUSBLICK_MIN_M = 2.5     # kürzester Vorausblick, auch im Stand
VORAUSBLICK_MAX_M = 6.0     # weiter voraus als der Bogen selbst hilft nicht


@dataclass
class TurnFollower:
    """Fährt eine geplante Route ab und meldet dabei wie eine Spur.

    Bewusst dieselbe Ausgabe wie die Spurführung (``GuidanceState``): der
    Lenkregler prüft unverändert weiter, ob die Abweichung noch im Rahmen liegt
    - nur bezieht sie sich während der Wende auf die Route statt auf die
    verlassene Spur. Ein eigener Weg an der Sicherheitsprüfung vorbei wäre
    genau die Abkürzung, die man in diesem Modul nicht nehmen darf.

    Beendet ist die Wende, wenn der letzte Punkt erreicht ist. Abgebrochen wird
    sie, sobald die Maschine der Route nicht mehr folgt - dann übernimmt der
    Fahrer, und die Führung fällt auf die Spur zurück.
    """

    pfad: list[Point] = field(default_factory=list)
    abbruch_abstand_m: float = 3.0
    index: int = 0
    fertig: bool = False
    abgebrochen: bool = False   # fertig, aber nicht am Ziel: der Fahrer übernimmt
    grund: str = ""

    @property
    def aktiv(self) -> bool:
        return bool(self.pfad) and not self.fertig

    def laenge_m(self) -> float:
        return sum(distance(self.pfad[i], self.pfad[i + 1])
                   for i in range(len(self.pfad) - 1))

    def _naechster(self, position: Point) -> tuple[float, float, int]:
        """Signierte Abweichung zur Route, Kurs des Stücks, Index davon."""
        bester = (float("inf"), 0.0, 0.0, 0)
        for i in range(len(self.pfad) - 1):
            a, b = self.pfad[i], self.pfad[i + 1]
            fuss, _, quer = project_on_segment(position, a, b)
            d = distance(position, fuss)
            if d < bester[0]:
                bester = (d, quer, heading_deg(a, b), i)
        return bester[1], bester[2], bester[3]

    def _zielpunkt(self, ab_index: int, position: Point,
                   vorausblick: float) -> Point:
        """Punkt auf der Route, ``vorausblick`` Meter voraus - ab dem Lotfußpunkt.

        Gemessen wird ab dem Punkt der Route, der der Maschine am nächsten liegt,
        nicht ab dem Anfang des Teilstücks. Bei dicht gesetzten Stützpunkten (ein
        Bogen) ist das dasselbe; auf einer langen Geraden zwischen zwei Bögen lag
        der Zielpunkt sonst *hinter* der Maschine, und sie lenkte voll herum.
        """
        a, b = self.pfad[ab_index], self.pfad[min(ab_index + 1, len(self.pfad) - 1)]
        fuss, _, _ = project_on_segment(position, a, b)
        rest = vorausblick
        von = fuss
        for i in range(ab_index, len(self.pfad) - 1):
            nach = self.pfad[i + 1]
            stueck = distance(von, nach)
            if stueck >= rest:
                anteil = rest / stueck if stueck > 0 else 0.0
                return (von[0] + (nach[0] - von[0]) * anteil,
                        von[1] + (nach[1] - von[1]) * anteil)
            rest -= stueck
            von = nach
        return self.pfad[-1]

    def solve(self, position: Point, heading: float, speed_ms: float,
              profile: VehicleProfile) -> GuidanceState:
        """Einen Schritt auf der Route - Abweichung, Kursfehler, Einschlag."""
        if len(self.pfad) < 2:
            self.fertig = True
            return GuidanceState(message="Keine Wenderoute")

        quer, _, index = self._naechster(position)
        self.index = index

        if abs(quer) > self.abbruch_abstand_m:
            self.abbrechen(f"Route verlassen ({abs(quer):.1f} m)")
            return GuidanceState(message=self.grund)

        if distance(position, self.pfad[-1]) < 1.5 and index >= len(self.pfad) - 3:
            self.fertig = True
            self.grund = "Wende beendet"
            return GuidanceState(message=self.grund)

        # Der Vorausblick wächst mit dem Tempo, aber nicht unbegrenzt: liegt er
        # weiter voraus als der Bogen tief ist, zielt er quer durch die Wende
        # hindurch statt an ihr entlang.
        vorausblick = min(VORAUSBLICK_MAX_M,
                          max(VORAUSBLICK_MIN_M, abs(speed_ms) * 1.2))
        ziel = self._zielpunkt(index, position, vorausblick)
        soll_kurs = heading_deg(position, ziel)
        kursfehler = angle_difference(soll_kurs, heading)

        # Pure Pursuit: der Einschlag, der die Hinterachse auf dem Kreis durch
        # den Zielpunkt hält. Ein linearer Ansatz (Einschlag ~ Kursfehler)
        # braucht in einem Bogen dauerhaft einen Kursfehler, um überhaupt den
        # Bogeneinschlag zu erzeugen - das Fahrzeug läuft dann nach außen, und
        # mit der trägen Lenkung eines echten Traktors verlässt es die Route.
        abstand = max(1.0, distance(position, ziel))
        einschlag = math.degrees(math.atan2(
            2.0 * profile.wheelbase_m * math.sin(math.radians(kursfehler)), abstand))
        einschlag = profile.einschlag_begrenzen(einschlag)

        return GuidanceState(
            active=True,
            mode="turn",
            cross_track_m=quer,
            pass_number=0,
            heading_error_deg=kursfehler,
            target_heading_deg=soll_kurs,
            steer_angle_deg=einschlag,
            distance_along_m=distance(position, self.pfad[-1]),
            lightbar=lightbar_offset(quer),
            message="Wende läuft",
        )

    def abbrechen(self, grund: str = "abgebrochen") -> None:
        self.fertig = True
        self.abgebrochen = True
        self.grund = grund

    def to_dict(self) -> dict:
        return {
            "aktiv": self.aktiv,
            "punkte": [list(p) for p in self.pfad],
            "index": self.index,
            "laenge_m": self.laenge_m(),
            "grund": self.grund,
        }
