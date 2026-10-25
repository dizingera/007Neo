"""Richtung der Längsneigung des Neigungssensors aus der Fahrt prüfen.

Der Hangausgleich (imu.terrain_offset) erwartet wie ``Attitude``: *Nase oben*
positiv. Ob der Sensor das tut, hängt vom Sensor und der Einbaulage ab - und ein
falsches Vorzeichen verdoppelt den Fehler längs statt ihn aufzuheben (bei 3 m
Antennenhöhe und 5° Hang gut 50 cm: Teilbreiten und Vorgewende schalten zu früh
oder zu spät).

Prüfen lässt es sich ohne Zutun des Fahrers: Bergauf steigt die RTK-Höhe. Über
Abschnitte von ``ABSCHNITT_M`` wird die Steigung aus der Höhe gerechnet und mit der
gemessenen Längsneigung verglichen: bergauf muss sie positiv sein. Zeigen genug
deutliche Abschnitte das Gegenteil, wird das Vorzeichen umgedreht.
"""

from __future__ import annotations

import math
from typing import Optional

ABSCHNITT_M = 5.0          # so lange Strecke je Vergleich
MIN_STEIGUNG_DEG = 2.0     # flacher sagt die Höhe nichts Verlässliches
MIN_TEMPO_MS = 1.0
MIN_PROBEN = 12
EINIG_ANTEIL = 0.8         # so viele Proben müssen übereinstimmen


class Laengsneigung:
    def __init__(self) -> None:
        self.vorzeichen = 1.0          # 1 = Sensor meldet Nase oben positiv (wie Attitude)
        self.bestaetigt = False
        self.meldung = ""
        self._start: Optional[tuple[float, float, float]] = None   # (Ost, Nord, Höhe)
        self._pitch_summe = 0.0
        self._pitch_n = 0
        self._passt = 0
        self._gegen = 0

    def _neu(self, punkt, hoehe) -> None:
        self._start = (punkt[0], punkt[1], hoehe)
        self._pitch_summe, self._pitch_n = 0.0, 0

    def update(self, punkt, hoehe: Optional[float], pitch_deg: Optional[float],
               tempo_ms: float, rtk: bool, rueckwaerts: bool = False) -> Optional[float]:
        """Eine Messung. Gibt ein neues Vorzeichen zurück, wenn es sich gerade geändert hat."""
        if (hoehe is None or pitch_deg is None or not rtk or rueckwaerts
                or tempo_ms < MIN_TEMPO_MS or not all(map(math.isfinite, (hoehe, pitch_deg)))):
            self._start = None
            return None
        if self._start is None:
            self._neu(punkt, hoehe)
            return None
        self._pitch_summe += pitch_deg * self.vorzeichen
        self._pitch_n += 1
        weg = math.hypot(punkt[0] - self._start[0], punkt[1] - self._start[1])
        if weg < ABSCHNITT_M:
            return None
        steigung = math.degrees(math.atan2(hoehe - self._start[2], weg))
        pitch = self._pitch_summe / max(1, self._pitch_n)
        self._neu(punkt, hoehe)
        if abs(steigung) < MIN_STEIGUNG_DEG or abs(pitch) < MIN_STEIGUNG_DEG / 2:
            return None
        # Nase oben positiv: bergauf (Steigung +) misst der Sensor positiv.
        if steigung * pitch > 0:
            self._passt += 1
        else:
            self._gegen += 1
        n = self._passt + self._gegen
        if n < MIN_PROBEN:
            return None
        if self._passt >= EINIG_ANTEIL * n:
            self.bestaetigt = True
            return None
        if self._gegen >= EINIG_ANTEIL * n:
            self.vorzeichen = -self.vorzeichen
            self.bestaetigt = True
            self._passt, self._gegen = 0, 0
            self.meldung = ("Längsneigung des Neigungssensors lief andersherum als die Steigung "
                            "aus der RTK-Höhe - Vorzeichen umgedreht.")
            return self.vorzeichen
        return None
