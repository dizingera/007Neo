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

    Gerade Kanten ab 20 m (wie bei den Randspuren). Hat ein Schlag gar keine,
    gehen auch Bögen, ihre Sehne gibt dann die Richtung vor.
    """
    ring = [tuple(map(float, p)) for p in grenze]
    if len(ring) < 3:
        return []
    alle = randspuren.kanten(ring)
    geraden = [k for k in alle if k["mode"] == "ab"] or alle
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    mitte = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    ergebnis = []
    for nr, kante in enumerate(geraden):
        a, b = tuple(kante["points"][0]), tuple(kante["points"][-1])
        name = randspuren.benennen(kante, ring)
        for start, (von, nach) in (("a", (a, b)), ("b", (b, a))):
            ergebnis.append({
                "id": f"{nr}{start}",
                "kante": name,
                "text": (f"Ecke {randspuren._himmelsrichtung(mitte, von)} → entlang "
                         f"{name[len(randspuren.NAMENS_PRAEFIX):]} nach "
                         f"{_richtung_name(heading_deg(von, nach))}"),
                "von": list(von), "nach": list(nach),
            })
    return ergebnis


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
    ringe = []
    for i in range(runden):
        einzug = (i + 0.5) * w
        punkte = headland.ring(ring, einzug)
        if len(punkte) < 3:
            break
        ringe.append({"runde": i + 1, "einzug_m": _r(einzug), "fahrgasse": ist_fahrgasse(einzug),
                      "punkte": [[_r(x), _r(y)] for x, y in punkte]})

    # -- Hauptfeld ---------------------------------------------------------
    innen = headland.ring(ring, vorgewende_m) if tiefe >= 2 * vorgewende_m + w else []
    spuren = []
    if len(innen) < 3:
        konflikte.append({
            "art": "zu_schmal",
            "text": (f"Der Schlag ist schmaler als zweimal das Vorgewende ({2 * vorgewende_m:g} m): "
                     f"nach dem Vorgewende bleibt kein Hauptfeld."),
            "anpassung": ["Nur Vorgewende-Runden fahren (Kontur)",
                          "Vorgewende an den Schmalseiten weglassen und längs durchsäen"],
        })
    else:
        k = math.floor(min(versatz(p) for p in ring) / w) - 1
        while (k + 0.5) * w <= tiefe + w:
            mitte_m = (k + 0.5) * w
            basis = (von[0] + nrm[0] * mitte_m, von[1] + nrm[1] * mitte_m)
            stuecke = _schnitt_mit_polygon(basis, e, innen)
            if stuecke:
                spuren.append({"nummer": len(spuren) + 1, "versatz_m": _r(mitte_m),
                               "fahrgasse": ist_fahrgasse(mitte_m),
                               "stuecke": [[[_r(a[0]), _r(a[1])], [_r(b[0]), _r(b[1])]] for a, b in stuecke],
                               "laenge_m": _r(sum(distance(a, b) for a, b in stuecke))})
            k += 1

    fahrgassen = [s for s in spuren if s["fahrgasse"]]
    if spuren and not fahrgassen:
        konflikte.append({"art": "keine_fahrgasse",
                          "text": "Im Hauptfeld liegt keine Fahrgasse - der Schlag ist dafür zu schmal.",
                          "anpassung": ["Pflege nur über die Vorgewende-Fahrgasse"]})

    # -- Prüfung der übrigen Kanten -----------------------------------------
    kurs = heading_deg(von, nach)
    for kante in randspuren.kanten(ring):
        a, b = tuple(kante["points"][0]), tuple(kante["points"][-1])
        winkel = abs((heading_deg(a, b) - kurs + 90.0) % 180.0 - 90.0)
        if winkel > PARALLEL_DEG or not fahrgassen:
            continue
        d_werte = [versatz(tuple(p)) for p in kante["points"]]
        d_min, d_max = min(d_werte), max(d_werte)
        if d_max < 1.0:
            continue                               # die Startkante selbst (oder auf ihrer Linie)
        ferne = d_min > tiefe / 2.0
        name = randspuren.benennen(kante, ring)

        def abweichung(d: float) -> tuple[float, Optional[dict]]:
            # Vorgewende-Fahrgasse an diesem Rand und die nächste Fahrgasse des Hauptfeldes.
            if ferne:
                h = d - F / 2.0
                davor = [s for s in fahrgassen if s["versatz_m"] <= h - 1e-6]
                if not davor:
                    return 0.0, None
                nachbar = max(davor, key=lambda s: s["versatz_m"])
                return (h - nachbar["versatz_m"]) - F, nachbar
            h = d + F / 2.0
            dahinter = [s for s in fahrgassen if s["versatz_m"] >= h + 1e-6]
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

    # Kurze Kanten mit Fahrgassen-Raster quer: nur ein Hinweis, kein Konflikt.
    rest = tiefe - (spuren[-1]["versatz_m"] + w / 2.0 + vorgewende_m) if spuren else 0.0
    if spuren and rest > 0.2:
        hinweise.append(f"Zwischen der letzten Säspur (Spur {spuren[-1]['nummer']}) und dem Vorgewende "
                        f"bleiben {rest:.1f} m - eine Spur mehr mit Teilbreite, oder diesen Streifen "
                        f"beim Vorgewende mitnehmen.")

    # Die Saisonspur: eine AB-Linie durch eine Fahrgasse, parallel zur Startkante.
    # Spur 0 ist damit eine Fahrgasse, die Führung zählt die übrigen alle F.
    saison_versatz = F / 2.0
    saisonspur = [[_r(von[0] + nrm[0] * saison_versatz), _r(von[1] + nrm[1] * saison_versatz)],
                  [_r(nach[0] + nrm[0] * saison_versatz), _r(nach[1] + nrm[1] * saison_versatz)]]

    # Texte für den Fahrer mit deutschem Komma (10,5 m statt 10.5 m).
    for k in konflikte:
        k["text"] = _deutsch(k["text"])
        k["anpassung"] = [_deutsch(a) for a in k["anpassung"]]
    hinweise = [_deutsch(h) for h in hinweise]

    return {
        "breite_m": w, "fahrgasse_m": F, "randspuren": r, "randvorgabe_passt": passt,
        "vorgewende_runden": len(ringe), "vorgewende_m": _r(vorgewende_m),
        "spuren": spuren, "ringe": ringe,
        "anzahl_spuren": len(spuren), "anzahl_fahrgassen": len(fahrgassen),
        "fahrgassen_abstaende": [_r(b["versatz_m"] - a["versatz_m"]) for a, b in zip(fahrgassen, fahrgassen[1:])],
        "strecke_km": _r((sum(s["laenge_m"] for s in spuren)
                          + sum(_umfang(x["punkte"]) for x in ringe)) / 1000.0),
        "konflikte": konflikte, "hinweise": hinweise,
        "saisonspur": saisonspur, "start": [_r(von[0]), _r(von[1])], "kurs": _r(heading_deg(von, nach)),
    }


def _deutsch(text: str) -> str:
    return re.sub(r"(\d)\.(\d)", r"\1,\2", text)


def _umfang(punkte: Sequence[Sequence[float]]) -> float:
    return sum(distance(tuple(punkte[i]), tuple(punkte[(i + 1) % len(punkte)])) for i in range(len(punkte)))
