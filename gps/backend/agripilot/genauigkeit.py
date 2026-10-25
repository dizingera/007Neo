"""Wie genau wurde bei einer Arbeit gelenkt?

Gemessen wird nur, wo es etwas über die Lenkung sagt: in Fahrt, auf einer
Spur, nicht in der Wende. Daraus je Arbeit

* ``effektiv_cm``  - quadratisches Mittel der Abweichung (das, was man spürt),
* ``p95_cm``       - 95 % der Zeit lag die Abweichung darunter,
* ``max_cm``       - die größte Abweichung,
* ``lenkanteil``   - welcher Teil der Fahrt die Automatik gelenkt hat,
* ``abgaben``      - wie oft die Lenkung von sich aus abgegeben hat (nicht der Fahrer),
* ``abgabe_gruende`` - und warum: Grund -> Anzahl.

Gespeichert wird das Ergebnis in der Arbeit (Spalte ``genauigkeit``) - so sieht
man über Wochen, ob die Abstimmung besser wird. Für das 95-%-Maß reicht eine
Häufigkeitstabelle in halben Zentimetern; die Messwerte selbst bleiben nicht liegen.
"""

from __future__ import annotations

import math
from typing import Optional

FACH_CM = 0.5
FAECHER = 200            # bis 1 m; alles darüber landet im letzten Fach


class Genauigkeit:
    def __init__(self) -> None:
        self.zuruecksetzen()

    def zuruecksetzen(self, abgaben_start: int = 0, gruende_start: Optional[dict] = None) -> None:
        self.gruende_start = dict(gruende_start or {})
        self.n = 0
        self.summe2 = 0.0
        self.maximum = 0.0
        self.fahr_s = 0.0
        self.lenk_s = 0.0
        self.faecher = [0] * FAECHER
        self.abgaben_start = abgaben_start

    def messen(self, quer_m: float, lenkt: bool, auf_spur: bool, dt: float) -> None:
        """Ein Takt. ``auf_spur``: in Fahrt, Spur aktiv, keine Wende."""
        if not auf_spur or not (0.0 < dt < 2.0) or not math.isfinite(quer_m):
            return
        self.fahr_s += dt
        if not lenkt:
            return
        self.lenk_s += dt
        cm = abs(quer_m) * 100.0
        self.n += 1
        self.summe2 += cm * cm
        self.maximum = max(self.maximum, cm)
        self.faecher[min(FAECHER - 1, int(cm / FACH_CM))] += 1

    def _quantil(self, anteil: float) -> float:
        grenze = anteil * self.n
        summe = 0
        for i, anzahl in enumerate(self.faecher):
            summe += anzahl
            if summe >= grenze:
                return (i + 1) * FACH_CM
        return FAECHER * FACH_CM

    def ergebnis(self, abgaben_jetzt: int = 0, gruende_jetzt: Optional[dict] = None) -> Optional[dict]:
        """Die Zahlen für die Arbeit, oder None, solange nichts gelenkt wurde."""
        if self.n == 0:
            return None
        gruende = {grund: anzahl - self.gruende_start.get(grund, 0)
                   for grund, anzahl in (gruende_jetzt or {}).items()}
        return {
            # Warum abgegeben wurde, häufigster Grund zuerst (ab 1.5.81).
            "abgabe_gruende": dict(sorted(((g, a) for g, a in gruende.items() if a > 0),
                                          key=lambda e: -e[1])),
            "effektiv_cm": round(math.sqrt(self.summe2 / self.n), 1),
            "p95_cm": round(self._quantil(0.95), 1),
            "max_cm": round(self.maximum, 1),
            "lenkanteil": round(self.lenk_s / self.fahr_s, 3) if self.fahr_s else 0.0,
            "lenk_min": round(self.lenk_s / 60.0, 1),
            "abgaben": max(0, abgaben_jetzt - self.abgaben_start),
            "messungen": self.n,
        }
