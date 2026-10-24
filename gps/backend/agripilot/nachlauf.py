"""Nachlauf der Lenkung messen: wie lange das Rad braucht, um dem Befehl zu folgen.

Zwischen Befehl und Rad liegen am Traktor USB, Motorregler, das Anlaufen des
Motors, Spiel im Gestänge und die Servolenkung. Die Regelart „Vorausschauend“
rechnet mit genau dieser Zeit (``lenk_traegheit_s``). Stimmt sie, hält sie die
Spur auch mit einem langsamen Motor; Stanley dagegen schaukelt sich dort auf
(Simulator, 1,3 s Nachlauf: Stanley pendelt meterweit, „Vorausschauend“ mit
1,3 s Trägheit bleibt unter 1 cm - mit der Vorgabe 0,3 s aber nicht).

Gemessen wird, solange die Lenkung lenkt und der Drehgeber einen Radwinkel
meldet. Aus Befehl und Rad der letzten ``FENSTER_S`` Sekunden wird die
Zeitkonstante eines Verzögerungsglieds erster Ordnung gesucht, das aus dem
Befehl das Rad am besten nachbildet, dazu ein fester Versatz für die Radmitte.
Totzeit und begrenzte Lenkgeschwindigkeit stecken darin mit: gemessen wird der
wirksame Nachlauf, so wie ihn der Regler sieht. Sicher ist das Ergebnis erst,
wenn sich der Befehl genug bewegt hat und die Nachbildung gut passt - auf
gerader Spur mit ruhigem Befehl gibt es nichts zu messen.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Optional

FENSTER_S = 30.0          # so viel Fahrt mit Lenkung wird ausgewertet
MIN_DAUER_S = 12.0        # darunter gibt es kein Ergebnis
MIN_STREUUNG_DEG = 0.4    # so viel muss sich der Befehl bewegt haben
MIN_GUETE = 0.6           # Anteil der Radbewegung, den die Nachbildung erklären muss
LUECKE_S = 0.5            # längere Pause: Nachbildung setzt neu auf
T_MAX_S = 2.5
T_SCHRITT_S = 0.05
NEU_RECHNEN_S = 1.0       # höchstens so oft wird neu ausgewertet


def _nachbilden(proben: list, t_s: float, versatz: float) -> list[float]:
    """Das Rad, wie es ein Verzögerungsglied mit ``t_s`` aus dem Befehl machen würde."""
    modell, y, vorher = [], 0.0, None
    for zeit, befehl, rad in proben:
        if vorher is None or zeit - vorher[0] > LUECKE_S:
            y = rad - versatz                        # nach einer Lücke: dort aufsetzen, wo das Rad steht
        else:
            dt = zeit - vorher[0]
            y += (vorher[1] - y) * (1.0 - math.exp(-dt / t_s))
        modell.append(y + versatz)
        vorher = (zeit, befehl)
    return modell


def schaetzen(proben: list) -> dict:
    """Nachlauf aus ``(zeit, befehl_deg, rad_deg)`` schätzen."""
    leer = {"nachlauf_s": None, "guete": 0.0, "sicher": False, "dauer_s": 0.0, "streuung_deg": 0.0}
    if len(proben) < 20:
        return leer
    dauer = sum(min(b[0] - a[0], LUECKE_S) for a, b in zip(proben, proben[1:]))
    befehle = [p[1] for p in proben]
    raeder = [p[2] for p in proben]
    mittel_b = sum(befehle) / len(befehle)
    streuung = math.sqrt(sum((b - mittel_b) ** 2 for b in befehle) / len(befehle))
    mittel_r = sum(raeder) / len(raeder)
    gesamt = sum((r - mittel_r) ** 2 for r in raeder)
    ergebnis = {**leer, "dauer_s": round(dauer, 1), "streuung_deg": round(streuung, 2)}
    if gesamt <= 1e-9:
        return ergebnis
    def fehler_bei(t_s: float) -> float:
        # Erst ohne Versatz nachbilden, dann den mittleren Rest als Versatz der
        # Radmitte nehmen und noch einmal - mehr braucht ein fester Versatz nicht.
        roh = _nachbilden(proben, t_s, 0.0)
        versatz = sum(r - m for r, m in zip(raeder, roh)) / len(raeder)
        modell = _nachbilden(proben, t_s, versatz)
        return sum((r - m) ** 2 for r, m in zip(raeder, modell))

    # Grob, dann fein um das beste: das läuft im Takt der Lenkung mit und soll kurz bleiben.
    grob = 3 * T_SCHRITT_S
    kandidaten = [k * grob for k in range(1, int(T_MAX_S / grob) + 1)]
    bestes = min((fehler_bei(t), t) for t in kandidaten)
    fein = [bestes[1] + k * T_SCHRITT_S for k in (-2, -1, 1, 2)]
    bestes = min([bestes] + [(fehler_bei(t), t) for t in fein if T_SCHRITT_S <= t <= T_MAX_S])
    guete = max(0.0, 1.0 - bestes[0] / gesamt)
    sicher = dauer >= MIN_DAUER_S and streuung >= MIN_STREUUNG_DEG and guete >= MIN_GUETE
    return {**ergebnis, "nachlauf_s": round(bestes[1], 2), "guete": round(guete, 2), "sicher": sicher}


class Nachlauf:
    """Sammelt Befehl und Radwinkel, solange gelenkt wird, und schätzt daraus den Nachlauf."""

    def __init__(self) -> None:
        self.proben: deque = deque()
        self._ergebnis = schaetzen([])
        self._gerechnet = -1e9

    def probe(self, zeit: float, befehl_deg: Optional[float], rad_deg: Optional[float], lenkt: bool) -> None:
        if not lenkt or befehl_deg is None or rad_deg is None:
            return
        if not (math.isfinite(befehl_deg) and math.isfinite(rad_deg)):
            return
        self.proben.append((zeit, float(befehl_deg), float(rad_deg)))
        while self.proben and zeit - self.proben[0][0] > FENSTER_S:
            self.proben.popleft()

    def ergebnis(self, jetzt: Optional[float] = None) -> dict:
        if jetzt is None:
            jetzt = self.proben[-1][0] if self.proben else 0.0
        if abs(jetzt - self._gerechnet) >= NEU_RECHNEN_S:
            self._ergebnis = schaetzen(list(self.proben))
            self._gerechnet = jetzt
        return self._ergebnis

    def sicher_s(self) -> Optional[float]:
        """Der Nachlauf in Sekunden - nur, wenn die Messung trägt."""
        e = self.ergebnis()
        return e["nachlauf_s"] if e["sicher"] else None

    def zuruecksetzen(self) -> None:
        self.proben.clear()
        self._ergebnis = schaetzen([])
        self._gerechnet = -1e9
