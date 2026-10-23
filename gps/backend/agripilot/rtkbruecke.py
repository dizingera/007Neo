"""RTK-Ausfall überbrücken: bei kurzem Verlust des RTK-Fix weiterlenken.

Hinter einer Baumreihe, im Funkloch oder wenn der Caster kurz hängt, fällt der
Empfänger von RTK-Fix auf Float, DGPS oder Einzelpunkt zurück. Diese Lösung
liegt um Dezimeter bis Meter daneben - springt aber von Sekunde zu Sekunde nur
wenig. Kommerzielle Systeme (Trimble xFill, John Deere RTK Extend) nutzen das:
Sie merken sich beim Abriss, wie weit die Ersatzlösung von der letzten
RTK-Position entfernt liegt, und rechnen diesen Versatz weiter heraus.

Genau das passiert hier:

* Beim Abriss wird die letzte RTK-Position mit Tempo und Kurs bis jetzt
  fortgeschrieben. Der Unterschied zur ersten Ersatzposition ist der Versatz.
* Jede weitere Ersatzposition bekommt diesen Versatz dazu - so bleibt die Spur
  ohne Sprung stehen, und die Abweichung wächst nur so schnell, wie die
  Ersatzlösung driftet (typisch wenige Zentimeter je Minute).
* Nach ``max_s`` Sekunden ist Schluss; ebenso sofort, wenn die Ersatzlösung
  springt (mehr als ``SPRUNG_M`` neben dem, was die Fahrt erklärt). Die Lenkung
  gibt dann wie bisher ab. Erst ein neuer RTK-Fix erlaubt die nächste Brücke.
"""

from __future__ import annotations

import math
from typing import Optional

LUECKE_S = 2.0      # so frisch muss die letzte RTK-Position beim Abriss sein
SPRUNG_M = 0.30     # mehr Sprung der Ersatzlösung beendet die Brücke

Punkt = tuple[float, float]


class RtkBruecke:
    def __init__(self) -> None:
        self.zuruecksetzen()

    def zuruecksetzen(self) -> None:
        self.aktiv = False
        self.abgelaufen = False      # bis zum nächsten RTK-Fix keine neue Brücke
        self.seit: Optional[float] = None
        self.versatz: Punkt = (0.0, 0.0)
        self.grund = ""
        self.ereignis = ""           # einmalig: was die Engine melden soll
        self._letzte_rtk: Optional[tuple[Punkt, float, float, float]] = None
        self._vorige: Optional[tuple[Punkt, float]] = None

    def update(self, position: Punkt, rank: int, tempo_ms: float, kurs_deg: Optional[float],
               jetzt: float, max_s: float) -> Punkt:
        """Die Position, mit der weitergerechnet wird. ``rank`` wie Fix.rank (4 = RTK-Fix)."""
        self.ereignis = ""
        if max_s <= 0:
            self.zuruecksetzen()
            return position
        if rank >= 4:
            if self.aktiv:
                self.ereignis = f"RTK wieder da nach {jetzt - self.seit:.0f} s Überbrückung"
            self.aktiv = False
            self.abgelaufen = False
            self.grund = ""
            self._letzte_rtk = (position, jetzt, kurs_deg if kurs_deg is not None else 0.0, tempo_ms)
            self._vorige = None
            return position

        if not self.aktiv:
            if self.abgelaufen or self._letzte_rtk is None or kurs_deg is None:
                return position
            vorher, t0, kurs0, tempo0 = self._letzte_rtk
            if jetzt - t0 > LUECKE_S:
                return position
            h = math.radians(kurs0)
            weg = tempo0 * (jetzt - t0)
            vorhersage = (vorher[0] + math.sin(h) * weg, vorher[1] + math.cos(h) * weg)
            self.versatz = (vorhersage[0] - position[0], vorhersage[1] - position[1])
            self.aktiv = True
            self.seit = jetzt
            self.grund = ""
            self._vorige = None
            self.ereignis = f"RTK weg - Lenkung überbrückt bis zu {max_s:.0f} s"

        if jetzt - self.seit > max_s:
            return self._beenden(position, f"RTK seit {max_s:.0f} s weg - Überbrückung beendet")
        korrigiert = (position[0] + self.versatz[0], position[1] + self.versatz[1])
        if self._vorige is not None:
            vorige, t_vorige = self._vorige
            weg = math.hypot(korrigiert[0] - vorige[0], korrigiert[1] - vorige[1])
            erwartet = tempo_ms * max(0.0, jetzt - t_vorige)
            if abs(weg - erwartet) > SPRUNG_M:
                return self._beenden(position, "Ersatzposition gesprungen - Überbrückung beendet")
        self._vorige = (korrigiert, jetzt)
        return korrigiert

    def _beenden(self, position: Punkt, grund: str) -> Punkt:
        self.aktiv = False
        self.abgelaufen = True
        self.grund = grund
        self.ereignis = grund
        self._vorige = None
        return position

    def status(self, jetzt: float, max_s: float) -> dict:
        return {
            "aktiv": self.aktiv,
            "dauer_s": round(jetzt - self.seit, 1) if self.aktiv and self.seit else 0.0,
            "rest_s": round(max(0.0, max_s - (jetzt - self.seit)), 1) if self.aktiv and self.seit else 0.0,
            "grund": self.grund,
        }
