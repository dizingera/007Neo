"""Träger Lenkmotor: Nachlauf messen, Regelart und Trägheit danach stellen, Ausgleich pausieren."""

import math
import random
import unittest
from collections import deque

from agripilot import abstimmung as ab
from agripilot import lenkung_einrichten as le
from agripilot.gnss import SimulatorSource
from agripilot.guidance import GuidanceLine, VehicleProfile
from agripilot.nachlauf import Nachlauf, schaetzen
from agripilot.spurintegral import SpurIntegral


def motor(befehle, dt=0.1, totzeit=0.5, traegheit=0.8, rate=8.0):
    """Was ein träger Lenkmotor aus einer Befehlsfolge macht."""
    warte = deque([0.0] * max(1, int(round(totzeit / dt))))
    rad, raeder = 0.0, []
    for b in befehle:
        warte.append(b)
        schritt = (warte.popleft() - rad) * min(1.0, dt / traegheit)
        rad += max(-rate * dt, min(rate * dt, schritt))
        raeder.append(rad)
    return raeder


def fahrt(sekunden=300.0, totzeit=0.6, traegheit=1.0, rate=6.0, modus="automatisch", gain=0.6,
          regelart="stanley", lenk_traegheit_s=0.3, integral=2.0, start_quer=0.4):
    """Andys Lage: Stanley, Lenkschärfe 0,6, Ausgleich 2, ein Motor mit rund 1,6 s Nachlauf."""
    random.seed(11)
    linie = GuidanceLine("ab", [(0, 0), (0, 50000)], 3.0)
    profil = {"steer_gain": gain, "actuator_latency_ms": 0, "regelart": regelart,
              "lenk_traegheit_s": lenk_traegheit_s}
    nachlauf = Nachlauf()
    tuner = ab.Lenkabstimmung(lambda: dict(profil), profil.update, nachlauf=nachlauf.sicher_s)
    tuner.einschalten(modus)
    integ = SpurIntegral()
    dt, tempo = 0.1, 2.7
    x, y, h, rad = start_quer, 0.0, 0.0, 0.0
    warte = deque([0.0] * max(1, int(round(totzeit / dt))))
    quer = []
    for i in range(int(sekunden / dt)):
        t = i * dt
        p = VehicleProfile(steer_gain=profil["steer_gain"], regelart=profil["regelart"], wheelbase_m=2.6,
                           lenk_traegheit_s=profil["lenk_traegheit_s"], steer_softening=1.2)
        g = linie.solve((x + random.gauss(0, 0.015), y), h, tempo, p)
        befehl = p.einschlag_begrenzen(g.steer_angle_deg
                                       + integ.update(g.cross_track_m, dt, tempo, True, True, integral, "hin"))
        warte.append(befehl)
        schritt = (warte.popleft() - rad) * min(1.0, dt / traegheit)
        rad += max(-rate * dt, min(rate * dt, schritt))
        nachlauf.probe(t, befehl, rad + 0.4, True)          # Drehgeber mit verstellter Mitte
        tuner.beobachten(t, g.cross_track_m, tempo, True, True)
        hr = math.radians(h)
        x += tempo * math.sin(hr) * dt
        y += tempo * math.cos(hr) * dt
        h = (h + math.degrees(tempo / 2.6 * math.tan(math.radians(rad))) * dt + 180) % 360 - 180
        quer.append(x)
    return profil, tuner, quer


def effektiv(werte):
    return math.sqrt(sum(v * v for v in werte) / len(werte))


class NachlaufMessen(unittest.TestCase):
    def test_the_lag_of_a_slow_motor_is_found(self):
        random.seed(2)
        befehle, b = [], 0.0
        for i in range(300):
            if i % 40 == 0:
                b = random.uniform(-6, 6)
            befehle.append(b)
        for totzeit, traegheit in ((0.1, 0.3), (0.3, 0.5), (0.5, 0.8)):
            raeder = motor(befehle, totzeit=totzeit, traegheit=traegheit, rate=30.0)
            e = schaetzen([(i * 0.1, b, r - 1.5) for i, (b, r) in enumerate(zip(befehle, raeder))])
            self.assertTrue(e["sicher"], e)
            self.assertAlmostEqual(e["nachlauf_s"], totzeit + traegheit, delta=0.2)

    def test_no_result_without_movement_or_wheel_angle(self):
        n = Nachlauf()
        for i in range(300):
            n.probe(i * 0.1, 0.2, 0.2, True)                  # ruhig geradeaus: nichts zu messen
        self.assertIsNone(n.sicher_s())
        n = Nachlauf()
        for i in range(300):
            n.probe(i * 0.1, 5.0 * math.sin(i / 10), None, True)   # kein Drehgeber
            n.probe(i * 0.1, 5.0 * math.sin(i / 10), 1.0, False)   # Lenkung aus
        self.assertEqual(len(n.proben), 0)
        self.assertIsNone(n.sicher_s())


