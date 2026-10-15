"""Vorausschauende Spurregelung: Modell, Rückführung und der Vergleich mit Stanley."""

import math
import unittest
from collections import deque

from agripilot import vorausregler as vr
from agripilot.guidance import GuidanceLine, VehicleProfile


def kreisbogen(r=40.0, grad=270.0, schritt=2.0):
    """Rechtskurve: Kreis um (r, 0), Beginn bei (0, 0) nach Norden."""
    n = int(math.radians(grad) * r / schritt)
    return [(r + r * math.cos(math.pi - i * schritt / r), r * math.sin(math.pi - i * schritt / r))
            for i in range(n + 1)]


def fahre(linie, regelart, tempo, sekunden, start, gain=0.9, dt=0.1, tau=0.35, totzeit=0.2, L=2.6):
    """Fahrradmodell mit träger Lenkung, Totzeit und 25 Grad/s Ratenbegrenzung."""
    p = VehicleProfile(steer_gain=gain, regelart=regelart, lenk_traegheit_s=tau, wheelbase_m=L)
    x, y, h = start
    delta = 0.0
    puffer = deque([0.0] * int(totzeit / dt))
    quer, t = [], 0.0
    while t < sekunden:
        g = linie.solve((x, y), h, tempo, p)
        puffer.append(g.steer_angle_deg)
        ziel = delta + (puffer.popleft() - delta) * min(1.0, dt / tau)
        delta += max(-2.5, min(2.5, ziel - delta))
        hr = math.radians(h)
        x += tempo * math.sin(hr) * dt
        y += tempo * math.cos(hr) * dt
        h = (h + math.degrees(tempo / L * math.tan(math.radians(delta))) * dt) % 360.0
        t += dt
        quer.append(g.cross_track_m)
    return quer


def effektiv(quer, ab):
    rest = quer[ab:]
    return math.sqrt(sum(v * v for v in rest) / len(rest))


class ModellTest(unittest.TestCase):
    def test_the_feedback_pulls_towards_the_line(self):
        k = vr.lqr_verstaerkung(3.0, 2.6, 0.3, 4.0, 1.0, 10.0)
        self.assertGreater(k[0], 0.0)        # rechts der Spur -> nach links lenken
        self.assertGreater(k[1], 0.0)        # nach rechts gedreht -> nach links lenken

    def test_a_vehicle_on_the_line_steers_straight_and_in_a_curve_steers_into_it(self):
        p = VehicleProfile(regelart="voraus")
        regler = vr.Vorausregler()
        self.assertAlmostEqual(regler.einschlag(0.0, 0.0, 3.0, p, 0.0), 0.0)
        regler = vr.Vorausregler()
        for _ in range(30):                   # Radwinkel eingeschwungen
            einschlag = regler.einschlag(0.0, 0.0, 3.0, p, 1 / 40.0)
        self.assertAlmostEqual(einschlag, math.degrees(math.atan(2.6 / 40.0)), delta=0.2)

    def test_the_limits_of_each_side_apply(self):
        p = VehicleProfile(regelart="voraus", max_steer_deg=35.0, max_steer_left_deg=10.0)
        self.assertEqual(vr.Vorausregler().einschlag(5.0, 0.0, 3.0, p), -10.0)

    def test_the_curvature_of_a_right_hand_curve_is_positive_and_flips_when_reversed(self):
        linie = GuidanceLine("curve", kreisbogen(), 3.0)
        _, kurs, along = linie._curve_lateral((0.0, 10.0))
        self.assertAlmostEqual(linie._kruemmung((0.0, 10.0), kurs, along), 1 / 40.0, delta=0.004)
        self.assertAlmostEqual(linie._kruemmung((0.0, 10.0), (kurs + 180) % 360, along), -1 / 40.0, delta=0.004)

    def test_stanley_stays_the_default(self):
        self.assertEqual(VehicleProfile().regelart, "stanley")


class VergleichTest(unittest.TestCase):
    """Im Regelkreis mit träger Lenkung: vorausschauend mindestens so gut, in Kurven deutlich besser."""

    def test_on_a_straight_line(self):
        for tempo in (2.0, 6.0):
            stanley = effektiv(fahre(GuidanceLine("ab", [(0, 0), (0, 500)], 3.0), "stanley", tempo, 50, (0.8, 0, 0)), 50)
            voraus = effektiv(fahre(GuidanceLine("ab", [(0, 0), (0, 500)], 3.0), "voraus", tempo, 50, (0.8, 0, 0)), 50)
            self.assertLess(voraus, 0.01, tempo)
            self.assertLessEqual(voraus, stanley + 0.002, tempo)

    def test_in_a_curve(self):
        for tempo in (2.0, 4.0):
            stanley = effektiv(fahre(GuidanceLine("curve", kreisbogen(), 3.0), "stanley", tempo, 40, (0, 0, 0)), 60)
            voraus = effektiv(fahre(GuidanceLine("curve", kreisbogen(), 3.0), "voraus", tempo, 40, (0, 0, 0)), 60)
            # Die Testfahrt bleibt im Bogen (270 Grad, R 40 m = 188 m).
            self.assertLess(voraus, 0.12, tempo)
            self.assertLess(voraus, 0.5 * stanley, tempo)


if __name__ == "__main__":
    unittest.main()
