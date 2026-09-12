"""Automatische Feldplanung: Arbeitsrichtung, Vorgewende, Bahnen, Reihenfolge.

Bis hierher entstand jede Spur beim Fahren: A setzen, B setzen, los. Das ist
richtig für den ersten Schlag - aber wer ein Feld zum dritten Mal bearbeitet,
weiß vorher, wie es am wenigsten Wenden kostet. Genau das rechnet dieses Modul
aus der Feldgrenze aus:

* **Arbeitsrichtung.** Die Richtung, in der das Feld mit den wenigsten Wenden
  auskommt. Gesucht wird nicht über eine Formel, sondern durch Ausprobieren:
  jede Kante der Grenze ist ein Kandidat (bei einem Rechteck liegt das Optimum
  genau dort), dazu ein grobes Raster über alle Richtungen und eine feine
  Nachsuche um den Sieger.
* **Vorgewende.** Die Ringe am Rand, auf denen gewendet wird. Ihre Tiefe kommt
  aus der Arbeitsbreite, gefahren werden sie als Kontur - das kann die Führung
  schon.
* **Bahnen.** Parallele Linien im Kern des Feldes, an der Grenze abgeschnitten.
* **Reihenfolge.** Nacheinander oder im Sprung, damit der Wendekreis passt.

Zur Geometrie, und warum sie so und nicht kürzer gerechnet wird
---------------------------------------------------------------

Der naheliegende Weg wäre, die Feldgrenze um die Vorgewendetiefe nach innen zu
versetzen und die Bahnen an dem entstandenen Vieleck abzuschneiden. Der Versatz
eines Vielecks ist aber genau dann unzuverlässig, wenn er gebraucht wird: an
einem spitzen Zipfel überschlägt er sich, an einer Einbuchtung entstehen
Schlaufen. ``headland.ring`` sagt das selbst - es ist eine Zeichenhilfe.

Hier wird deshalb nicht versetzt, sondern **gemessen**. Ein Punkt gehört zum
Kern, wenn er innerhalb der Grenze liegt *und* weiter als die Vorgewendetiefe
von ihr entfernt ist. Beides sind geprüfte Bausteine (``point_in_polygon``,
``distance_to_boundary``), beide stimmen auch an Zipfeln und Einbuchtungen.

In zwei Stufen, weil das eine schnell und das andere genau sein muss:

1. **Schnitt mit der Feldgrenze** - exakt und in einem Durchgang über die
   Kanten (``_abschnitte_im_feld``). Das sagt, wo die Bahn überhaupt im Feld
   liegt, und beim Suchen der Arbeitsrichtung genügt allein das: Dutzende
   Richtungen durchzurechnen kostet so Millisekunden statt Sekunden.
2. **Abschneiden des Vorgewendes** - abgetastet, weil eine Einbuchtung an der
   Seite einen Abschnitt mitten durchtrennen kann und man das nur sieht, wenn
   man hinschaut. Wo der Kern anfängt und aufhört, wird per
   Intervallhalbierung auf den Zentimeter nachgeschärft.

Das kostet Rechenzeit - einmal beim Planen, nicht im Fahren.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as datenfeld
from typing import Optional, Sequence

from .geo import (Point, distance, normalize_heading, point_in_polygon,
                  polygon_area, polygon_perimeter)
from .headland import distance_to_boundary, ring

# Abtastschritt beim Suchen der Bahnenden. Fein genug, dass ein zwei Meter
# breiter Zipfel nicht durchrutscht, grob genug, dass ein 40-ha-Schlag in
# Sekunden geplant ist. Die Ränder werden anschließend nachgeschärft.
ABTAST_M = 1.0

# Genauigkeit, mit der die Bahnenden nachgeschärft werden.
RAND_GENAU_M = 0.05

# Kürzer als das lohnt keine eigene Bahn - das ist ein Zipfel, der beim
# Vorgewende mitgenommen wird. Bezogen auf die Arbeitsbreite, weil bei 3 m
# Breite ein 8-m-Stück sinnvoll ist und bei 36 m nicht.
MIN_BAHN_FAKTOR = 0.5

MUSTER = ("fortlaufend", "sprung")


def _achsen(richtung_grad: float) -> tuple[Point, Point]:
    """Längs- und Querrichtung zu einem Kurs (0 = Nord, 90 = Ost)."""
    h = math.radians(richtung_grad)
    return (math.sin(h), math.cos(h)), (math.cos(h), -math.sin(h))


@dataclass(frozen=True)
class PlanEinstellungen:
    """Was der Fahrer vorgibt, bevor gerechnet wird."""

    arbeitsbreite_m: float
    vorgewende_breiten: float = 2.0     # Tiefe in Arbeitsbreiten
    richtung_grad: Optional[float] = None   # None = automatisch suchen
    muster: str = "fortlaufend"
    wenderadius_m: float = 6.0
    geschwindigkeit_kmh: float = 8.0

    def normalisiert(self) -> "PlanEinstellungen":
        return PlanEinstellungen(
            arbeitsbreite_m=max(0.5, min(60.0, float(self.arbeitsbreite_m))),
            vorgewende_breiten=max(0.0, min(10.0, float(self.vorgewende_breiten))),
            richtung_grad=(None if self.richtung_grad is None
                           else normalize_heading(float(self.richtung_grad))),
            muster=self.muster if self.muster in MUSTER else "fortlaufend",
            wenderadius_m=max(1.0, min(30.0, float(self.wenderadius_m))),
            geschwindigkeit_kmh=max(1.0, min(30.0, float(self.geschwindigkeit_kmh))),
        )

    @property
    def vorgewende_tiefe_m(self) -> float:
        return self.vorgewende_breiten * self.arbeitsbreite_m

    def to_dict(self) -> dict:
        return {
            "arbeitsbreite_m": self.arbeitsbreite_m,
            "vorgewende_breiten": self.vorgewende_breiten,
            "richtung_grad": self.richtung_grad,
            "muster": self.muster,
            "wenderadius_m": self.wenderadius_m,
            "geschwindigkeit_kmh": self.geschwindigkeit_kmh,
        }

    @classmethod
    def from_dict(cls, werte: Optional[dict]) -> "PlanEinstellungen":
        werte = dict(werte or {})
        richtung = werte.get("richtung_grad")
        return cls(
            arbeitsbreite_m=float(werte.get("arbeitsbreite_m", 3.0)),
            vorgewende_breiten=float(werte.get("vorgewende_breiten", 2.0)),
            richtung_grad=(None if richtung in (None, "") else float(richtung)),
            muster=str(werte.get("muster", "fortlaufend")),
            wenderadius_m=float(werte.get("wenderadius_m", 6.0)),
            geschwindigkeit_kmh=float(werte.get("geschwindigkeit_kmh", 8.0)),
        ).normalisiert()


@dataclass
class Bahn:
    """Ein am Kern abgeschnittenes Stück einer Parallelspur.

    Eine Spur kann in mehrere Bahnen zerfallen - überall dort, wo eine
    Einbuchtung oder ein Hindernis den Kern in zwei Teile trennt. Gefahren wird
    jedes Stück für sich, also ist jedes Stück eine eigene Bahn.
    """

    nummer: int          # Reihenfolge im Plan, ab 1
    spur: int            # Nummer der Parallelspur, ab 0 - Nachbarn erkennt man hieran
    start: Point
    ende: Point
    richtung: float
    laenge_m: float

    def umgedreht(self) -> "Bahn":
        """Dieselbe Bahn, von der anderen Seite gefahren."""
        return Bahn(self.nummer, self.spur, self.ende, self.start,
                    normalize_heading(self.richtung + 180.0), self.laenge_m)

    def to_dict(self) -> dict:
        return {
            "nummer": self.nummer,
            "spur": self.spur,
            "start": list(self.start),
            "ende": list(self.ende),
            "richtung": round(self.richtung, 2),
            "laenge_m": round(self.laenge_m, 1),
        }


@dataclass
class Feldplan:
    """Das fertige Ergebnis: Bahnen, Ringe und die Zahlen dazu."""

    einstellungen: PlanEinstellungen
    richtung_grad: float
    automatisch: bool
    bahnen: list[Bahn]
    ringe: list[list[Point]] = datenfeld(default_factory=list)
    feld_flaeche_ha: float = 0.0
    kern_flaeche_ha: float = 0.0
    vorgewende_flaeche_ha: float = 0.0
    arbeitsstrecke_m: float = 0.0
    wendestrecke_m: float = 0.0
    ringstrecke_m: float = 0.0

    @property
    def spuren(self) -> int:
        return len({b.spur for b in self.bahnen})

    @property
    def wenden(self) -> int:
        return max(0, len(self.bahnen) - 1)

    @property
    def strecke_gesamt_m(self) -> float:
        return self.arbeitsstrecke_m + self.wendestrecke_m + self.ringstrecke_m

    @property
    def dauer_min(self) -> float:
        tempo_ms = self.einstellungen.geschwindigkeit_kmh / 3.6
        return self.strecke_gesamt_m / tempo_ms / 60.0 if tempo_ms > 0 else 0.0

    def bahn(self, nummer: int) -> Optional[Bahn]:
        for b in self.bahnen:
            if b.nummer == nummer:
                return b
        return None

    def to_dict(self) -> dict:
        return {
            "einstellungen": self.einstellungen.to_dict(),
            "richtung_grad": round(self.richtung_grad, 2),
            "automatisch": self.automatisch,
            "bahnen": [b.to_dict() for b in self.bahnen],
            "ringe": [[list(p) for p in r] for r in self.ringe],
            "feld_flaeche_ha": round(self.feld_flaeche_ha, 3),
            "kern_flaeche_ha": round(self.kern_flaeche_ha, 3),
            "vorgewende_flaeche_ha": round(self.vorgewende_flaeche_ha, 3),
            "spuren": self.spuren,
            "bahnen_anzahl": len(self.bahnen),
            "wenden": self.wenden,
            "arbeitsstrecke_m": round(self.arbeitsstrecke_m),
            "wendestrecke_m": round(self.wendestrecke_m),
            "ringstrecke_m": round(self.ringstrecke_m),
            "strecke_gesamt_m": round(self.strecke_gesamt_m),
            "dauer_min": round(self.dauer_min, 1),
        }


# ---------------------------------------------------------------------------
# Kern des Feldes
# ---------------------------------------------------------------------------


def im_kern(punkt: Point, grenze: Sequence[Point], tiefe_m: float) -> bool:
    """Liegt der Punkt im bearbeitbaren Kern, also hinter dem Vorgewende?

    Gemessen statt versetzt - siehe der Hinweis oben im Modul. Bei Tiefe 0 ist
    der Kern das ganze Feld.
    """
    if not point_in_polygon(punkt, grenze):
        return False
    if tiefe_m <= 0.0:
        return True
    abstand = distance_to_boundary(punkt, grenze)
    return abstand is not None and abstand >= tiefe_m


def _rand_schaerfen(drinnen: Point, draussen: Point, grenze: Sequence[Point],
                    tiefe_m: float) -> Point:
    """Die Kante zwischen Kern und Rand per Intervallhalbierung einkreisen.

    Die Abtastung findet den Übergang nur auf ``ABTAST_M`` genau. Ein halber
    Meter am Bahnende ist auf 40 Bahnen ein Hektar Unterschied in der Rechnung
    und im Feld ein sichtbarer Absatz - also wird nachgeschärft.
    """
    a, b = drinnen, draussen
    while distance(a, b) > RAND_GENAU_M:
        mitte = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        if im_kern(mitte, grenze, tiefe_m):
            a = mitte
        else:
            b = mitte
    return a


def _spur_stuecke(v: float, richtung: float, grenze: Sequence[Point],
                  tiefe_m: float, u_von: float, u_bis: float,
                  min_laenge: float) -> list[tuple[Point, Point]]:
    """Ein Stück Parallele innerhalb der Feldgrenze am Vorgewende abschneiden.

    ``u_von`` bis ``u_bis`` ist bereits ein Abschnitt *innerhalb* der Grenze,
    geliefert von ``_abschnitte_im_feld``. Abgeschnitten wird hier nur noch das
    Vorgewende, und das durch Abtasten: eine Einbuchtung an der Seite kann den
    Abschnitt in der Mitte durchtrennen, und das sieht man nur, wenn man
    hinschaut. Wo der Kern anfängt und aufhört, wird anschließend per
    Intervallhalbierung nachgeschärft.
    """
    laengs, quer = _achsen(richtung)
    basis = (quer[0] * v, quer[1] * v)

    def punkt(u: float) -> Point:
        return (basis[0] + laengs[0] * u, basis[1] + laengs[1] * u)

    # Ohne Vorgewende ist der Kern das ganze Feld - dann ist der Abschnitt schon
    # das Ergebnis, exakt und ohne einen einzigen Abtastschritt.
    if tiefe_m <= 0.0:
        anfang, ende = punkt(u_von), punkt(u_bis)
        return [(anfang, ende)] if distance(anfang, ende) >= min_laenge else []

    stuecke: list[tuple[Point, Point]] = []
    lauf_start: Optional[float] = None
    vorher_drin = False
    u = u_von
    while u <= u_bis + ABTAST_M:
        u_jetzt = min(u, u_bis)
        drin = im_kern(punkt(u_jetzt), grenze, tiefe_m)
        if drin and not vorher_drin:
            lauf_start = u_jetzt
        elif not drin and vorher_drin and lauf_start is not None:
            anfang = (_rand_schaerfen(punkt(lauf_start), punkt(lauf_start - ABTAST_M),
                                      grenze, tiefe_m)
                      if lauf_start > u_von else punkt(lauf_start))
            ende = _rand_schaerfen(punkt(u_jetzt - ABTAST_M), punkt(u_jetzt),
                                   grenze, tiefe_m)
            if distance(anfang, ende) >= min_laenge:
                stuecke.append((anfang, ende))
            lauf_start = None
        vorher_drin = drin
        if u_jetzt >= u_bis:
            break
        u += ABTAST_M

    if vorher_drin and lauf_start is not None:
        anfang = (_rand_schaerfen(punkt(lauf_start), punkt(lauf_start - ABTAST_M),
                                  grenze, tiefe_m)
                  if lauf_start > u_von else punkt(lauf_start))
        ende = punkt(u_bis)
        if distance(anfang, ende) >= min_laenge:
            stuecke.append((anfang, ende))
    return stuecke


def _spur_bereich(grenze: Sequence[Point], richtung: float) -> tuple[float, float,
                                                                    float, float]:
    """Ausdehnung des Feldes längs und quer zur Arbeitsrichtung."""
    laengs, quer = _achsen(richtung)
    us = [p[0] * laengs[0] + p[1] * laengs[1] for p in grenze]
    vs = [p[0] * quer[0] + p[1] * quer[1] for p in grenze]
    return min(us), max(us), min(vs), max(vs)


def _abschnitte_im_feld(v: float, uv_grenze: Sequence[tuple[float, float]]
                        ) -> list[tuple[float, float]]:
    """Wo liegt die Parallele im Abstand ``v`` innerhalb der Feldgrenze?

    Exakt gerechnet, nicht abgetastet: die Grenze ist ein Streckenzug, und wo
    eine Kante die Höhe ``v`` überquert, steht der Durchstoßpunkt in einer
    Zeile. Sortiert man die Durchstoßpunkte, liegt abwechselnd Feld und
    Nicht-Feld dazwischen - dieselbe Gerade-Ungerade-Regel, nach der auch
    ``point_in_polygon`` arbeitet.

    Das ist der schnelle Vorfilter: er sagt, *wo überhaupt* abgetastet werden
    muss, und beim Suchen der Arbeitsrichtung ersetzt er das Abtasten ganz.
    Übergeben wird die Grenze bereits in Bahnkoordinaten (längs, quer).
    """
    kreuzungen: list[float] = []
    n = len(uv_grenze)
    for i in range(n):
        u1, v1 = uv_grenze[i]
        u2, v2 = uv_grenze[(i + 1) % n]
        if (v1 > v) != (v2 > v):
            kreuzungen.append(u1 + (v - v1) * (u2 - u1) / (v2 - v1))
    kreuzungen.sort()
    return [(kreuzungen[i], kreuzungen[i + 1])
            for i in range(0, len(kreuzungen) - 1, 2)]


def _bahnkoordinaten(grenze: Sequence[Point],
                     richtung: float) -> list[tuple[float, float]]:
    """Die Feldgrenze in Bahnkoordinaten: längs zur Arbeitsrichtung, quer dazu."""
    laengs, quer = _achsen(richtung)
    return [(p[0] * laengs[0] + p[1] * laengs[1],
             p[0] * quer[0] + p[1] * quer[1]) for p in grenze]


def _spurabstaende(uv_grenze: Sequence[tuple[float, float]],
                   breite: float) -> list[float]:
    """Die Querabstände der Parallelspuren.

    Die erste Spur liegt eine halbe Arbeitsbreite hinter dem Rand des Feldes -
    so deckt die Bahn den Streifen bis zum Rand ab, statt mit der Mitte auf dem
    Rand zu liegen und die Hälfte zu vergeuden.
    """
    vs = [v for _, v in uv_grenze]
    v_von, v_bis = min(vs), max(vs)
    abstaende = []
    v = v_von + breite / 2.0
    while v <= v_bis:
        abstaende.append(v)
        v += breite
    return abstaende


def bahnen_fuer(grenze: Sequence[Point], richtung: float, breite: float,
                tiefe_m: float) -> list[tuple[int, Point, Point]]:
    """Alle Bahnstücke einer Arbeitsrichtung, als (Spurnummer, Start, Ende)."""
    if len(grenze) < 3 or breite <= 0.0:
        return []
    uv = _bahnkoordinaten(grenze, richtung)
    min_laenge = breite * MIN_BAHN_FAKTOR
    ergebnis: list[tuple[int, Point, Point]] = []
    for spur, v in enumerate(_spurabstaende(uv, breite)):
        for u_von, u_bis in _abschnitte_im_feld(v, uv):
            for anfang, ende in _spur_stuecke(v, richtung, grenze, tiefe_m,
                                              u_von, u_bis, min_laenge):
                ergebnis.append((spur, anfang, ende))
    return ergebnis


# ---------------------------------------------------------------------------
# Arbeitsrichtung
# ---------------------------------------------------------------------------


def _bewerten(grenze: Sequence[Point], richtung: float, breite: float,
              tiefe_m: float) -> tuple[int, float]:
    """Güte einer Arbeitsrichtung: (Anzahl Bahnen, Gesamtlänge). Kleiner ist besser.

    Zuerst zählen die Bahnen, denn jede kostet eine Wende, und die Wende kostet
    Zeit, Sprit und einen verdichteten Streifen am Feldrand.

    Bei gleich vielen Bahnen entscheidet die kürzere Gesamtstrecke. Das klingt
    verkehrt herum - länger wäre doch mehr Fläche? Ist es nicht: der Kern des
    Feldes steht fest, egal aus welcher Richtung man ihn anfährt. Mehr Meter für
    denselben Kern heißen nur, dass die Bahnen schräg liegen und sich stärker
    überlappen. Auf einem Rechteck fallen dadurch genau die Richtungen heraus,
    die einen Hauch schief zur langen Seite stehen und sonst zufällig gewinnen
    würden.

    Gerechnet wird ohne Abtasten - nur der exakte Schnitt mit der Feldgrenze,
    an beiden Enden um die Vorgewendetiefe gekürzt. Für den Vergleich von
    Dutzenden Richtungen genügt das und kostet statt Sekunden nichts; die
    genaue Geometrie entsteht erst, wenn der Sieger feststeht.
    """
    uv = _bahnkoordinaten(grenze, richtung)
    min_laenge = breite * MIN_BAHN_FAKTOR
    anzahl, gesamt = 0, 0.0
    for v in _spurabstaende(uv, breite):
        for u_von, u_bis in _abschnitte_im_feld(v, uv):
            laenge = (u_bis - u_von) - 2.0 * tiefe_m
            if laenge >= min_laenge:
                anzahl += 1
                gesamt += laenge
    if anzahl == 0:
        return (10 ** 6, 0.0)
    return (anzahl, round(gesamt, 3))


def beste_richtung(grenze: Sequence[Point], breite: float,
                   tiefe_m: float) -> float:
    """Die Arbeitsrichtung mit den wenigsten Bahnen.

    Kandidaten sind erstens die Kanten der Feldgrenze - bei jedem Feld, das
    einigermaßen gerade Seiten hat, liegt das Optimum auf einer davon -, zweitens
    ein grobes Raster über alle Richtungen für alles Runde und Krumme, drittens
    eine feine Nachsuche um den Sieger. Richtung und Gegenrichtung sind
    dieselbe Bahnschar, also genügt der Halbkreis.
    """
    if len(grenze) < 3:
        return 0.0

    kandidaten: set[float] = set()
    n = len(grenze)
    for i in range(n):
        a, b = grenze[i], grenze[(i + 1) % n]
        if distance(a, b) < 1e-6:
            continue
        kandidaten.add(round(
            math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 180.0, 3))
    kandidaten.update(float(g) for g in range(0, 180, 10))

    bewertet = [(_bewerten(grenze, g, breite, tiefe_m), g) for g in sorted(kandidaten)]
    beste = min(bewertet)[1]

    for schritt in (3.0, 1.0):
        umgebung = [beste + k * schritt for k in (-2, -1, 1, 2)]
        bewertet = [(_bewerten(grenze, g % 180.0, breite, tiefe_m), g % 180.0)
                    for g in umgebung]
        bewertet.append((_bewerten(grenze, beste, breite, tiefe_m), beste))
        beste = min(bewertet)[1]
    return normalize_heading(beste)


# ---------------------------------------------------------------------------
# Reihenfolge
# ---------------------------------------------------------------------------


def sprungweite(wenderadius_m: float, breite_m: float) -> int:
    """Wie viele Spuren übersprungen werden müssen, damit die Wende passt.

    Bei einer Ω-Wende auf die direkte Nachbarspur muss die Maschine einen Bogen
    fahren, der breiter ist als der Spurabstand - bei 6 m Wenderadius und 3 m
    Arbeitsbreite geht das nicht ohne Rangieren. Gebraucht wird ein Versatz von
    zwei Wenderadien; wie viele Spuren das sind, steht hier.
    """
    if breite_m <= 0.0:
        return 1
    return max(1, math.ceil(2.0 * wenderadius_m / breite_m))


def reihenfolge(spuren: Sequence[int], muster: str, sprung: int) -> list[int]:
    """Die Spurnummern in der Reihenfolge, in der sie gefahren werden.

    ``fortlaufend`` fährt sie der Reihe nach - richtig, wenn der Wendekreis auf
    die Nachbarspur passt. ``sprung`` fährt in Schritten von ``sprung`` durch,
    kehrt ans Feldende zurück und füllt die ausgelassenen Spuren auf. Jede
    Wende ist dann weit genug; der Preis sind ``sprung`` lange Fahrten über das
    Vorgewende, die in der Streckenrechnung auch als solche auftauchen.
    """
    geordnet = sorted(set(spuren))
    if muster != "sprung" or sprung <= 1:
        return geordnet
    ergebnis: list[int] = []
    for start in range(sprung):
        ergebnis.extend(geordnet[i] for i in range(start, len(geordnet), sprung))
    return ergebnis


def _wendestrecke(bahnen: Sequence[Bahn], wenderadius_m: float) -> float:
    """Geschätzte Strecke zwischen den Bahnen.

    Geschätzt, nicht gerechnet: die wirkliche Wende plant ``headland.plan``,
    und die hängt an Position, Kurs und Feldgrenze. Für den Plan genügt die
    untere Grenze - Luftlinie zum nächsten Anfang, aber nie kürzer als ein
    Halbkreis mit dem Wendekreis der Maschine, weil die Maschine nicht auf der
    Stelle dreht.
    """
    gesamt = 0.0
    for vorher, nachher in zip(bahnen, bahnen[1:]):
        direkt = distance(vorher.ende, nachher.start)
        gesamt += max(direkt, math.pi * wenderadius_m)
    return gesamt


# ---------------------------------------------------------------------------
# Der ganze Plan
# ---------------------------------------------------------------------------


def vorgewende_ringe(grenze: Sequence[Point], breite: float,
                     tiefe_m: float) -> list[list[Point]]:
    """Die Ringspuren des Vorgewendes, von außen nach innen.

    Ring 0 liegt eine halbe Arbeitsbreite innerhalb der Grenze, jeder weitere
    eine Breite weiter drinnen - dieselbe Zählung wie bei den Bahnen. Gefahren
    werden sie im Konturmodus, der die Grenze selbst als Bezug nimmt; diese
    Polygone sind die Vorschau dazu.
    """
    if len(grenze) < 3 or breite <= 0.0 or tiefe_m <= 0.0:
        return []
    ringe = []
    anzahl = max(1, int(round(tiefe_m / breite)))
    for k in range(anzahl):
        versatz = (k + 0.5) * breite
        if versatz >= tiefe_m + breite / 2.0:
            break
        polygon = ring(grenze, versatz)
        if len(polygon) >= 3:
            ringe.append(polygon)
    return ringe


def planen(grenze: Sequence[Point],
           einstellungen: PlanEinstellungen) -> Feldplan:
    """Aus der Feldgrenze einen vollständigen Arbeitsplan rechnen."""
    einstellungen = einstellungen.normalisiert()
    grenze = [tuple(p) for p in grenze]
    if len(grenze) < 3:
        raise ValueError("Für einen Plan braucht es eine Feldgrenze mit "
                         "mindestens drei Punkten")

    breite = einstellungen.arbeitsbreite_m
    tiefe = einstellungen.vorgewende_tiefe_m

    automatisch = einstellungen.richtung_grad is None
    richtung = (beste_richtung(grenze, breite, tiefe) if automatisch
                else einstellungen.richtung_grad)

    stuecke = bahnen_fuer(grenze, richtung, breite, tiefe)
    nach_spur: dict[int, list[tuple[Point, Point]]] = {}
    for spur, anfang, ende in stuecke:
        nach_spur.setdefault(spur, []).append((anfang, ende))

    folge = reihenfolge(nach_spur.keys(), einstellungen.muster,
                        sprungweite(einstellungen.wenderadius_m, breite))

    bahnen: list[Bahn] = []
    nummer = 0
    letztes_ende: Optional[Point] = None
    for spur in folge:
        for anfang, ende in nach_spur[spur]:
            nummer += 1
            # Gefahren wird abwechselnd hin und zurück: das Ende der letzten
            # Bahn ist der Anfang der Wende, und die soll kurz sein. Ohne das
            # stünde nach jeder Bahn die ganze Feldlänge als Leerfahrt an.
            if letztes_ende is not None and \
                    distance(letztes_ende, ende) < distance(letztes_ende, anfang):
                anfang, ende = ende, anfang
            laenge = distance(anfang, ende)
            bahnen.append(Bahn(
                nummer=nummer, spur=spur, start=anfang, ende=ende,
                richtung=(math.degrees(math.atan2(ende[0] - anfang[0],
                                                  ende[1] - anfang[1])) % 360.0),
                laenge_m=laenge,
            ))
            letztes_ende = ende

    ringe = vorgewende_ringe(grenze, breite, tiefe)
    arbeitsstrecke = sum(b.laenge_m for b in bahnen)
    feld_flaeche = polygon_area(grenze) / 10_000.0
    # Die Kernfläche aus den Bahnen: jede Bahn deckt ihre Länge mal die
    # Arbeitsbreite ab. Genau das wird auch wirklich bearbeitet - eine aus dem
    # versetzten Vieleck gerechnete Fläche wäre glatter, aber nicht das, was
    # die Maschine hinterlässt.
    kern_flaeche = min(feld_flaeche, arbeitsstrecke * breite / 10_000.0)

    return Feldplan(
        einstellungen=einstellungen,
        richtung_grad=richtung,
        automatisch=automatisch,
        bahnen=bahnen,
        ringe=ringe,
        feld_flaeche_ha=feld_flaeche,
        kern_flaeche_ha=kern_flaeche,
        vorgewende_flaeche_ha=max(0.0, feld_flaeche - kern_flaeche),
        arbeitsstrecke_m=arbeitsstrecke,
        wendestrecke_m=_wendestrecke(bahnen, einstellungen.wenderadius_m),
        ringstrecke_m=sum(polygon_perimeter(r) for r in ringe),
    )


# ---------------------------------------------------------------------------
# Fortschritt: was ist erledigt, was steht noch an
# ---------------------------------------------------------------------------

# Ab diesem Anteil gilt eine Bahn als gefahren. Nicht 100 %: an den Enden
# fehlen fast immer ein paar Meter, weil die Wende früher beginnt als die Bahn
# endet, und eine Bahn, die deshalb ewig offen bliebe, wäre als Anzeige
# wertlos.
ERLEDIGT_AB = 0.9

# Abstand der Stichproben entlang einer Bahn. Die bearbeitete Fläche liegt als
# Raster mit einer halben Meter Kantenlänge vor - feiner zu messen bringt
# nichts.
PROBE_M = 2.0


@dataclass
class BahnFortschritt:
    nummer: int
    anteil: float
    erledigt: bool

    def to_dict(self) -> dict:
        return {"nummer": self.nummer, "anteil": round(self.anteil, 3),
                "erledigt": self.erledigt}


@dataclass
class Planfortschritt:
    """Wie weit der Plan abgearbeitet ist."""

    bahnen: list[BahnFortschritt]
    erledigt_anzahl: int
    offen_anzahl: int
    flaechen_anteil: float
    rest_ha: float
    naechste: Optional[int]

    def to_dict(self) -> dict:
        return {
            "bahnen": [b.to_dict() for b in self.bahnen],
            "erledigt_anzahl": self.erledigt_anzahl,
            "offen_anzahl": self.offen_anzahl,
            "flaechen_anteil": round(self.flaechen_anteil, 3),
            "prozent": round(self.flaechen_anteil * 100.0, 1),
            "rest_ha": round(self.rest_ha, 2),
            "naechste": self.naechste,
        }


def bahn_anteil(bahn: Bahn, ist_bearbeitet, schritt: float = PROBE_M) -> float:
    """Welcher Anteil einer Bahn schon bearbeitet ist.

    ``ist_bearbeitet`` ist eine Funktion Punkt -> bool; im Betrieb ist das
    ``CoverageMap.is_covered``. Die Bahn wird in gleichmäßigen Schritten
    abgetastet und ausgezählt - das ist robust gegen Lücken in der Mitte, die
    eine reine Längenrechnung übersehen würde.
    """
    if bahn.laenge_m <= 0.0:
        return 0.0
    anzahl = max(2, int(bahn.laenge_m / max(0.5, schritt)) + 1)
    treffer = 0
    for i in range(anzahl):
        t = i / (anzahl - 1)
        punkt = (bahn.start[0] + t * (bahn.ende[0] - bahn.start[0]),
                 bahn.start[1] + t * (bahn.ende[1] - bahn.start[1]))
        if ist_bearbeitet(punkt):
            treffer += 1
    return treffer / anzahl


def fortschritt(plan: Feldplan, ist_bearbeitet,
                ab_position: Optional[Point] = None,
                schwelle: float = ERLEDIGT_AB) -> Planfortschritt:
    """Den Plan gegen die bearbeitete Fläche halten.

    ``naechste`` ist die Bahn, die als nächstes drankommt: die nächstgelegene
    offene Bahn, wenn eine Position bekannt ist, sonst die erste offene in der
    geplanten Reihenfolge. Die nächstgelegene ist die ehrlichere Antwort -
    wer die Reihenfolge einmal verlassen hat, will nicht ans andere Feldende
    geschickt werden, nur weil dort eine Lücke blieb.
    """
    stand = [BahnFortschritt(b.nummer, a, a >= schwelle)
             for b, a in ((b, bahn_anteil(b, ist_bearbeitet)) for b in plan.bahnen)]
    nach_nummer = {b.nummer: b for b in plan.bahnen}
    offen = [s for s in stand if not s.erledigt]
    breite = plan.einstellungen.arbeitsbreite_m

    rest_m = sum(nach_nummer[s.nummer].laenge_m * (1.0 - s.anteil) for s in offen)
    gesamt_m = plan.arbeitsstrecke_m

    naechste: Optional[int] = None
    if offen:
        if ab_position is None:
            naechste = offen[0].nummer
        else:
            naechste = min(
                offen,
                key=lambda s: min(distance(ab_position, nach_nummer[s.nummer].start),
                                  distance(ab_position, nach_nummer[s.nummer].ende)),
            ).nummer

    return Planfortschritt(
        bahnen=stand,
        erledigt_anzahl=len(stand) - len(offen),
        offen_anzahl=len(offen),
        flaechen_anteil=(1.0 - rest_m / gesamt_m) if gesamt_m > 0 else 0.0,
        rest_ha=rest_m * breite / 10_000.0,
        naechste=naechste,
    )
