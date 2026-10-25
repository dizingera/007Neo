"""Dauerversatz ausgleichen: Seitenhang, seitlich ziehendes Gerät, verstellte Radmitte.

Stanley und die vorausschauende Regelung lenken nur, solange eine Abweichung da
ist - eine Kraft, die die Maschine ständig zur Seite drückt, gleichen sie nie
ganz aus: am Hang oder mit dem Pflug läuft sie dauerhaft ein paar Zentimeter
neben der Spur. Wie in AgOpenGPS (CGuidance, „Stanley integral“) wird der
Restfehler deshalb langsam aufsummiert und als zusätzlicher Einschlag gegengelenkt:

* nur, wenn die Lenkung wirklich lenkt, in Fahrt (über 1 m/s), nahe der Spur
  (unter ``NAHE_M``) und ruhig (die Abweichung ändert sich langsam) - sonst
  würde jedes Einlenken auf die Spur und jede Wende aufsummiert;
* zeigt der Ausgleich schon in die falsche Richtung (die Maschine ist auf der
  anderen Seite), baut er sich dreimal so schnell ab - kein Aufschaukeln;
* ist es nur unruhig (Rauschen, Bodenwelle), pausiert er; weit weg von der Spur
  oder in der Wende klingt er rasch ab; aus und rückwärts ist er null;
* pendelt die Maschine um die Spur (die geglättete Abweichung wechselt in
  ``PENDEL_FENSTER_S`` mehrmals die Seite), summiert er nicht weiter und baut
  sich langsam ab: Bei einem trägen Lenkmotor ist das Aufsummieren selbst das,
  was die Lenkung aufschaukelt (Simulator, 1,3 s Nachlauf: mit Ausgleich 18 cm,
  ohne 1 cm);
* begrenzt auf ``GRENZE_DEG``;
* gemerkt **je Fahrtrichtung**: am Seitenhang drückt der Hang auf der Hinspur
  nach rechts, auf der Rückspur nach links. Nach der Wende geht es mit dem Wert
  weiter, der auf dieser Fahrtrichtung zuletzt gepasst hat, statt bei null.

``staerke`` (Grad je Meter und Sekunde) steht beim Traktor; 0 schaltet es ab.
Der zweite Teil, die Hang-Vorsteuerung, lenkt sofort mit der gemessenen
Seitenneigung dagegen, statt zu warten, bis die Maschine abgedriftet ist.
"""

from __future__ import annotations

import math
from collections import deque

NAHE_M = 0.25            # nur so nah an der Spur wird aufsummiert
RUHIG_M_S = 0.3          # schnelleres Wandern ist Einlenken, kein Dauerversatz
GLAETTUNG_S = 1.0        # über so lange wird die Abweichung geglättet, bevor ihr Wandern zählt
MIN_TEMPO_MS = 1.0
GRENZE_DEG = 6.0
ABKLINGEN_JE_S = 0.03    # außerhalb: in einer Sekunde auf 3 % (AgOpenGPS: 0,7 je Takt)
FALSCH_FAKTOR = 3.0
PENDEL_FENSTER_S = 10.0
PENDEL_SPANNE_M = 0.10   # so weit (Spitze zu Spitze) muss es pendeln
PENDEL_WECHSEL = 2       # so oft wechselt die Abweichung im Fenster die Seite
PENDEL_HYSTERESE_M = 0.02
PENDEL_ABKLINGEN_JE_S = 0.93   # pendelt es: in 10 s auf rund die Hälfte


