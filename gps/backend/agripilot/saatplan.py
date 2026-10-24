"""Saatplan: einen Schlag vor der Aussaat ganz durchplanen - Säspuren und Fahrgassen.

Vorgabe aus der Praxis: an jeder Feldgrenze erst ``randspuren`` normale
Säspuren, dann die Fahrgasse. Bei 3 m Sämaschine und 21 m Fahrgassenweite sind
das drei Säspuren und die Fahrgasse in der vierten - ihre Mitte liegt auf
10,5 m, der halben Fahrgassenweite: die Spritze deckt von dort genau den Rand
bis 21 m ab. Das geht nur auf, wenn die Fahrgassenweite eine ungerade Zahl von
Sämaschinenbreiten ist: ``(2 * randspuren + 1) * breite == fahrgassenweite``.

Aufbau des Plans:

* **Vorgewende** rundum, so tief wie eine Fahrgassenweite: ``2r + 1`` Runden,
  die mittlere ist die Vorgewende-Fahrgasse (r Säspuren, Fahrgasse, r Säspuren).
* **Hauptfeld** parallel zur gewählten Startkante, im Raster der Sämaschine.
  Die Fahrgassen liegen auf ``fahrgassenweite / 2 + j * fahrgassenweite`` von
  der Startkante - an der Startkante also genau nach der Randvorgabe.
* **Prüfung** jeder anderen Kante, die zur Fahrtrichtung (fast) parallel
  liegt: dort muss die letzte Fahrgasse des Hauptfeldes genau eine
  Fahrgassenweite vor der Vorgewende-Fahrgasse liegen. Sonst bleibt ein
  Streifen ohne Dünger und Pflanzenschutz oder wird doppelt behandelt - das ist
  der Konflikt, den der Plan zeigt, mit Vorschlägen zur Anpassung.

Alles in Feldmetern (x Ost, y Nord), wie Grenze und Spuren gespeichert sind.
Nur Rechnung, kein Zustand: die Engine speichert den Plan und übernimmt ihn
auf Wunsch als Saisonspur.
"""

from __future__ import annotations

import math
import re
from typing import Optional, Sequence

from . import headland
from . import randspuren
from .geo import Point, distance, heading_deg, point_in_polygon

PARALLEL_DEG = 20.0      # bis hierhin gilt eine Kante als parallel zur Fahrtrichtung
TOLERANZ_M = 0.3         # so weit darf eine Fahrgasse vom Sollabstand abweichen
KLEINE_LUECKE_M = 5.0    # darunter keine Zusatz-Fahrgasse vorschlagen


def startkanten(grenze: Sequence[Point]) -> list[dict]:
    """Die Kanten, an denen ein Plan beginnen kann - je zwei Startecken.

    Jede Kante ab 20 m (wie bei den Randspuren). Bei Bögen gibt die Sehne die
    Richtung vor. Vorn stehen die geraden und fast geraden (``fast_gerade``),
    die längste davon ist vorgewählt: an ihr fängt man beim Säen an. Stärker
    gebogene folgen als "(Bogen, Stich x m)" - am 24.10.2026 waren 51 Kanten auf 25
    Schlägen gar nicht wählbar, die meisten Feldränder mit 2-3 m Stich.
    """
    ring = [tuple(map(float, p)) for p in grenze]
    if len(ring) < 3:
        return []
    alle = randspuren.kanten(ring)
    # Kennungen bleiben, was sie waren: eine gespeicherte Vorgabe ("1a") wird beim
    # Übernehmen neu gerechnet. Gerade Kanten zählen 0, 1, 2 … wie bisher; Bögen tragen
    # ihre feste Nummer an der Grenze (k7 = siebte Kante), egal wie die Toleranz steht.
    geraden = [k for k in alle if k["mode"] == "ab"]
    if geraden:
        auswahl = [(str(i), k) for i, k in enumerate(geraden)]
        auswahl += [(f"k{i}", k) for i, k in enumerate(alle) if k["mode"] != "ab"]
    else:
        auswahl = [(str(i), k) for i, k in enumerate(alle)]
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    mitte = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    ergebnis = []
    for nr, kante in auswahl:
        a, b = tuple(kante["points"][0]), tuple(kante["points"][-1])
        if distance(a, b) < 20.0:
            continue
        name = randspuren.benennen(kante, ring)
        gerade = kante["mode"] == "ab"
        bogen = not gerade and not fast_gerade(kante["points"])
        for start, (von, nach) in (("a", (a, b)), ("b", (b, a))):
            text = (f"Ecke {randspuren._himmelsrichtung(mitte, von)} → entlang "
                    f"{name[len(randspuren.NAMENS_PRAEFIX):]} nach "
                    f"{_richtung_name(heading_deg(von, nach))}")
            if bogen and geraden:
                text += f" (Bogen, Stich {_stich(kante['points']):.0f} m)"
            elif not gerade and geraden:
                text += " (fast gerade)"
            ergebnis.append({"id": f"{nr}{start}", "kante": name, "text": text,
                             "von": list(von), "nach": list(nach), "laenge_m": _r(distance(a, b)),
                             "bogen": bool(bogen and geraden)})
    ergebnis.sort(key=lambda o: (o["bogen"], -o["laenge_m"]))   # stabil: je Kante bleibt a vor b
    if ergebnis:
        laengste = ergebnis[0]["laenge_m"]
        for o in ergebnis:
            if not o["bogen"] and o["laenge_m"] == laengste:
                o["text"] += " · längste Kante"
                o["laengste"] = True
    return ergebnis


