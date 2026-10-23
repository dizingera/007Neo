"""Lenk-Assistent: Auswertung der Querabweichung und die Nachstellung der Regelung."""

import math
import random
import unittest
from collections import deque

from agripilot import abstimmung as ab
from agripilot.guidance import GuidanceLine, VehicleProfile


def reihe(fn, sekunden=20.0, dt=0.1):
    n = int(sekunden / dt)
    return [i * dt for i in range(n)], [fn(i * dt) for i in range(n)]


class BewertenTest(unittest.TestCase):
    def test_a_steady_swing_is_oscillation(self):
        t, q = reihe(lambda s: 0.08 * math.sin(2 * math.pi * s / 5.0))
        b = ab.bewerte(t, q)
        self.assertEqual(b.art, "schwingt")
        self.assertAlmostEqual(b.periode_s, 5.0, delta=0.7)
        self.assertGreater(b.amplitude_m, 0.05)

    def test_a_large_steady_offset_without_swinging_is_sluggish(self):
        t, q = reihe(lambda s: 0.16 + 0.01 * math.sin(s))
        b = ab.bewerte(t, q)
        self.assertEqual(b.art, "traege")
        self.assertAlmostEqual(b.mittel_m, 0.16, delta=0.01)

    def test_noise_around_zero_is_good(self):
        random.seed(3)
        t, q = reihe(lambda s: random.gauss(0, 0.01))
        self.assertEqual(ab.bewerte(t, q).art, "gut")

    def test_a_decaying_swing_is_good(self):
        t, q = reihe(lambda s: 0.2 * math.exp(-s / 3.0) * math.cos(2 * math.pi * s / 4.0))
        self.assertEqual(ab.bewerte(t, q).art, "gut")

    def test_growing_swings_are_recognised_early(self):
        t, q = reihe(lambda s: 0.03 * math.exp(s / 4.0) * math.sin(2 * math.pi * s / 4.0), sekunden=9.0)
        self.assertTrue(ab.bewerte(t, q).wachsend)


class Welt:
    """Ein Profil, das der Assistent lesen und verstellen kann."""

    def __init__(self, gain=0.9, latenz=0):
        self.werte = {"steer_gain": gain, "actuator_latency_ms": latenz}

    def lesen(self):
        return dict(self.werte)

    def setzen(self, neu):
        self.werte.update(neu)


def fenster(tuner, quer, start=0.0, sekunden=21.0, lenkt=True, ruhig=True, tempo=3.0):
    t = start
    while t < start + sekunden:
        tuner.beobachten(t, quer(t), tempo, lenkt, ruhig)
        t += 0.1
    return t


def schwingen(s):
    return 0.08 * math.sin(2 * math.pi * s / 5.0)


