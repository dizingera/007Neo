"""Vorgewende: der Rand des Feldes - und das Wenden darin.

Das Vorgewende ist kein eigenes Feld, sondern eine Tiefe: so viele Arbeitsbreiten
vom Rand nach innen, wie zum Wenden gebraucht werden. Alles hier leitet sich
deshalb aus der gespeicherten Feldgrenze ab, nicht aus einer zweiten, getrennt
gepflegten Geometrie - eine zweite Geometrie läuft der ersten früher oder später
davon, und niemand merkt es.

Drei Dinge stehen hier:

* **Wie weit ist es noch?** Ein Strahl in Fahrtrichtung bis zur Feldgrenze,
  abzüglich der Vorgewendetiefe. Das ist die Zahl, die der Fahrer wirklich
  braucht, und sie kommt ohne Versatzpolygon aus.
* **Zwei Wendemuster.** Die Ω-Wende (weiter Bogen) und die U-Wende (zwei
  Viertelkreise mit Zwischengerade). Beide werden vor der Fahrt geplant und
  vollständig gegen die Feldgrenze geprüft.
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
MUSTER = ("omega", "u")
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
    """Die Feldgrenze um ``inset_m`` nach innen versetzt - als Zeichenhilfe.

    Jede Kante wird nach innen geschoben und mit ihren Nachbarn geschnitten; das
    hält Ecken spitz, statt sie wie ein Versatz über die Eckpunkte abzurunden.
    Bei einem schmalen Zipfel im Schlag kann sich das Ergebnis selbst
    überschlagen. Solche Punkte werden verworfen, aber es bleibt eine
    Zeichenhilfe: **die Führung rechnet nie damit**, sie misst den Abstand zur
    echten Grenze (siehe ``GuidanceLine`` im Kontur-Modus).
    """
    if len(boundary) < 3 or inset_m <= 0.0:
        return [tuple(p) for p in boundary]

    punkte = [tuple(p) for p in boundary]
    n = len(punkte)
    # Umlaufsinn: bei positiver Fläche (gegen den Uhrzeigersinn) zeigt die
    # Linksnormale nach innen, sonst die Rechtsnormale.
    flaeche = sum(punkte[i][0] * punkte[(i + 1) % n][1]
                  - punkte[(i + 1) % n][0] * punkte[i][1] for i in range(n))
    vorzeichen = 1.0 if flaeche > 0 else -1.0

    kanten = []
    for i in range(n):
        a, b = punkte[i], punkte[(i + 1) % n]
        laenge = distance(a, b)
        if laenge < 1e-9:
            continue
        ex, ey = (b[0] - a[0]) / laenge, (b[1] - a[1]) / laenge
        nx, ny = -ey * vorzeichen, ex * vorzeichen
        kanten.append(((a[0] + nx * inset_m, a[1] + ny * inset_m), (ex, ey)))
    if len(kanten) < 3:
        return []

    innen: list[Point] = []
    for i in range(len(kanten)):
        (px, py), (ex, ey) = kanten[i]
        (qx, qy), (fx, fy) = kanten[(i + 1) % len(kanten)]
        nenner = ex * fy - ey * fx
        if abs(nenner) < 1e-9:
            innen.append((qx, qy))      # fast parallel: der Versatzpunkt genügt
            continue
        t = ((qx - px) * fy - (qy - py) * fx) / nenner
        innen.append((px + ex * t, py + ey * t))

    # Aufräumen: was beim Versatz nach außen gerutscht oder zu nah an die Grenze
    # geraten ist, war ein Selbstüberschlag und hat im Ring nichts zu suchen.
    sauber = [p for p in innen
              if point_in_polygon(p, punkte)
              and (distance_to_boundary(p, punkte) or 0.0) > inset_m * 0.6]
    return sauber if len(sauber) >= 3 else []


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


def plan_omega(start: Point, heading: float, versatz_m: float,
               radius_m: float, tiefe_m: float, turn_left: bool,
               samples: int = 60) -> list[Point]:
    """Ω-Wende: erst nach vorn ausholen, dann zurück auf die Nachbarspur.

    Als kubische Bézierkurve, weil die Form dabei aus vier Punkten entsteht und
    nicht aus aneinandergesetzten Bögen: Start, zwei Stützpunkte weit voraus,
    und ein Zielpunkt auf der Nachbarspur. Weil der zweite Stützpunkt **vor**
    dem Ziel liegt, läuft die Kurve am Ende rückwärts in ihn hinein - genau der
    weite Bogen, der die Fahrtrichtung umkehrt, ohne dass ein Rückwärtsgang
    nötig wäre.

    Die Stützweite wächst mit der Vorgewendetiefe und mit dem Wendekreis: die
    Ω-Wende darf ausholen, das ist ihr Zweck. Wo dafür kein Platz ist, gehört
    die U-Wende hin.
    """
    fx, fy = _forward(heading)
    # Seitlicher Versatz zeigt in die Wenderichtung.
    sx, sy = _side(heading, turn_left)
    versatz = abs(versatz_m)
    ausholen = max(tiefe_m * 0.70, radius_m * 1.45)
    ziel_vor = max(tiefe_m * 0.35, radius_m * 0.55)

    p0 = tuple(start)
    p3 = (start[0] + sx * versatz + fx * ziel_vor,
          start[1] + sy * versatz + fy * ziel_vor)
    c1 = (p0[0] + fx * ausholen + sx * versatz * 0.18,
          p0[1] + fy * ausholen + sy * versatz * 0.18)
    c2 = (p3[0] + fx * ausholen - sx * versatz * 0.18,
          p3[1] + fy * ausholen - sy * versatz * 0.18)

    punkte: list[Point] = []
    for i in range(samples + 1):
        t = i / samples
        u = 1.0 - t
        punkte.append((
            p0[0] * u ** 3 + c1[0] * 3 * u * u * t + c2[0] * 3 * u * t * t + p3[0] * t ** 3,
            p0[1] * u ** 3 + c1[1] * 3 * u * u * t + c2[1] * 3 * u * t * t + p3[1] * t ** 3,
        ))
    return punkte


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
         spurabstand_m: float, tiefe_m: float) -> list[Point]:
    """Das eingestellte Muster planen - Ω oder U."""
    versatz = max(0.5, spurabstand_m) * max(1, settings.ueberspringen)
    links = settings.richtung == "links"
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
        """Punkt auf der Route, ``vorausblick`` Meter voraus."""
        rest = vorausblick
        for i in range(ab_index, len(self.pfad) - 1):
            stueck = distance(self.pfad[i], self.pfad[i + 1])
            if stueck >= rest:
                anteil = rest / stueck if stueck > 0 else 0.0
                a, b = self.pfad[i], self.pfad[i + 1]
                return (a[0] + (b[0] - a[0]) * anteil,
                        a[1] + (b[1] - a[1]) * anteil)
            rest -= stueck
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

        einschlag = max(-profile.max_steer_deg,
                        min(profile.max_steer_deg, kursfehler * WENDE_LENKFAKTOR))

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
        self.grund = grund

    def to_dict(self) -> dict:
        return {
            "aktiv": self.aktiv,
            "punkte": [list(p) for p in self.pfad],
            "index": self.index,
            "laenge_m": self.laenge_m(),
            "grund": self.grund,
        }