def _stich(punkte: Sequence[Sequence[float]]) -> float:
    """Wie weit ein Bogen höchstens von der Geraden zwischen seinen Enden abweicht."""
    a, b = tuple(punkte[0]), tuple(punkte[-1])
    laenge = distance(a, b)
    if laenge < 1e-9:
        return 0.0
    return max(abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) / laenge for p in punkte)


FAST_GERADE_M = 3.0        # so weit darf ein Bogen von seiner Sehne abweichen ...
FAST_GERADE_ANTEIL = 0.02  # ... oder so viel seiner Länge, was größer ist (Feldränder mit Zaunknick)


def fast_gerade(punkte: Sequence[Sequence[float]]) -> bool:
    """Ein Bogen, der kaum von der Geraden zwischen seinen Enden abweicht."""
    a, b = tuple(punkte[0]), tuple(punkte[-1])
    laenge = distance(a, b)
    if laenge < 20.0:
        return False
    return _stich(punkte) <= max(FAST_GERADE_M, FAST_GERADE_ANTEIL * laenge)


def _richtung_name(kurs: float) -> str:
    namen = ["Norden", "Nordosten", "Osten", "Südosten", "Süden", "Südwesten", "Westen", "Nordwesten"]
    return namen[int(((kurs + 22.5) % 360.0) // 45.0)]


def _schnitt_mit_polygon(p: Point, e: Point, polygon: Sequence[Point]) -> list[tuple[Point, Point]]:
    """Die Stücke der Geraden p + s·e, die im Polygon liegen."""
    werte: list[float] = []
    n = len(polygon)
    for i in range(n):
        q1, q2 = polygon[i], polygon[(i + 1) % n]
        dx, dy = q2[0] - q1[0], q2[1] - q1[1]
        nenner = e[0] * dy - e[1] * dx
        if abs(nenner) < 1e-12:
            continue
        wx, wy = q1[0] - p[0], q1[1] - p[1]
        s = (wx * dy - wy * dx) / nenner
        t = (wx * e[1] - wy * e[0]) / nenner
        if -1e-9 <= t < 1.0 - 1e-9:
            werte.append(s)
    werte.sort()
    stuecke = []
    for s1, s2 in zip(werte, werte[1:]):
        if s2 - s1 < 0.5:
            continue
        m = (s1 + s2) / 2.0
        if point_in_polygon((p[0] + e[0] * m, p[1] + e[1] * m), polygon):
            stuecke.append(((p[0] + e[0] * s1, p[1] + e[1] * s1), (p[0] + e[0] * s2, p[1] + e[1] * s2)))
    return stuecke


MIN_STUECK_M = 1.0       # kürzere Säspur-Stücke (Zipfel) lohnen nicht


def _nah_an_kante(p: Point, e: Point, a: Point, b: Point, abstand: float) -> Optional[tuple[float, float]]:
    """Das s-Intervall der Geraden p + s·e, das näher als ``abstand`` an der Strecke a-b liegt.

    Der Bereich um eine Strecke ist ein Rechteck mit zwei Halbkreisen; die
    Gerade schneidet ihn in höchstens einem Intervall (er ist konvex).
    """
    teile: list[tuple[float, float]] = []
    for c in (a, b):                                     # die beiden Kreise
        wx, wy = p[0] - c[0], p[1] - c[1]
        bq = wx * e[0] + wy * e[1]
        disk = bq * bq - (wx * wx + wy * wy - abstand * abstand)
        if disk > 0.0:
            w = math.sqrt(disk)
            teile.append((-bq - w, -bq + w))
    laenge = distance(a, b)
    if laenge > 1e-9:                                    # das Rechteck
        u = ((b[0] - a[0]) / laenge, (b[1] - a[1]) / laenge)
        n = (-u[1], u[0])
        lo, hi = -math.inf, math.inf
        for richtung, grenzen in ((u, (0.0, laenge)), (n, (-abstand, abstand))):
            start = (p[0] - a[0]) * richtung[0] + (p[1] - a[1]) * richtung[1]
            steigung = e[0] * richtung[0] + e[1] * richtung[1]
            if abs(steigung) < 1e-12:
                if not grenzen[0] < start < grenzen[1]:
                    lo, hi = 1.0, -1.0
                    break
                continue
            s1, s2 = sorted(((grenzen[0] - start) / steigung, (grenzen[1] - start) / steigung))
            lo, hi = max(lo, s1), min(hi, s2)
        if lo < hi:
            teile.append((lo, hi))
    if not teile:
        return None
    return min(t[0] for t in teile), max(t[1] for t in teile)


def _hauptfeld_stuecke(p: Point, e: Point, ring: Sequence[Point], abstand: float) -> list[tuple[Point, Point]]:
    """Die Stücke der Geraden p + s·e im Schlag, die mindestens ``abstand`` von jeder Grenzkante liegen.

    Genau statt über den versetzten Ring (``headland.ring``): der ist eine
    Zeichenhilfe und verliert bei gestuften Schlägen ganze Ecken - am
    23.10.2026 auf Fünfzehn Jauchert ein Dreieck, das dann keine Säspur bekam.
    """
    stuecke = []
    for a, b in _schnitt_mit_polygon(p, e, ring):
        s_a = (a[0] - p[0]) * e[0] + (a[1] - p[1]) * e[1]
        s_b = (b[0] - p[0]) * e[0] + (b[1] - p[1]) * e[1]
        frei = [(min(s_a, s_b), max(s_a, s_b))]
        n = len(ring)
        for i in range(n):
            sperre = _nah_an_kante(p, e, ring[i], ring[(i + 1) % n], abstand)
            if sperre is None:
                continue
            neu = []
            for lo, hi in frei:
                if sperre[1] <= lo or sperre[0] >= hi:
                    neu.append((lo, hi))
                    continue
                if sperre[0] > lo:
                    neu.append((lo, sperre[0]))
                if sperre[1] < hi:
                    neu.append((sperre[1], hi))
            frei = neu
        for lo, hi in frei:
            if hi - lo >= MIN_STUECK_M:
                stuecke.append(((p[0] + e[0] * lo, p[1] + e[1] * lo), (p[0] + e[0] * hi, p[1] + e[1] * hi)))
    return stuecke


def _r(wert: float) -> float:
    return round(wert, 2)


def planen(grenze: Sequence[Point], von: Point, nach: Point, breite_m: float,
           fahrgasse_m: float, randspuren_zahl: int = 3) -> dict:
    """Den ganzen Schlag planen. ``von``→``nach`` ist die Startkante, ``von`` die Startecke."""
    ring = [tuple(map(float, p)) for p in grenze]
    if len(ring) < 3:
        raise ValueError("Der Schlag hat keine Grenze")
    if not 1.0 <= breite_m <= 60.0:
        raise ValueError("Sämaschinenbreite zwischen 1 und 60 m")
    if not breite_m <= fahrgasse_m <= 120.0:
        raise ValueError("Fahrgassenweite mindestens eine Sämaschinenbreite")
    w, F, r = float(breite_m), float(fahrgasse_m), int(randspuren_zahl)
    laenge = distance(von, nach)
    if laenge < 1.0:
        raise ValueError("Die Startkante ist zu kurz")
    e = ((nach[0] - von[0]) / laenge, (nach[1] - von[1]) / laenge)
    # Innen liegt links der Kante, wenn die Grenze gegen den Uhrzeigersinn läuft.
    # Die Startkante kann in beiden Richtungen gewählt sein - deshalb prüfen.
    nrm = (-e[1], e[0])
    xs = [(p[0] - von[0]) * nrm[0] + (p[1] - von[1]) * nrm[1] for p in ring]
    if sum(1 for d in xs if d > 1.0) < sum(1 for d in xs if d < -1.0):
        nrm = (e[1], -e[0])
    def versatz(p: Point) -> float:
        return (p[0] - von[0]) * nrm[0] + (p[1] - von[1]) * nrm[1]
    tiefe = max(versatz(p) for p in ring)

    konflikte: list[dict] = []
    hinweise: list[str] = []

    # -- Randvorgabe: r Säspuren, dann die Fahrgasse auf halber Weite ------
    soll = (2 * r + 1) * w
    passt = abs(soll - F) <= 0.01
    if not passt:
        verhaeltnis = F / w
        konflikte.append({
            "art": "randvorgabe",
            "text": (f"{r} Säspuren und dann die Fahrgasse gehen mit {w:g} m Sämaschine nur bei "
                     f"{soll:g} m Fahrgassenweite auf, nicht bei {F:g} m "
                     f"({F:g} / {w:g} = {verhaeltnis:.2f} Säbreiten)."),
            "anpassung": [
                f"Fahrgassenweite {soll:g} m einstellen",
                f"Sämaschine mit {F / (2 * r + 1):.2f} m Arbeitsbreite",
            ] + ([f"{(round(verhaeltnis) - 1) // 2} Säspuren am Rand statt {r}"]
                 if abs(verhaeltnis - round(verhaeltnis)) < 0.01 and round(verhaeltnis) % 2 == 1 else []),
        })
    runden = max(1, math.ceil(F / w - 1e-6))       # Vorgewende so tief wie eine Fahrgassenweite
    vorgewende_m = runden * w

    def ist_fahrgasse(mitte_m: float) -> bool:
        # Die Spur, in deren Streifen eine Fahrgasse (F/2 + j·F) fällt.
        j = math.floor((mitte_m - F / 2.0) / F + 0.5)
        abweichung = mitte_m - (F / 2.0 + j * F)
        return -w / 2.0 + 1e-6 < abweichung <= w / 2.0 + 1e-6

    # -- Vorgewende-Runden -------------------------------------------------
    # Zerfällt der Schlag an einem engen Hals, hat eine Runde mehrere Ringe.
    ringe = []
    for i in range(runden):
        einzug = (i + 0.5) * w
        teile = [p for p in headland.ringe(ring, einzug) if len(p) >= 3]
        if not teile:
            break
        for punkte in teile:
            # Auf Zentimeter gerundet können Millimeter-Stummel über ihr Nachbarstück
            # kippen: danach die Kreuzungen noch einmal herausschneiden.
            gerundet = headland._ohne_kreuzungen([(_r(x), _r(y)) for x, y in punkte])
            ringe.append({"runde": i + 1, "einzug_m": _r(einzug), "fahrgasse": ist_fahrgasse(einzug),
                          "punkte": [[x, y] for x, y in gerundet]})
    anzahl_runden = len({x["runde"] for x in ringe})

    # -- Hauptfeld ---------------------------------------------------------
    spuren = []
    if tiefe >= 2 * vorgewende_m + w:
        k = math.floor(min(versatz(p) for p in ring) / w) - 1
        while (k + 0.5) * w <= tiefe + w:
            mitte_m = (k + 0.5) * w
            basis = (von[0] + nrm[0] * mitte_m, von[1] + nrm[1] * mitte_m)
            stuecke = _hauptfeld_stuecke(basis, e, ring, vorgewende_m)
            if stuecke:
                spuren.append({"nummer": len(spuren) + 1, "versatz_m": _r(mitte_m),
                               "fahrgasse": ist_fahrgasse(mitte_m),
                               "stuecke": [[[_r(a[0]), _r(a[1])], [_r(b[0]), _r(b[1])]] for a, b in stuecke],
                               "laenge_m": _r(sum(distance(a, b) for a, b in stuecke))})
            k += 1
    if not spuren:
        konflikte.append({
            "art": "zu_schmal",
            "text": (f"Der Schlag ist schmaler als zweimal das Vorgewende ({2 * vorgewende_m:g} m): "
                     f"nach dem Vorgewende bleibt kein Hauptfeld."),
            "anpassung": ["Nur Vorgewende-Runden fahren (Kontur)",
                          "Vorgewende an den Schmalseiten weglassen und längs durchsäen"],
        })

    # -- Gegenrand ----------------------------------------------------------
    # So wird gesät: von der Startkante (A) so viele Fahrgassen wie passen,
    # alle F. Von der Gegenseite (B) her nach r Säspuren die Fahrgasse (auf
    # F/2). Zwischen der letzten Fahrgasse von A und der von B liegt, was übrig
    # bleibt - bis hinunter zu einem Meter. Dazwischen wird aufgefüllt; Spritze
    # und Streuer schalten an der Fahrgasse von B so viel Teilbreite ab, wie
    # die beiden Fahrgassen näher als F beieinander liegen.
    kurs = heading_deg(von, nach)
    parallele = []
    for kante in randspuren.kanten(ring):
        a, b = tuple(kante["points"][0]), tuple(kante["points"][-1])
        if abs((heading_deg(a, b) - kurs + 90.0) % 180.0 - 90.0) > PARALLEL_DEG:
            continue
        d_werte = [versatz(tuple(p)) for p in kante["points"]]
        if max(d_werte) < 1.0:
            continue                               # die Startkante selbst (oder auf ihrer Linie)
        parallele.append((kante, randspuren.benennen(kante, ring), min(d_werte), max(d_werte)))
    # Die Gegenseite ist eine Kante, hinter der das Feld aufhört (p[3] reicht bis an die
    # Tiefe des Schlags). Eine Stufe mitten im Feld ist es nicht: 23.10.2026 nahm der Plan auf
    # Fünfzehn Jauchert eine Stufe der Westgrenze als B - alles dahinter blieb ohne Säspur.
    gegen = [p for p in parallele if p[2] > tiefe / 2.0 and p[3] - p[2] <= w and p[3] >= tiefe - w]
    gegenrand = min(gegen, key=lambda p: p[2]) if gegen and spuren else None
    fuellstreifen = None
    gassen_m: list[float] = [F / 2.0]              # wo Fahrgassen liegen, ab Startkante
    gasse_b = None
    if gegenrand is not None:
        kante, name, d_min, d_max = gegenrand
        D = (d_min + d_max) / 2.0
        gasse_b = D - F / 2.0                      # Fahrgasse von B: r Säspuren vom Rand
        # Nach der letzten Fahrgasse von A kommt noch eine Säbreite. Die darf
        # nicht in die Fahrgasse von B fallen: also mindestens zwei Säbreiten
        # zwischen den beiden Fahrgassen.
        gassen_a = [F / 2.0 + j * F for j in range(1, int(tiefe / F) + 2)
                    if F / 2.0 + j * F <= gasse_b - 2 * w + TOLERANZ_M]
        letzte = max(gassen_a) if gassen_a else F / 2.0
        a_ende = letzte + w / 2.0 + w              # Kante der Säbreite nach der letzten Fahrgasse
        b_kante = gasse_b - w / 2.0                # Kante der Fahrgassen-Spur von B
        # A: alles bis einschließlich der Säbreite nach der letzten Fahrgasse.
        spuren[:] = [s for s in spuren if s["versatz_m"] <= letzte + w + 1e-6]
        for s in spuren:
            s["fahrgasse"] = any(-w / 2.0 < s["versatz_m"] - g <= w / 2.0 + 1e-6 for g in gassen_a)
        vorhanden = {round(s["versatz_m"], 2) for s in spuren}
        for mitte_m in ([letzte, letzte + w] if gassen_a else []):
            if round(mitte_m, 2) in vorhanden:
                continue                           # liegt schon im Hauptfeld
            basis = (von[0] + nrm[0] * mitte_m, von[1] + nrm[1] * mitte_m)
            stuecke = _gekuerzt(_schnitt_mit_polygon(basis, e, ring), vorgewende_m)
            if stuecke:
                spuren.append({"nummer": 0, "versatz_m": _r(mitte_m), "fahrgasse": mitte_m == letzte,
                               "im_band": True,
                               "stuecke": [[[_r(a[0]), _r(a[1])], [_r(b[0]), _r(b[1])]] for a, b in stuecke],
                               "laenge_m": _r(sum(distance(a, b) for a, b in stuecke))})
        spuren.sort(key=lambda s: s["versatz_m"])
        for nr, s in enumerate(spuren, start=1):
            s["nummer"] = nr
        anzahl_a = len(spuren)
        # B: r Säspuren, Fahrgasse (Vorgewende-Band), dann weiter Richtung A bis
        # zur letzten Säspur von A - im Raster von B.
        fuell_zahl = 0
        k = r + 1
        while D - (k + 0.5) * w + w / 2.0 > a_ende + TOLERANZ_M:
            mitte_m = D - (k + 0.5) * w
            fuell_zahl += 1
            if mitte_m < D - vorgewende_m + 1e-6:  # im Band liegen die Vorgewende-Runden
                basis = (von[0] + nrm[0] * mitte_m, von[1] + nrm[1] * mitte_m)
                stuecke = _hauptfeld_stuecke(basis, e, ring, vorgewende_m)
                if stuecke:
                    spuren.append({"nummer": len(spuren) + 1, "versatz_m": _r(mitte_m), "fahrgasse": False,
                                   "fuell": True, "von_b": True,
                                   "stuecke": [[[_r(a[0]), _r(a[1])], [_r(b[0]), _r(b[1])]] for a, b in stuecke],
                                   "laenge_m": _r(sum(distance(a, b) for a, b in stuecke))})
            k += 1
        breite_fuell = b_kante - a_ende
        ueberlappung = fuell_zahl * w - breite_fuell
        gassen_m += gassen_a + [gasse_b]
        abstand = gasse_b - letzte
        letzte_spur = next((s for s in spuren if s["fahrgasse"] and abs(s["versatz_m"] - letzte) <= w / 2.0), None)
        text = (f"Am {name}: letzte Fahrgasse von A"
                + (f" (Spur {letzte_spur['nummer']})" if letzte_spur else "")
                + f" und Fahrgasse von B liegen {abstand:.1f} m auseinander"
                + ("." if abs(abstand - F) <= TOLERANZ_M else f" statt {F:g} m."))
        anpassung = []
        if abstand < F - TOLERANZ_M:
            anpassung.append(f"Spritze und Streuer an der Fahrgasse von B {F - abstand:.1f} m Teilbreite "
                             f"zum Feld hin abschalten - sonst wird das doppelt behandelt")
        elif abstand > F + TOLERANZ_M:
            anpassung.append(f"Zwischen den beiden Fahrgassen bleiben {abstand - F:.1f} m, die keine ganz "
                             f"erreicht: an beiden je {(abstand - F) / 2:.1f} m weiter streuen und spritzen")
        anpassung.append(f"Von B her nach der Fahrgasse noch {fuell_zahl} Säspuren bis zur letzten Säspur "
                         f"von A ({max(0.0, breite_fuell):.1f} m)"
                         + (f"; die letzte überlappt {ueberlappung:.1f} m mit der Säbreite nach der "
                            f"Fahrgasse von A - dort Teilbreite der Sämaschine, die Fahrgassen bleiben frei"
                            if ueberlappung > TOLERANZ_M else ""))
        fuellstreifen = {
            "kante": name, "abstand_m": _r(abstand), "breite_m": _r(max(0.0, breite_fuell)),
            "von_m": _r(a_ende), "bis_m": _r(b_kante), "ueberlappung_m": _r(max(0.0, ueberlappung)),
            "anzahl_a": anzahl_a, "anzahl_von_b": fuell_zahl,
            "spuren": [s["nummer"] for s in spuren if s.get("von_b")],
            "text": text, "anpassung": anpassung,
            "punkte": [[_r(p[0]), _r(p[1])] for p in kante["points"]],
        }
    else:
        gassen_m += [s["versatz_m"] for s in spuren if s["fahrgasse"]]

    fahrgassen = [s for s in spuren if s["fahrgasse"]]
    if spuren and not fahrgassen:
        konflikte.append({"art": "keine_fahrgasse",
                          "text": "Im Hauptfeld liegt keine Fahrgasse - der Schlag ist dafür zu schmal.",
                          "anpassung": ["Pflege nur über die Vorgewende-Fahrgasse"]})

    # -- Prüfung der übrigen Kanten -----------------------------------------
    for kante, name, d_min, d_max in parallele:
        if not fahrgassen:
            break
        if gegenrand is not None and abs((d_min + d_max) / 2.0 - (gegenrand[2] + gegenrand[3]) / 2.0) < w \
                and d_max - d_min <= w:
            continue                               # der Gegenrand: Füllstreifen statt Konflikt
        ferne = d_min > tiefe / 2.0

        def abweichung(d: float) -> tuple[float, Optional[dict]]:
            # Vorgewende-Fahrgasse an diesem Rand und die nächste Fahrgasse des Hauptfeldes.
            if ferne:
                h = d - F / 2.0
                davor = [s for s in fahrgassen if s["versatz_m"] <= h - 1e-6 and not s.get("rest")]
                if not davor:
                    return 0.0, None
                nachbar = max(davor, key=lambda s: s["versatz_m"])
                return (h - nachbar["versatz_m"]) - F, nachbar
            h = d + F / 2.0
            dahinter = [s for s in fahrgassen if s["versatz_m"] >= h + 1e-6 and not s.get("rest")]
            if not dahinter:
                return 0.0, None
            nachbar = min(dahinter, key=lambda s: s["versatz_m"])
            return (nachbar["versatz_m"] - h) - F, nachbar

        dev1, nachbar = abweichung(d_min if ferne else d_max)
        dev2, _ = abweichung(d_max if ferne else d_min)
        if nachbar is None:
            continue
        keil = abs(d_max - d_min) > w
        schlimmste = dev1 if abs(dev1) >= abs(dev2) else dev2
        if abs(dev1) <= TOLERANZ_M and abs(dev2) <= TOLERANZ_M:
            continue
        abstand = F + schlimmste
        if keil:
            konflikte.append({
                "art": "keil", "kante": name,
                "text": (f"{name}: Keil - der Rand läuft schräg zur Fahrtrichtung (Randabstand "
                         f"{d_min:.0f} bis {d_max:.0f} m). Der Abstand zwischen den Fahrgassen und der "
                         f"Vorgewende-Fahrgasse ändert sich entlang des Randes; {F:g} m passen dort nicht durchgehend."),
                "anpassung": [
                    "Im Keil Spritze und Streuer mit Teilbreitenschaltung fahren (Teilbreiten nach und nach zu)",
                    f"Den Plan an {name} beginnen - dann liegt der Keil an der gegenüberliegenden Seite",
                ],
                "punkte": [[_r(p[0]), _r(p[1])] for p in kante["points"]]})
            continue
        if schlimmste > 0:
            text = (f"{name}: zwischen der letzten Fahrgasse (Spur {nachbar['nummer']}) und der "
                    f"Vorgewende-Fahrgasse liegen {abstand:.1f} m statt {F:g} m - ein Streifen von "
                    f"{schlimmste:.1f} m bekommt keinen Dünger und keinen Pflanzenschutz.")
        else:
            text = (f"{name}: zwischen der letzten Fahrgasse (Spur {nachbar['nummer']}) und der "
                    f"Vorgewende-Fahrgasse liegen nur {abstand:.1f} m statt {F:g} m - "
                    f"{-schlimmste:.1f} m werden doppelt gedüngt und gespritzt.")
        anpassung = []
        if schlimmste < 0:
            anpassung.append(f"An der Vorgewende-Fahrgasse am {name} {-schlimmste:.1f} m Teilbreite "
                             f"von Spritze und Streuer zum Feld hin abschalten")
        elif schlimmste < KLEINE_LUECKE_M:
            anpassung.append(f"Kleine Lücke: an der Fahrgasse Spur {nachbar['nummer']} und an der "
                             f"Vorgewende-Fahrgasse je {schlimmste / 2:.1f} m weiter streuen und spritzen "
                             f"(Randdüsen, Streubreite) - eine zusätzliche Fahrgasse lohnt erst ab "
                             f"{KLEINE_LUECKE_M:g} m")
        else:
            # Eine Fahrgasse mehr auf der Säspur, die am besten in die Lücke passt;
            # sie überlappt dann mit ihren Nachbarn - dort Teilbreite schalten.
            h = (d_min - F / 2.0) if ferne else (d_max + F / 2.0)
            ziel = nachbar["versatz_m"] + (F if ferne else -F)
            zusatz = min(spuren, key=lambda s: abs(s["versatz_m"] - ziel))
            z = zusatz["versatz_m"]
            zum_rand = (z + F - h) if ferne else (h + F - z)
            zur_gasse = F - abs(z - nachbar["versatz_m"])
            teile = [f"{zum_rand:.1f} m zum Vorgewende hin"] if zum_rand > TOLERANZ_M else []
            teile += [f"{zur_gasse:.1f} m zur Fahrgasse Spur {nachbar['nummer']} hin"] if zur_gasse > TOLERANZ_M else []
            anpassung.append(f"Zusätzliche Fahrgasse auf Spur {zusatz['nummer']} ({z:.1f} m von der "
                             f"Startkante); Spritze und Streuer dort "
                             f"{' und '.join(teile) or 'ohne Teilbreite'} abschalten")
        breite = (d_min + d_max) / 2.0
        unten = math.floor(breite / F) * F
        anpassung.append(f"Zur Einordnung: Randabstand {breite:.1f} m - ohne Rest ginge es bei "
                         f"{unten:g} m oder {unten + F:g} m (ganze Fahrgassenweiten)")
        konflikte.append({"art": "abstand", "kante": name, "text": text,
                          "abweichung_m": _r(schlimmste), "anpassung": anpassung,
                          "punkte": [[_r(p[0]), _r(p[1])] for p in kante["points"]]})

    # Die Säbreite, die nicht mehr ganz passt: nur ein Hinweis, kein Konflikt.
    ende = ((gegenrand[2] + gegenrand[3]) / 2.0 - vorgewende_m) if gegenrand else (tiefe - vorgewende_m)
    rest = ende - (spuren[-1]["versatz_m"] + w / 2.0) if spuren else 0.0
    if spuren and rest > 0.2:
        hinweise.append(f"Zwischen der letzten Säspur (Spur {spuren[-1]['nummer']}) und dem Band am Rand "
                        f"bleiben {rest:.1f} m - die letzte Spur mit Teilbreite fahren, oder diesen "
                        f"Streifen beim Vorgewende mitnehmen.")

    # Reihenfolge, so wie gesät wird.
    reihenfolge = []
    haupt_spuren = [s for s in spuren if not s.get("fuell")]
    if gegenrand is not None and fuellstreifen:
        gassen_haupt = [s for s in spuren if s["fahrgasse"]]
        reihenfolge.append(
            f"Von der Startecke (A): so viele Fahrgassen wie passen, alle {F:g} m, nach der letzten noch "
            f"eine Säbreite - Spur 1–{fuellstreifen['anzahl_a']}, Fahrgassen in Spur "
            f"{', '.join(str(s['nummer']) for s in gassen_haupt) or 'keine'}")
        reihenfolge.append(f"An der Gegenseite (B, {gegenrand[1]}) anfangen: {r} Säspuren ab der Grenze, "
                           f"dann die Fahrgasse auf {F / 2:.1f} m (Spur \u201eSaatplan \u2026 B\u201c laden)")
        if fuellstreifen["anzahl_von_b"]:
            reihenfolge.append(f"Von B weiter Richtung A bis zur letzten Säspur von A: "
                               f"{fuellstreifen['anzahl_von_b']} Säspuren"
                               + (f", die letzte mit {fuellstreifen['ueberlappung_m']:.1f} m Überlappung"
                                  if fuellstreifen["ueberlappung_m"] > TOLERANZ_M else ""))
    elif haupt_spuren:
        gassen_haupt = [s for s in spuren if s["fahrgasse"]]
        reihenfolge.append(
            f"Von der Startecke (A) bis zum Gegenrand: Spur 1–{haupt_spuren[-1]['nummer']}, Fahrgassen in Spur "
            f"{', '.join(str(s['nummer']) for s in gassen_haupt) or 'keine'} (alle {F:g} m)")
    reihenfolge.append(f"Vorgewende rundum: {anzahl_runden} Runden, Fahrgasse in Runde {r + 1}")

    # Die Saisonspur: eine AB-Linie durch die erste Fahrgasse, parallel zur
    # Startkante. Mit Gegenrand bekommt sie die Liste der Fahrgassen mit (in
    # Metern neben der Spur, rechts der Richtung positiv): im Füllstreifen
    # keine, dafür die Restfahrgasse. Ohne Gegenrand gilt alle F.
    saison_versatz = F / 2.0
    saisonspur = [[_r(von[0] + nrm[0] * saison_versatz), _r(von[1] + nrm[1] * saison_versatz)],
                  [_r(nach[0] + nrm[0] * saison_versatz), _r(nach[1] + nrm[1] * saison_versatz)]]
    rechts = 1.0 if (nrm[0] * e[1] - nrm[1] * e[0]) > 0 else -1.0
    saison_fahrgassen = sorted(_r(rechts * (t - saison_versatz)) + 0.0 for t in set(gassen_m)) \
        if gegenrand is not None else []
    saisonspur_b, saisonspur_b_fahrgassen = None, []
    if gasse_b is not None:
        saisonspur_b = [[_r(von[0] + nrm[0] * gasse_b), _r(von[1] + nrm[1] * gasse_b)],
                        [_r(nach[0] + nrm[0] * gasse_b), _r(nach[1] + nrm[1] * gasse_b)]]
        saisonspur_b_fahrgassen = sorted(_r(rechts * (g - gasse_b)) + 0.0 for g in set(gassen_m))
    if fuellstreifen:
        fuellstreifen["text"] = _deutsch(fuellstreifen["text"])
        fuellstreifen["anpassung"] = [_deutsch(a) for a in fuellstreifen["anpassung"]]
    reihenfolge = [_deutsch(x) for x in reihenfolge]

    # Texte für den Fahrer mit deutschem Komma (10,5 m statt 10.5 m).
    for k in konflikte:
        k["text"] = _deutsch(k["text"])
        k["anpassung"] = [_deutsch(a) for a in k["anpassung"]]
    hinweise = [_deutsch(h) for h in hinweise]

    return {
        "breite_m": w, "fahrgasse_m": F, "randspuren": r, "randvorgabe_passt": passt,
        "vorgewende_runden": anzahl_runden, "vorgewende_m": _r(vorgewende_m),
        "spuren": spuren, "ringe": ringe,
        "anzahl_spuren": len(spuren), "anzahl_fahrgassen": len(fahrgassen),
        "fahrgassen_abstaende": [_r(b["versatz_m"] - a["versatz_m"]) for a, b in zip(fahrgassen, fahrgassen[1:])],
        "strecke_km": _r((sum(s["laenge_m"] for s in spuren)
                          + sum(_umfang(x["punkte"]) for x in ringe)) / 1000.0),
        "konflikte": konflikte, "hinweise": hinweise,
        "fuellstreifen": fuellstreifen, "reihenfolge": reihenfolge,
        "saisonspur": saisonspur, "saisonspur_fahrgassen": saison_fahrgassen,
        "saisonspur_b": saisonspur_b, "saisonspur_b_fahrgassen": saisonspur_b_fahrgassen, "start": [_r(von[0]), _r(von[1])], "kurs": _r(heading_deg(von, nach)),
    }


def alle_spuren(plan: dict) -> list[dict]:
    """Jede Spur des Plans einzeln zum Lenken: Hauptfeld (A), Spuren von der
    Gegenseite (B) und die Vorgewende-Runden.

    Ein Hauptfeldstreifen, den eine Stufe der Grenze teilt, wird zu mehreren
    Spuren - jede ein gerades Stück. Die Runden sind geschlossene Ringe.
    """
    spuren = []
    for s in plan.get("spuren", []):
        art = "B" if s.get("von_b") else "A"
        for a, b in s.get("stuecke", []):
            spuren.append({"art": art, "nummer": s["nummer"], "punkte": [list(a), list(b)],
                           "geschlossen": False, "fahrgasse": bool(s.get("fahrgasse"))})
    for ring in plan.get("ringe", []):
        if len(ring.get("punkte", [])) >= 3:
            spuren.append({"art": "V", "nummer": ring["runde"], "punkte": [list(p) for p in ring["punkte"]],
                           "geschlossen": True, "fahrgasse": bool(ring.get("fahrgasse"))})
    return spuren


def _gekuerzt(stuecke, um_m: float):
    """Jedes Stück an beiden Enden um ``um_m`` kürzen (das Vorgewende an den Enden)."""
    ergebnis = []
    for a, b in stuecke:
        laenge = distance(a, b)
        if laenge <= 2 * um_m + 1.0:
            continue
        ex, ey = (b[0] - a[0]) / laenge, (b[1] - a[1]) / laenge
        ergebnis.append(((a[0] + ex * um_m, a[1] + ey * um_m), (b[0] - ex * um_m, b[1] - ey * um_m)))
    return ergebnis


def _deutsch(text: str) -> str:
    return re.sub(r"(\d)\.(\d)", r"\1,\2", text)


def _umfang(punkte: Sequence[Sequence[float]]) -> float:
    return sum(distance(tuple(punkte[i]), tuple(punkte[(i + 1) % len(punkte)])) for i in range(len(punkte)))
