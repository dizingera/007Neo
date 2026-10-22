"""Hindernisse im Feld: Mast, Stein, Schacht - einmal markiert, auf allen Geräten.

Ein Hindernis ist ein Punkt (WGS84) mit einem Radius. Gespeichert wird es in
der Tabelle ``hindernisse`` und wie Felder und Spuren über den Master
abgeglichen; hier steht nur die Geometrie in lokalen Metern:

* ``warnung``: liegt eines voraus in der Breite, die das Gespann gleich
  überfährt? Voraus heißt: so weit, wie die Maschine in ``VORAUS_S`` Sekunden
  kommt, mindestens ``MIN_VORAUS_M``. Gemeldet wird das nächste.
* ``auf_route``: liegt eines auf einer geplanten Wende? Dann wird sie nicht
  freigegeben.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

VORAUS_S = 8.0
MIN_VORAUS_M = 15.0
SEITEN_RESERVE_M = 1.0      # zur halben Arbeitsbreite: so knapp vorbei ist schon zu knapp
ARTEN = ("Mast", "Stein", "Schacht", "Baum", "Sonstiges")


def warnung(position, kurs_deg: Optional[float], tempo_ms: float, breite_m: float,
            hindernisse: Iterable[dict]) -> Optional[dict]:
    """Das nächste Hindernis voraus im Fahrstreifen, oder None.

    ``hindernisse``: je ``{"id", "name", "x", "y", "radius_m"}`` in lokalen Metern.
    ``abstand_m`` ist der Weg bis zum Rand des Hindernisses (0 = man steht drin).
    """
    if position is None or kurs_deg is None:
        return None
    h = math.radians(kurs_deg)
    vor = (math.sin(h), math.cos(h))
    rechts = (math.cos(h), -math.sin(h))
    reichweite = max(MIN_VORAUS_M, tempo_ms * VORAUS_S)
    halb = max(0.5, breite_m / 2.0) + SEITEN_RESERVE_M
    beste = None
    for hd in hindernisse:
        dx, dy = hd["x"] - position[0], hd["y"] - position[1]
        voraus = dx * vor[0] + dy * vor[1]
        seitlich = dx * rechts[0] + dy * rechts[1]
        radius = max(0.0, float(hd.get("radius_m") or 0.0))
        if abs(seitlich) > halb + radius:
            continue
        if voraus < -radius or voraus - radius > reichweite:
            continue
        abstand = max(0.0, voraus - radius)
        if beste is None or abstand < beste["abstand_m"]:
            beste = {"id": hd.get("id"), "name": hd.get("name") or "Hindernis",
                     "abstand_m": round(abstand, 1),
                     "seite": "mitte" if abs(seitlich) < 0.5 else ("rechts" if seitlich > 0 else "links")}
    return beste


def auf_route(pfad, breite_m: float, hindernisse: Iterable[dict]) -> Optional[dict]:
    """Das erste Hindernis, das das Gespann auf diesem Pfad berühren würde."""
    halb = max(0.5, breite_m / 2.0) + SEITEN_RESERVE_M / 2
    for hd in hindernisse:
        grenze = halb + max(0.0, float(hd.get("radius_m") or 0.0))
        p = (hd["x"], hd["y"])
        for a, b in zip(pfad, pfad[1:]):
            if _abstand_strecke(p, a, b) < grenze:
                return {"id": hd.get("id"), "name": hd.get("name") or "Hindernis"}
    return None


def _abstand_strecke(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    laenge2 = dx * dx + dy * dy
    t = 0.0 if laenge2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / laenge2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))