class AbstimmungTest(unittest.TestCase):
    def neu(self, modus, gain=0.9, latenz=0):
        welt = Welt(gain, latenz)
        tuner = ab.Lenkabstimmung(welt.lesen, welt.setzen)
        tuner.einschalten(modus)
        return welt, tuner

    def test_oscillation_lowers_the_gain_one_small_step_per_window(self):
        welt, tuner = self.neu("automatisch")
        fenster(tuner, schwingen)
        self.assertAlmostEqual(welt.werte["steer_gain"], 0.9 * ab.SCHRITT_RUNTER, places=3)
        self.assertEqual(len(tuner.schritte), 1)
        self.assertEqual(tuner.befund.art, "schwingt")

    def test_observe_mode_only_suggests(self):
        welt, tuner = self.neu("beobachten")
        fenster(tuner, schwingen)
        self.assertEqual(welt.werte["steer_gain"], 0.9)
        self.assertIn("Vorschlag", tuner.hinweis)

    def test_at_the_lowest_gain_the_latency_compensation_is_raised(self):
        welt, tuner = self.neu("automatisch", gain=ab.GAIN_MIN)
        fenster(tuner, schwingen)
        self.assertEqual(welt.werte["steer_gain"], ab.GAIN_MIN)
        self.assertEqual(welt.werte["actuator_latency_ms"], ab.LATENZ_SCHRITT_MS)

    def test_nothing_changes_unless_the_machine_steers_calmly_at_a_sensible_speed(self):
        for kwargs in ({"lenkt": False}, {"ruhig": False}, {"tempo": 0.3}, {"tempo": 9.0}):
            welt, tuner = self.neu("automatisch")
            fenster(tuner, schwingen, **kwargs)
            self.assertEqual(welt.werte["steer_gain"], 0.9, kwargs)
            self.assertEqual(tuner.schritte, [], kwargs)

    def test_an_interruption_discards_the_window(self):
        welt, tuner = self.neu("automatisch")
        t = fenster(tuner, schwingen, sekunden=12.0)
        t = fenster(tuner, schwingen, start=t, sekunden=3.0, ruhig=False)       # Wende / Spurwechsel
        t = fenster(tuner, schwingen, start=t + 2.0, sekunden=12.0)              # Lücke > 1,5 s dazwischen
        self.assertEqual(tuner.schritte, [])                                      # weder 20 s noch zusammenhängend

    def test_sluggish_raises_the_gain_as_an_experiment_and_takes_it_back_if_it_does_not_help(self):
        welt, tuner = self.neu("automatisch", gain=0.5)
        offset = lambda s: 0.15                                                   # bleibt, egal was der Regler tut
        t = fenster(tuner, offset)
        self.assertAlmostEqual(welt.werte["steer_gain"], 0.5 * ab.SCHRITT_HOCH, places=3)
        t = fenster(tuner, offset, start=t + 0.1)                                 # Einschwingen: ausgelassen
        self.assertAlmostEqual(welt.werte["steer_gain"], 0.5 * ab.SCHRITT_HOCH, places=3)
        self.assertIn("eingeschwungen", tuner.hinweis)
        fenster(tuner, offset, start=t + 0.1)
        self.assertEqual(welt.werte["steer_gain"], 0.5)                           # zurück
        self.assertIn("keine Frage der Reglerstärke", tuner.hinweis)
        # ... und danach wird eine Weile nicht noch einmal hochgedreht.
        fenster(tuner, offset, start=100.0)
        self.assertEqual(welt.werte["steer_gain"], 0.5)

    def test_sluggish_keeps_the_higher_gain_if_it_helped(self):
        welt, tuner = self.neu("automatisch", gain=0.5)
        t = fenster(tuner, lambda s: 0.15)
        t = fenster(tuner, lambda s: 0.12, start=t + 0.1)                         # noch im Einschwingen
        fenster(tuner, lambda s: 0.08, start=t + 0.1)
        self.assertGreaterEqual(welt.werte["steer_gain"], 0.5 * ab.SCHRITT_HOCH - 1e-6)
        self.assertNotIn("keine Frage", tuner.hinweis)

    def test_the_gain_never_leaves_its_limits_and_steps_are_capped(self):
        welt, tuner = self.neu("automatisch", gain=ab.GAIN_MIN * 1.01)
        t = 0.0
        for _ in range(40):
            t = fenster(tuner, schwingen, start=t + 5.0)
        self.assertGreaterEqual(welt.werte["steer_gain"], ab.GAIN_MIN)
        self.assertLessEqual(welt.werte["actuator_latency_ms"], ab.LATENZ_MAX_MS)
        self.assertLessEqual(len(tuner.schritte), ab.MAX_SCHRITTE)

    def test_reset_restores_the_start_values(self):
        welt, tuner = self.neu("automatisch", gain=0.9)
        fenster(tuner, schwingen)
        self.assertNotEqual(welt.werte["steer_gain"], 0.9)
        tuner.zuruecksetzen()
        self.assertEqual(welt.werte, {"steer_gain": 0.9, "actuator_latency_ms": 0})

    def test_status_is_readable(self):
        welt, tuner = self.neu("automatisch")
        fenster(tuner, schwingen)
        s = tuner.status()
        self.assertEqual(s["modus"], "automatisch")
        self.assertEqual(s["befund"]["art"], "schwingt")
        self.assertEqual(s["ausgangswerte"]["steer_gain"], 0.9)
        self.assertEqual(s["schritte"][0]["was"], "steer_gain")


