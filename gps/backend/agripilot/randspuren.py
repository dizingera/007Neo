"""Spuren aus der Feldgrenze: jede gerade Kante wird eine AB-Spur, jeder
gebogene Rand eine Kurvenspur.

Die Grenze kommt meist aus MyTerra und hat an geraden Kanten wenige Punkte,
an gebogenen viele. Zerlegt wird sie mit Douglas-Peucker: was innerhalb
``TOLERANZ_M`` einer Sehne liegt, gilt als gerade. Eine gerade Strecke ab
``MIN_LAENGE_M`` wird eine AB-Spur; aufeinanderfolgende kurze Stücke, die nur
sanft abknicken, sind ein Bogen - zusammen ab ``MIN_LAENGE_M`` eine Kurvenspur.

Wie bei der Kontur liegt Spur 0 auf der Grenze selbst; jede weitere Spur liegt
eine Arbeitsbreite weiter. Der Spurabstand kommt beim Laden vom Gerät, die
Spuren passen also zu jeder Maschine.
"""

from __future__ import annotations

from typing import Sequence

from .geo import Point, distance, heading_deg, project_on_segment

MIN_LAENGE_M = 20.0
TOLERANZ_M = 0.75          # so weit darf ein Punkt von der Sehne abweichen und sie bleibt gerade
BOGEN_KNICK_MAX_DEG = 45.0  # schärfer ist eine Ecke, kein Bogen (zwischen kurzen Stücken)
SANFT_KNICK_DEG = 12.0      # bis hierhin gehören auch lange Stücke zu einem Bogen
NAMENS_PRAEFIX = "Rand "


def _knick(a: Point, b: Point, c: Point) -> float:
    """Richtungsänderung an b in Grad (0 = geradeaus)."""
    h1, h2 = heading_deg(a, b), heading_deg(b, c)
    return abs((h2 - h1 + 540.0) % 360.0 - 180.0)


def _ring_ohne_doppel(grenze: Sequence[Point]) -> list[Point]:
    punkte: list[Point] = []
    for p in grenze:
        p = (float(p[0]), float(p[1]))
        if not punkte or distance(p, punkte[-1]) > 0.05:
            punkte.append(p)
    if len(punkte) > 1 and distance(punkte[0], punkte[-1]) <= 0.05:
        punkte.pop()
    return punkte


def _douglas_peucker(punkte: list[Point], toleranz: float) -> list[int]:
    """Indizes der Punkte, die die Form tragen (erster und letzter immer)."""
    behalten = {0, len(punkte) - 1}
    stapel = [(0, len(punkte) - 1)]
    while stapel:
        anfang, ende = stapel.pop()
        weitester, abstand = -1, toleranz
        for i in range(anfang + 1, ende):
            fuss, _, _ = project_on_segment(punkte[i], punkte[anfang], punkte[ende])
            d = distance(punkte[i], fuss)
            if d > abstand:
                weitester, abstand = i, d
        if weitester >= 0:
            behalten.add(weitester)
            stapel.append((anfang, weitester))
            stapel.append((weitester, ende))
    return sorted(behalten)


def kanten(grenze: Sequence[Point], min_laenge_m: float = MIN_LAENGE_M,
           toleranz_m: float = TOLERANZ_M) -> list[dict]:
    """Gerade Kanten und Bögen der Grenze: [{mode, points, laenge_m}]."""
    ring = _ring_ohne_doppel(grenze)
    n = len(ring)
    if n < 3:
        return []
    # Beim schärfsten Knick anfangen - sonst schnitte der Anfang des Rings eine
    # gerade Kante in zwei Hälften, die einzeln zu kurz sein könnten.
    start = max(range(n), key=lambda i: _knick(ring[i - 1], ring[i], ring[(i + 1) % n]))
    linie = ring[start:] + ring[:start] + [ring[start]]
    stuetzen = _douglas_peucker(linie, toleranz_m)
    stuecke = [(stuetzen[k], stuetzen[k + 1]) for k in range(len(stuetzen) - 1)]

    # Stücke zu Läufen verbinden. Ein Lauf aus einem einzigen Stück ist eine
    # gerade Kante (AB), ein Lauf aus mehreren ein Bogen (Kurve). Verbunden wird
    # über leichte Knicke - ein sanft gebogener Rand zerfiele sonst in lauter
    # 30-m-Geraden, jede eine eigene AB-Spur. Kurze Stücke dürfen schärfer
    # abknicken (dichte Punkte im Bogen), an langen trennt schon ein mäßiger
    # Knick: dort ist es eine Ecke zwischen zwei Kanten. Eine lange Gerade, die
    # tangential in einen Bogen übergeht, bleibt eine eigene AB-Spur.
    laeufe: list[list[tuple[int, int]]] = []
    for i, j in stuecke:
        if laeufe:
            vi, vj = laeufe[-1][-1]
            knick = _knick(linie[vi], linie[vj], linie[j])
            vorher_lang = distance(linie[vi], linie[vj]) >= min_laenge_m
            jetzt_lang = distance(linie[i], linie[j]) >= min_laenge_m
            if vorher_lang and jetzt_lang:
                grenze_knick = SANFT_KNICK_DEG       # leicht geknickter langer Rand
            elif not vorher_lang and not jetzt_lang:
                grenze_knick = BOGEN_KNICK_MAX_DEG   # dicht gesetzter Bogen
            else:
                grenze_knick = -1.0                  # Gerade läuft in einen Bogen: dort trennen
            if knick <= grenze_knick:
                laeufe[-1].append((i, j))
                continue
        laeufe.append([(i, j)])

    ergebnis: list[dict] = []
    for lauf in laeufe:
        von, bis = lauf[0][0], lauf[-1][1]
        punkte = linie[von:bis + 1]
        laenge = sum(distance(punkte[k], punkte[k + 1]) for k in range(len(punkte) - 1))
        if laenge < min_laenge_m:
            continue
        if len(lauf) == 1:
            ergebnis.append({"mode": "ab", "points": [linie[von], linie[bis]], "laenge_m": laenge})
        else:
            ergebnis.append({"mode": "curve", "points": punkte, "laenge_m": laenge})
    return ergebnis


def _himmelsrichtung(von: Point, nach: Point) -> str:
    winkel = heading_deg(von, nach)
    namen = ["Nord", "Nordost", "Ost", "Südost", "Süd", "Südwest", "West", "Nordwest"]
    return namen[int(((winkel + 22.5) % 360.0) // 45.0)]


def benennen(kante: dict, grenze: Sequence[Point]) -> str:
    """Wo am Feld die Kante liegt: „Rand Nord 245 m“, „Rand Kurve West 80 m“."""
    ring = _ring_ohne_doppel(grenze)
    # Mitte der Ausdehnung, nicht der Punktdurchschnitt: ein Bogen mit vielen
    # Punkten zöge den Durchschnitt zu sich, und die Ostkante hieße "Südost".
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    mitte_feld = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    punkte = kante["points"]
    mitte = punkte[len(punkte) // 2] if kante["mode"] == "curve" else (
        (punkte[0][0] + punkte[-1][0]) / 2.0, (punkte[0][1] + punkte[-1][1]) / 2.0)
    art = "Kurve " if kante["mode"] == "curve" else ""
    return f"{NAMENS_PRAEFIX}{art}{_himmelsrichtung(mitte_feld, mitte)} {kante['laenge_m']:.0f} m"


def ist_randspur(name: str) -> bool:
    return name.startswith(NAMENS_PRAEFIX)


def runden(punkte: Sequence[Point]) -> list[list[float]]:
    return [[round(p[0], 3), round(p[1], 3)] for p in punkte]

