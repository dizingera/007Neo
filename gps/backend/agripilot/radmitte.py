"""Radmitte aus der Fahrt lernen (wie „Smart WAS“ in AgOpenGPS).

„Mitte lernen“ im Stand verlässt sich darauf, dass die Räder in dem Moment
wirklich gerade stehen. Schon ein halbes Grad daneben lässt die Maschine
dauerhaft zur Seite laufen. Beim Fahren lässt sich die Mitte genauer finden:
fährt die Maschine geradeaus auf der Spur, muss der gemessene Radwinkel im
Mittel null sein. Ist er es nicht, sitzt die Mitte um genau diesen Wert daneben.

Gesammelt wird nur, was etwas darüber sagt: Lenkung an, gerade Spur (AB),
mindestens ``MIN_TEMPO_MS``, nahe der Spur, kleiner Winkel. Der Median ist robust
gegen einzelne Ausreißer (Einlenken, Bodenwelle); die Streuung sagt, wie sicher
das Ergebnis ist.
"""

from __future__ import annotations

import math
from collections import deque
from statistics import median, pstdev
from typing import Optional

MAX_PROBEN = 2000
MIN_PROBEN = 200
MIN_TEMPO_MS = 2.0 / 3.6
MAX_WINKEL_DEG = 15.0
MAX_ABSTAND_M = 0.5
MAX_KURSFEHLER_DEG = 5.0
MAX_KORREKTUR_DEG = 5.0


class Radmitte:
    def __init__(self) -> None:
        self.sammelt = False
        self.proben: deque = deque(maxlen=MAX_PROBEN)

    def start(self) -> None:
        self.sammelt = True

    def stop(self) -> None:
        self.sammelt = False

    def zuruecksetzen(self) -> None:
        self.proben.clear()

    def probe(self, radwinkel_deg: Optional[float], lenkt: bool, gerade_spur: bool,
              tempo_ms: float, quer_m: float, kursfehler_deg: float) -> bool:
        """Einen Messwert nehmen, wenn er etwas über die Mitte sagt."""
        if (not self.sammelt or radwinkel_deg is None or not math.isfinite(radwinkel_deg)
                or not lenkt or not gerade_spur or tempo_ms < MIN_TEMPO_MS
                or abs(quer_m) > MAX_ABSTAND_M or abs(radwinkel_deg) > MAX_WINKEL_DEG
                or abs(kursfehler_deg) > MAX_KURSFEHLER_DEG):
            return False
        self.proben.append(radwinkel_deg)
        return True

    def verschieben(self, grad: float) -> None:
        """Nach dem Übernehmen: die gesammelten Werte zählen ab jetzt um ``grad`` versetzt."""
        self.proben = deque((p - grad for p in self.proben), maxlen=MAX_PROBEN)

    def ergebnis(self) -> dict:
        n = len(self.proben)
        aus = {"sammelt": self.sammelt, "proben": n, "noetig": MIN_PROBEN, "gueltig": False}
        if n < MIN_PROBEN:
            return aus
        werte = list(self.proben)
        mitte = median(werte)
        streuung = pstdev(werte)
        # Sicherheit: genug Proben und wenig Streuung. Bei 1° Streuung und 1000 Proben
        # ist der Median auf Hundertstel genau - die Zahl ist eine Einordnung, kein Beweis.
        vertrauen = min(1.0, n / 1000.0) * max(0.0, 1.0 - streuung / 3.0)
        aus.update(median_deg=round(mitte, 2), mittel_deg=round(sum(werte) / n, 2),
                   streuung_deg=round(streuung, 2), vertrauen=round(vertrauen, 2),
                   korrektur_deg=round(mitte, 2),
                   gueltig=vertrauen >= 0.5 and abs(mitte) <= MAX_KORREKTUR_DEG and abs(mitte) >= 0.05)
        return aus