class AssistentBeiTraegemMotor(unittest.TestCase):
    def test_without_it_stanley_swings(self):
        _, _, quer = fahrt(modus="aus")
        self.assertGreater(effektiv(quer[-600:]), 0.10)

    def test_the_assistant_switches_to_predictive_with_the_measured_lag(self):
        profil, tuner, quer = fahrt()
        self.assertEqual(profil["regelart"], "voraus")
        self.assertAlmostEqual(profil["lenk_traegheit_s"], 1.6, delta=0.4)
        self.assertLess(effektiv(quer[-600:]), 0.02)
        self.assertTrue(any(s.was == "regelart" for s in tuner.schritte))
        tuner.zuruecksetzen()
        self.assertEqual(profil["regelart"], "stanley")       # der Weg zurück bleibt

    def test_old_saved_starting_values_still_bring_the_control_mode_back(self):
        profil = {"steer_gain": 0.6, "actuator_latency_ms": 0, "regelart": "stanley", "lenk_traegheit_s": 0.3}
        tuner = ab.Lenkabstimmung(lambda: dict(profil), profil.update, nachlauf=lambda: 1.4)
        tuner.modus = "automatisch"                      # so stellt die Engine ihn nach einem Neustart her
        tuner.ausgangswerte = {"steer_gain": 0.9, "actuator_latency_ms": 0}
        t = 0.0
        while t < 12.0:
            tuner.beobachten(t, 0.03 * (1.6 ** t) * math.sin(2 * math.pi * t / 4.0), 3.0, True, True)
            t += 0.1
        self.assertEqual(profil["regelart"], "voraus")
        tuner.zuruecksetzen()
        self.assertEqual(profil, {"steer_gain": 0.9, "actuator_latency_ms": 0, "regelart": "stanley",
                                  "lenk_traegheit_s": 0.3})

    def test_suggestions_only_change_nothing(self):
        profil, tuner, _ = fahrt(modus="beobachten", sekunden=150.0)
        self.assertEqual(profil["regelart"], "stanley")
        self.assertTrue(any(s.was == "regelart" and s.nach == "voraus" for s in tuner.schritte))

    def test_a_wrong_inertia_is_corrected_and_a_fitting_one_kept(self):
        # Gemessen wird beim wilden Pendeln, wo der Motor an seiner Grenze läuft: der
        # Wert liegt dann über dem ruhigen Nachlauf (1,6 s) - aber im stabilen Bereich.
        profil, tuner, quer = fahrt(regelart="voraus", lenk_traegheit_s=0.3)
        self.assertGreaterEqual(profil["lenk_traegheit_s"], 1.0)
        self.assertLessEqual(profil["lenk_traegheit_s"], ab.TRAEGHEIT_MAX_S)
        self.assertLess(effektiv(quer[-600:]), 0.02)
        profil, tuner, _ = fahrt(regelart="voraus", lenk_traegheit_s=1.5, sekunden=150.0)
        self.assertEqual(profil["lenk_traegheit_s"], 1.5)
        self.assertFalse(any(s.was == "lenk_traegheit_s" for s in tuner.schritte))

    def test_a_quick_motor_keeps_stanley(self):
        profil, tuner, _ = fahrt(totzeit=0.1, traegheit=0.2, rate=30.0, sekunden=150.0)
        self.assertEqual(profil["regelart"], "stanley")


class AusgleichBeimPendeln(unittest.TestCase):
    def test_it_does_not_add_up_while_swinging(self):
        i = SpurIntegral()
        for k in range(600):
            i.update(0.08 * math.sin(2 * math.pi * k * 0.1 / 6.0) + 0.03, 0.1, 3.0, True, True, 2.0, "hin")
        self.assertTrue(i.pendelt)
        self.assertLess(abs(i.wert_deg), 0.3)

    def test_a_steady_offset_still_counts(self):
        i = SpurIntegral()
        for _ in range(300):
            i.update(0.05, 0.1, 3.0, True, True, 2.0, "hin")
        self.assertFalse(i.pendelt)
        self.assertLess(i.wert_deg, -1.0)


class Lenkwerkstatt(unittest.TestCase):
    def test_fine_tuning_is_open_once_the_direction_is_right(self):
        bereit = {"typ": "phidget", "bereit": True}
        schritte = {s["id"]: s for s in le.status({"richtung": {"invert_motor": False}}, bereit, False, False, {})}
        for kennung in ("mindestleistung", "zaehlwerte", "feinabstimmung"):
            self.assertTrue(schritte[kennung]["moeglich"], kennung)
        self.assertFalse(schritte["freigabe"]["moeglich"])
        schritte = {s["id"]: s for s in le.status({}, bereit, False, False, {})}
        self.assertFalse(schritte["feinabstimmung"]["moeglich"])


class SimulatorTotzeit(unittest.TestCase):
    def test_the_wheel_waits_for_the_dead_time(self):
        sim = SimulatorSource(lambda fix: None)
        sim.speed_ms = 0.0
        sim.lenk_totzeit_s = 0.5
        sim.set_steer(10.0)
        for _ in range(4):
            sim._step(0.1)
        self.assertAlmostEqual(sim.rad_deg, 0.0)
        for _ in range(4):
            sim._step(0.1)
        self.assertGreater(sim.rad_deg, 0.5)


if __name__ == "__main__":
    unittest.main()