def fahre(gain, totzeit, tuner=None, welt=None, sekunden=600.0, tempo=3.0, bias_deg=0.0, y0=0.2, dt=0.1):
    """Ein Fahrzeug mit Fahrradmodell, Stanley-Regler, Ratenbegrenzung und Totzeit der Lenkung."""
    profil = VehicleProfile()
    linie = GuidanceLine("ab", [(0, 0), (0, 100)], 3.0)
    y, psi, delta = y0, 0.0, 0.0
    puffer = deque([0.0] * int(totzeit / dt))
    rng = random.Random(1)
    t, spur = 0.0, []
    while t < sekunden:
        profil.steer_gain = welt.werte["steer_gain"] if welt else gain
        gemessen = y + rng.gauss(0, 0.01)
        puffer.append(linie._steer_angle(gemessen, -psi, tempo, profil))
        delta += max(-2.5, min(2.5, puffer.popleft() - delta))                  # 25 Grad/s bei 10 Hz
        psi += math.degrees(tempo / 2.6 * math.tan(math.radians(delta + bias_deg))) * dt
        y += tempo * math.sin(math.radians(psi)) * dt
        t += dt
        spur.append(y)
        if tuner:
            tuner.beobachten(t, y, tempo, True, True)
    return spur


def effektiv(spur, n=300):
    letzte = spur[-n:]
    return math.sqrt(sum(v * v for v in letzte) / len(letzte))


class RegelkreisTest(unittest.TestCase):
    """Das Fahrzeug im Kreis: der Assistent muss aus eigener Kraft zur Ruhe kommen."""

    def abstimmen(self, gain, totzeit, **kwargs):
        welt = Welt(gain)
        tuner = ab.Lenkabstimmung(welt.lesen, welt.setzen)
        tuner.einschalten("automatisch")
        spur = fahre(gain, totzeit, tuner, welt, **kwargs)
        return welt, tuner, spur

    def test_without_the_assistant_the_marginal_controller_keeps_swinging(self):
        spur = fahre(0.9, 0.8, y0=0.6, sekunden=200.0)
        self.assertGreater(effektiv(spur), 0.05)

    def test_a_marginal_controller_is_calmed_down(self):
        welt, tuner, spur = self.abstimmen(0.9, 0.8, y0=0.6)
        self.assertLess(welt.werte["steer_gain"], 0.9)
        self.assertLess(effektiv(spur), 0.03)

    def test_a_controller_that_builds_up_is_brought_back(self):
        welt, tuner, spur = self.abstimmen(2.0, 0.5)
        self.assertLess(welt.werte["steer_gain"], 2.0)
        self.assertLess(effektiv(spur), 0.03)
        self.assertLess(max(abs(v) for v in spur), 0.5)

    def test_an_unstable_controller_ends_calm_within_the_step_limit(self):
        welt, tuner, spur = self.abstimmen(1.5, 0.8)
        self.assertLess(effektiv(spur), 0.03)
        self.assertLessEqual(len(tuner.schritte), ab.MAX_SCHRITTE)
        self.assertGreaterEqual(welt.werte["steer_gain"], ab.GAIN_MIN)

    def test_a_good_controller_is_left_alone(self):
        welt, tuner, spur = self.abstimmen(0.9, 0.3)
        self.assertEqual(tuner.schritte, [])
        self.assertEqual(welt.werte["steer_gain"], 0.9)

    def test_a_sluggish_controller_is_made_firmer(self):
        # Eine konstante Lenkabweichung (Mitte um 0,6 Grad falsch) lässt einen schwachen Regler daneben stehen.
        welt, tuner, spur = self.abstimmen(0.15, 0.3, bias_deg=0.6)
        self.assertGreater(welt.werte["steer_gain"], 0.15)
        self.assertLess(effektiv(spur), 0.12)


if __name__ == "__main__":
    unittest.main()
