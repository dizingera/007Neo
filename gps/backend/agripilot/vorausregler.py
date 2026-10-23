"""Vorausschauende Spurregelung: ein Fahrzeugmodell rechnet, wie die Maschine
auf die Lenkung reagiert, und wählt den Einschlag, der über die nächsten
Sekunden am wenigsten Abweichung und am wenigsten Lenkarbeit kostet.

Der Stanley-Regler (guidance._steer_angle) schaut nur auf den Augenblick:
Abweichung und Kurswinkel jetzt. Er weiß nicht, dass die Lenkung eines
Traktors träge ist und eine Kurve vor ihm liegt - deshalb läuft er in Kurven
nach außen und pendelt bei Tempo leichter. Hier dagegen:

* **Modell.** Querabweichung ``e_y`` (m, + rechts), Kurswinkel ``e_psi`` (rad,
  + nach rechts gedreht) und der tatsächliche Radwinkel ``delta`` (rad), der dem
  verlangten Einschlag mit einer Zeitkonstante ``tau`` folgt:

      e_y'   = v * e_psi
      e_psi' = v / L * delta - v * kappa
      delta' = (u - delta) / tau

* **Optimale Rückführung (LQR).** Für dieses Modell wird die Rückführung
  gerechnet, die die Summe aus gewichteter Abweichung, Kurswinkel und Einschlag
  über einen unendlichen Horizont kleinstmöglich hält - das ist die Grenzform
  der modellprädiktiven Regelung ohne Beschränkungen. Die Grenzen (Anschläge,
  Lenkgeschwindigkeit) kommen danach, wie beim Stanley-Regler.
* **Vorsteuerung der Krümmung.** In einer Kurve braucht es den Einschlag
  ``atan(L * kappa)``, auch wenn die Maschine genau auf der Spur ist. Der kommt
  vorab dazu; geregelt wird nur die Abweichung davon.

Die Verstärkung ``steer_gain`` bleibt der eine Drehknopf (sie gewichtet die
Abweichung), damit der Lenk-Assistent auch diese Regelart abstimmen kann.

Ohne numpy: die Matrizen sind 3x3, die Riccati-Iteration läuft in Python in
Millisekunden und wird je Tempo (auf 0,1 m/s gerundet) zwischengespeichert.
"""

from __future__ import annotations

import math
from typing import Optional

TAKT_S = 0.1                 # Diskretisierung des Modells
Q_ABWEICHUNG = 4.0           # Gewicht der Querabweichung (je m^2), mal steer_gain^2
Q_KURS = 1.0                 # Gewicht des Kurswinkels (je rad^2)
R_EINSCHLAG = 10.0           # Gewicht des Einschlags (je rad^2) - Lenkarbeit; kleiner wird bei Tempo und träger Lenkung unruhig
RICCATI_SCHRITTE = 400


def _mal(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))] for i in range(len(a))]


def _plus(a, b):
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def _minus(a, b):
    return [[a[i][j] - b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def _t(a):
    return [list(z) for z in zip(*a)]


def lqr_verstaerkung(tempo_ms: float, radstand_m: float, tau_s: float,
                     q_y: float, q_psi: float, r: float) -> list[float]:
    """Rückführung K (u = -K x) für das Modell bei diesem Tempo."""
    v = max(0.3, abs(tempo_ms))
    dt = TAKT_S
    tau = max(0.05, tau_s)
    a = [[1.0, dt * v, 0.0],
         [0.0, 1.0, dt * v / radstand_m],
         [0.0, 0.0, 1.0 - dt / tau]]
    b = [[0.0], [0.0], [dt / tau]]
    q = [[q_y, 0.0, 0.0], [0.0, q_psi, 0.0], [0.0, 0.0, 0.0]]
    p = [row[:] for row in q]
    k = [[0.0, 0.0, 0.0]]
    for _ in range(RICCATI_SCHRITTE):
        bt_p = _mal(_t(b), p)                       # 1x3
        nenner = r + _mal(bt_p, b)[0][0]
        k = [[x / nenner for x in _mal(bt_p, a)[0]]]   # 1x3
        a_bk = _minus(a, _mal(b, k))
        neu = _plus(q, _plus(_mal(_mal(_t(k), [[r]]), k), _mal(_mal(_t(a_bk), p), a_bk)))
        if max(abs(neu[i][j] - p[i][j]) for i in range(3) for j in range(3)) < 1e-9:
            p = neu
            break
        p = neu
    return k[0]


class Vorausregler:
    """Hält den geschätzten Radwinkel und die zwischengespeicherten Rückführungen."""

    def __init__(self) -> None:
        self._delta = 0.0                 # geschätzter Radwinkel (rad), aus den eigenen Befehlen
        self._letzt: Optional[float] = None
        self._cache: dict = {}

    def zuruecksetzen(self) -> None:
        self._delta = 0.0
        self._letzt = None

    def _k(self, tempo_ms: float, profil) -> list[float]:
        gain = max(0.05, float(profil.steer_gain))
        schluessel = (round(abs(tempo_ms), 1), round(profil.wheelbase_m, 2),
                      round(profil.lenk_traegheit_s, 2), round(gain, 3))
        if schluessel not in self._cache:
            if len(self._cache) > 400:
                self._cache.clear()
            self._cache[schluessel] = lqr_verstaerkung(
                schluessel[0], profil.wheelbase_m, profil.lenk_traegheit_s,
                Q_ABWEICHUNG * gain * gain, Q_KURS, R_EINSCHLAG)
        return self._cache[schluessel]

    def einschlag(self, quer_m: float, kursfehler_deg: float, tempo_ms: float, profil,
                  kruemmung: float = 0.0, jetzt: Optional[float] = None,
                  radwinkel_deg: Optional[float] = None) -> float:
        """Einschlag in Grad (+ = rechts).

        ``kursfehler_deg`` wie in GuidanceState: Sollkurs minus Istkurs, also
        positiv, wenn nach rechts gelenkt werden muss. ``kruemmung`` (1/m, +
        Rechtskurve in Fahrtrichtung). ``radwinkel_deg``: gemessen, wenn es eine
        Rückmeldung gibt - sonst wird er aus den eigenen Befehlen geschätzt.
        """
        L = max(0.5, profil.wheelbase_m)
        tau = max(0.05, profil.lenk_traegheit_s)
        if jetzt is not None:
            dt = TAKT_S if self._letzt is None else max(0.0, min(0.5, jetzt - self._letzt))
            self._letzt = jetzt
        else:
            dt = TAKT_S
        if radwinkel_deg is not None:
            self._delta = math.radians(radwinkel_deg)
        vor = math.atan(L * kruemmung)                       # Vorsteuerung der Kurve
        x = [quer_m, -math.radians(kursfehler_deg), self._delta - vor]
        k = self._k(tempo_ms, profil)
        u = vor - sum(ki * xi for ki, xi in zip(k, x))
        befehl = profil.einschlag_begrenzen(math.degrees(u))
        # Schätzung des Radwinkels mit demselben Modell weiterführen.
        self._delta += (math.radians(befehl) - self._delta) * min(1.0, dt / tau)
        return befehl
