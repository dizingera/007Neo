"""Spurregelung wie bei Cerea: ein Punkt voraus auf der Spur, dazu D, I und I2.

Cerea lenkt nicht auf den Abstand zur Spur selbst, sondern peilt einen Punkt auf
der Spur ein Stück voraus an (P-Anteil, „Pure Pursuit“). Wie weit voraus, ist der
eigentliche Drehknopf:

* **fest:** ``L`` (Meter) ab der Antenne - so steht es im Cerea-Handbuch 300.40
  („minimum distance between the antenna and the imaginary point“, Antenne auf
  der Haube). Gerechnet wird von der Hinterachse aus; sitzt die Antenne hinter
  der Vorderachse (auf dem Kabinendach), zählt mindestens ein Radstand, sonst
  läge der Punkt so nah, dass jede Lenkung schaukelt;
* **mit dem Tempo:** ``VORAUS_S`` Sekunden Fahrweg, geteilt durch die
  *Lenkaggressivität* (100 = so, 200 = halber Weg, also schärfer; 50 = doppelt,
  also ruhiger). Bei Tempo wandert der Punkt weiter weg - sonst schaukelt sich
  die Maschine auf, was im Schritttempo genau richtig lenkt;
* **mit dem Abstand zur Spur:** ``AUFFAHR_FAKTOR`` mal die Abweichung, geteilt
  durch die *Auffahraggressivität*. Wer 50 cm daneben einschaltet, soll zügig,
  aber nicht quer auf die Spur fahren; nahe der Spur rückt der Punkt wieder heran.

Ein Punkt voraus ist von sich aus gedämpft: Die Maschine fährt eine Kurve auf ihn
zu, statt auf die Spur zu springen - und zwar bei jedem Tempo gleich ruhig.

* **D** schaut, wie schnell sich die Maschine auf die Spur zu bewegt, und lenkt
  früher zurück (gegen das Überschwingen);
* **I** summiert den Rest auf, der bleibt (Hang, ziehendes Gerät) - mit kurzem
  Gedächtnis (``I_GEDAECHTNIS_S``), damit er nach dem Hang nicht nachwirkt;
* **I2** ist der zweite, langsame Teil ohne Vergessen: gegen das Driften parallel
  zur Spur, das die ganze Spur lang bleibt (Cerea-Grundsätze 300.40: „I² driften
  parallel zur AB-Linie, max. 1/2 vom I-Wert“; Handbuch: „secondary integral factor.
  Delete remaining XTE“). D dämpft wie ein Stoßdämpfer (Handbuch: üblich 0,1, bis 0,7;
  bei der Saat hinter dem Grubber eher hoch).

Empfohlene Bereiche aus den Cerea-Grundsätzen: D 0,1-0,5, I 0,05-0,2, I2 0,02-0,10;
L nach geplantem Tempo (7 km/h 0,2 · 10 km/h 0,3 · 15 km/h 0,4 · 20 km/h 0,5).

Abgestimmt an einem Traktormodell mit träger Lenkung (Zeitkonstante bis 1 s,
Totzeit bis 0,5 s): Der Stanley-Regler auf die Hinterachse kippt dort bei 7 km/h
ins Aufschaukeln, dieser bleibt unter 4 cm. Die Grundwerte sind die aus Andys
Cerea (Aggressivitäten 100, L 0,10, I 0,09, I2 0,06, D 0,45).
"""

from __future__ import annotations

import math

VORAUS_S = 1.2            # Sekunden Fahrweg bis zum Punkt bei Lenkaggressivität 100
AUFFAHR_FAKTOR = 2.0      # je Meter Abweichung so viele Meter weiter weg (Cerea-Erklärung: 10 cm -> 20 cm)
MIN_ABSTAND_M = 1.0
D_GRAD_JE_M_S = 20.0      # D = 1: so viele Grad je m/s, mit der die Maschine auf die Spur zuläuft
I_GRAD_JE_M_S = 20.0      # I = 1: so viele Grad je Meter und Sekunde Abweichung
I2_GRAD_JE_M_S = 10.0
I_GEDAECHTNIS_S = 10.0
NAHE_M = 0.25             # nur so nah an der Spur wird aufsummiert
MIN_TEMPO_MS = 0.8
GRENZE_DEG = 6.0

# Wertebereiche wie in der Oberfläche; was außerhalb liegt, wird darauf begrenzt.
BEREICH = {"cerea_lenk_aggr": (10.0, 200.0), "cerea_auffahr_aggr": (10.0, 200.0),
           "cerea_l": (0.0, 3.0), "cerea_i": (0.0, 1.0), "cerea_i2": (0.0, 1.0), "cerea_d": (0.0, 2.0)}


def wert(profile, name: str) -> float:
    unten, oben = BEREICH[name]
    try:
        zahl = float(getattr(profile, name))
    except (TypeError, ValueError):
        return unten
    return min(oben, max(unten, zahl)) if math.isfinite(zahl) else unten