class SpurIntegral:
    def __init__(self) -> None:
        self.wert_deg = 0.0
        self._vorher_quer: float | None = None
        self._glatt: float | None = None
        self._richtung = ""
        self.gemerkt: dict[str, float] = {}
        self._zeit = 0.0
        self._verlauf: deque = deque()     # (Zeit, geglättete Abweichung)
        self.pendelt = False

    def zuruecksetzen(self, alles: bool = True) -> None:
        self.wert_deg = 0.0
        self._vorher_quer = None
        self._glatt = None
        self._verlauf.clear()
        self.pendelt = False
        if alles:
            self.gemerkt = {}
            self._richtung = ""

    def verschieben(self, grad: float) -> None:
        """Nach einer Korrektur der Radmitte: was der Ausgleich davon schon getragen hat, abziehen."""
        self.wert_deg = max(-GRENZE_DEG, min(GRENZE_DEG, self.wert_deg + grad))
        self.gemerkt = {k: max(-GRENZE_DEG, min(GRENZE_DEG, v + grad)) for k, v in self.gemerkt.items()}

    def update(self, quer_m: float, dt: float, tempo_ms: float, lenkt: bool,
               auf_spur: bool, staerke: float, richtung: str = "") -> float:
        """Der zusätzliche Einschlag (Grad, + rechts). ``quer_m``: + = rechts der Spur.

        ``richtung``: in welcher Richtung die Spur befahren wird (z.B. "hin"/"her").
        """
        if staerke <= 0 or tempo_ms < 0 or not math.isfinite(quer_m):
            self.zuruecksetzen()
            return 0.0
        if not lenkt:
            # Lenkung aus: nichts wirkt, aber was je Richtung gelernt ist, bleibt -
            # die Lenkung geht nach jeder Wende kurz aus und wieder an.
            self.zuruecksetzen(alles=False)
            self._richtung = ""          # beim Wiedereinschalten den gemerkten Wert laden
            return 0.0
        if richtung != self._richtung:
            self._richtung = richtung
            self.wert_deg = self.gemerkt.get(richtung, 0.0)
            self._vorher_quer = None
            self._glatt = None
            self._verlauf.clear()
            self.pendelt = False
        if not (0.0 < dt < 1.0):
            self._vorher_quer = self._glatt = quer_m
            return self.wert_deg
        # Wandern an der geglätteten Abweichung messen: das Rauschen des Empfängers
        # (2 cm von Fix zu Fix sind 0,3 m/s) ist kein Einlenken.
        vorher = self._glatt if self._glatt is not None else quer_m
        self._glatt = vorher + (quer_m - vorher) * min(1.0, dt / GLAETTUNG_S)
        wandern = abs(self._glatt - vorher) / dt
        self._vorher_quer = quer_m
        self.pendelt = self._pendeln_merken(dt)
        nahe = auf_spur and tempo_ms > MIN_TEMPO_MS and abs(quer_m) < NAHE_M
        if self.pendelt:
            self.wert_deg *= PENDEL_ABKLINGEN_JE_S ** dt
        elif nahe and wandern < RUHIG_M_S:
            schritt = -staerke * quer_m * dt
            # Zeigt der Ausgleich in dieselbe Richtung wie die Abweichung, drückt er die
            # Maschine weiter weg: dann schneller zurücknehmen.
            if self.wert_deg * quer_m > 0:
                schritt *= FALSCH_FAKTOR
            self.wert_deg = max(-GRENZE_DEG, min(GRENZE_DEG, self.wert_deg + schritt))
            self.gemerkt[richtung] = self.wert_deg
        elif not nahe:
            self.wert_deg *= ABKLINGEN_JE_S ** dt
        return self.wert_deg

    def _pendeln_merken(self, dt: float) -> bool:
        self._zeit += dt
        self._verlauf.append((self._zeit, self._glatt))
        while self._verlauf and self._zeit - self._verlauf[0][0] > PENDEL_FENSTER_S:
            self._verlauf.popleft()
        if len(self._verlauf) < 10 or self._zeit - self._verlauf[0][0] < PENDEL_FENSTER_S / 2:
            return False
        werte = [w for _, w in self._verlauf]
        if max(werte) - min(werte) < PENDEL_SPANNE_M:
            return False
        mittel = sum(werte) / len(werte)
        wechsel, seite = 0, 0
        for w in werte:
            if w - mittel > PENDEL_HYSTERESE_M:
                wechsel += seite == -1
                seite = 1
            elif w - mittel < -PENDEL_HYSTERESE_M:
                wechsel += seite == 1
                seite = -1
        return wechsel >= PENDEL_WECHSEL


def hang_vorsteuerung(neigung_deg: float, faktor: float) -> float:
    """Einschlag gegen den Hangabtrieb (Grad, + rechts).

    ``neigung_deg`` wie beim Hangausgleich: + = nach rechts geneigt (rechte Seite
    tiefer). Dann drückt der Hang nach rechts, gelenkt wird nach links.
    """
    if not faktor or not math.isfinite(neigung_deg):
        return 0.0
    return max(-GRENZE_DEG, min(GRENZE_DEG, -faktor * neigung_deg))