def zielabstand(abweichung_m: float, tempo_ms: float, profile) -> float:
    """Wie weit voraus der angepeilte Punkt liegt (m)."""
    return max(MIN_ABSTAND_M,
               max(1.0, profile.wheelbase_m, profile.antenna_forward_m) + wert(profile, "cerea_l")
               + VORAUS_S * abs(tempo_ms) * 100.0 / wert(profile, "cerea_lenk_aggr")
               + AUFFAHR_FAKTOR * abs(abweichung_m) * 100.0 / wert(profile, "cerea_auffahr_aggr"))


def einschlag(abweichung_m: float, kursfehler_deg: float, tempo_ms: float,
              profile, kruemmung: float = 0.0) -> float:
    """Einschlag (Grad, + rechts) aus Punkt voraus, D und Krümmung - ohne I/I2.

    ``abweichung_m``: + rechts der Spur; ``kursfehler_deg``: Spurkurs minus Kurs der
    Maschine (+ heißt: die Spur liegt rechts herum). ``kruemmung``: 1/m, + Rechtskurve.
    """
    psi = math.radians(kursfehler_deg)
    abstand = zielabstand(abweichung_m, tempo_ms, profile)
    # Im Fahrzeug (x rechts, y vorn): Fußpunkt auf der Spur, dann so weit entlang der
    # Spur, dass der Punkt im Zielabstand liegt.
    fuss_x, fuss_y = -abweichung_m * math.cos(psi), abweichung_m * math.sin(psi)
    entlang = math.sqrt(max(abstand * abstand - abweichung_m * abweichung_m, 0.0))
    ziel_x, ziel_y = fuss_x + entlang * math.sin(psi), fuss_y + entlang * math.cos(psi)
    winkel = math.atan2(ziel_x, ziel_y)
    weit = max(math.hypot(ziel_x, ziel_y), 0.3)
    radstand = max(1.0, profile.wheelbase_m)
    p_anteil = math.degrees(math.atan(2.0 * radstand * math.sin(winkel) / weit))
    zulauf = -tempo_ms * math.sin(psi)          # m/s nach rechts von der Spur weg
    d_anteil = -wert(profile, "cerea_d") * D_GRAD_JE_M_S * zulauf
    kurve = math.degrees(math.atan(radstand * kruemmung)) if kruemmung else 0.0
    return profile.einschlag_begrenzen(p_anteil + d_anteil + kurve)


class CereaIntegral:
    """I und I2: der Rest, den der Punkt voraus allein nicht wegbekommt.

    Wie der Dauerversatz-Ausgleich (spurintegral.py) nur, wenn die Lenkung lenkt,
    in Fahrt, nahe der Spur; je Fahrtrichtung gemerkt (am Hang drückt es auf der
    Rückspur zur anderen Seite).
    """

    def __init__(self) -> None:
        self.i_deg = 0.0
        self.i2_deg = 0.0
        self._richtung = ""
        self._gemerkt: dict[str, tuple[float, float]] = {}

    @property
    def summe(self) -> float:
        return self.i_deg + self.i2_deg

    def update(self, abweichung_m: float, dt: float, tempo_ms: float, lenkt: bool,
               auf_spur: bool, profile, richtung: str = "") -> float:
        if richtung != self._richtung:
            if self._richtung:
                self._gemerkt[self._richtung] = (self.i_deg, self.i2_deg)
            self.i_deg, self.i2_deg = self._gemerkt.get(richtung, (0.0, 0.0))
            self._richtung = richtung
        if not lenkt or tempo_ms < 0:
            self.i_deg = self.i2_deg = 0.0
            return 0.0
        dt = min(max(dt, 0.0), 0.5)
        if not auf_spur or abs(abweichung_m) > 1.0:
            abbau = math.exp(-dt / 2.0)            # in der Wende, weit weg: rasch vergessen
            self.i_deg *= abbau
            self.i2_deg *= abbau
        elif tempo_ms >= MIN_TEMPO_MS and abs(abweichung_m) <= NAHE_M:
            self.i_deg += (-wert(profile, "cerea_i") * I_GRAD_JE_M_S * abweichung_m
                           - self.i_deg / I_GEDAECHTNIS_S) * dt
            self.i2_deg += -wert(profile, "cerea_i2") * I2_GRAD_JE_M_S * abweichung_m * dt
        self.i_deg = max(-GRENZE_DEG, min(GRENZE_DEG, self.i_deg))
        self.i2_deg = max(-GRENZE_DEG, min(GRENZE_DEG, self.i2_deg))
        return self.summe

    def verschieben(self, grad: float) -> None:
        """Nach einer Korrektur der Radmitte den Teil, den I2 dafür gegengelenkt hat, herausnehmen."""
        self.i2_deg = max(-GRENZE_DEG, min(GRENZE_DEG, self.i2_deg + grad))
