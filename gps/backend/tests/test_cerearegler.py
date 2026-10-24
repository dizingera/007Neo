"""Regelart "cerea": Punkt voraus auf der Spur mit D, I und I2 (cerearegler.py)."""

import math
import random
import tempfile
import unittest
import warnings
from collections import deque

from agripilot import cerearegler
from agripilot import config as config_module
from agripilot.cerearegler import CereaIntegral
from agripilot.guidance import TRAKTOR_FELDER, GuidanceLine, VehicleProfile
from agripilot.spurintegral import SpurIntegral


def profil(**werte):
    return VehicleProfile(regelart="cerea", wheelbase_m=2.6, **werte)


def fahrt(regelart="cerea", tempo=2.0, totzeit=0.6, traegheit=1.0, rate=6.0, sekunden=240.0,
          start_quer=0.4, hang_deg=0.0, **werte):
    """Ein Traktor mit trägem Lenkmotor auf einer AB-Spur; Abweichung der letzten Minute."""
    random.seed(11)
    linie = GuidanceLine("ab", [(0, 0), (0, 50000)], 3.0)
    p = VehicleProfile(regelart=regelart, wheelbase_m=2.6, steer_gain=0.9, steer_softening=1.2, **werte)
    stanley_i, cerea_i = SpurIntegral(), CereaIntegral()
    dt = 0.1
    x, y, h, rad = start_quer, 0.0, 0.0, 0.0
    warte = deque([0.0] * max(1, int(round(totzeit / dt))))
    quer = []
    for _ in range(int(sekunden / dt)):
        g = linie.solve((x + random.gauss(0, 0.015), y), h, tempo, p)
        if regelart == "cerea":
            zusatz = cerea_i.update(g.cross_track_m, dt, tempo, True, True, p, "hin")
        else:
            zusatz = stanley_i.update(g.cross_track_m, dt, tempo, True, True, 2.0, "hin")
        warte.append(p.einschlag_begrenzen(g.steer_angle_deg + zusatz))
        rad += max(-rate * dt, min(rate * dt, (warte.popleft() - rad) * min(1.0, dt / traegheit)))
        hr = math.radians(h)
        x += tempo * math.sin(hr) * dt
        y += tempo * math.cos(hr) * dt
        h = (h + math.degrees(tempo / 2.6 * math.tan(math.radians(rad + hang_deg))) * dt + 180) % 360 - 180
        quer.append(x)
    rest = quer[-600:]
    return max(abs(v) for v in rest), sum(rest) / len(rest)


class PunktVoraus(unittest.TestCase):
    def test_on_the_line_it_steers_straight_and_off_it_back_towards_it(self):
        p = profil()
        self.assertAlmostEqual(cerearegler.einschlag(0.0, 0.0, 2.0, p), 0.0)
        self.assertLess(cerearegler.einschlag(0.3, 0.0, 2.0, p), 0.0)     # rechts daneben -> links
        self.assertGreater(cerearegler.einschlag(-0.3, 0.0, 2.0, p), 0.0)
        # Schon schräg auf die Spur zu: weniger einlenken als parallel daneben.
        self.assertGreater(cerearegler.einschlag(0.3, 5.0, 2.0, p), cerearegler.einschlag(0.3, 0.0, 2.0, p))

    def test_the_point_moves_away_with_speed_and_distance_like_the_cerea_sliders(self):
        p = profil()
        nah = cerearegler.zielabstand(0.0, 1.0, p)
        self.assertGreater(cerearegler.zielabstand(0.0, 3.0, p), nah)          # schneller
        self.assertGreater(cerearegler.zielabstand(0.5, 1.0, p), nah)          # weiter daneben
        self.assertGreater(cerearegler.zielabstand(0.0, 3.0, profil(cerea_lenk_aggr=50)),
                           cerearegler.zielabstand(0.0, 3.0, profil(cerea_lenk_aggr=200)))
        self.assertGreater(cerearegler.zielabstand(0.5, 1.0, profil(cerea_auffahr_aggr=50)),
                           cerearegler.zielabstand(0.5, 1.0, profil(cerea_auffahr_aggr=200)))
        self.assertAlmostEqual(cerearegler.zielabstand(0.0, 0.0, profil(cerea_l=0.5))
                               - cerearegler.zielabstand(0.0, 0.0, profil(cerea_l=0.1)), 0.4)

    def test_nonsense_values_are_clamped(self):
        p = profil(cerea_lenk_aggr=0, cerea_d=float("nan"), cerea_i=-3)
        self.assertTrue(math.isfinite(cerearegler.einschlag(0.3, 2.0, 3.0, p)))
        self.assertEqual(cerearegler.wert(p, "cerea_lenk_aggr"), 10.0)
        self.assertEqual(cerearegler.wert(p, "cerea_i"), 0.0)

    def test_d_brakes_the_approach(self):
        ohne, mit = profil(cerea_d=0.0), profil(cerea_d=0.45)
        # Rechts daneben und schon schnell nach links unterwegs: D lenkt früher zurück.
        self.assertGreater(cerearegler.einschlag(0.2, 8.0, 2.5, mit), cerearegler.einschlag(0.2, 8.0, 2.5, ohne))


class TraegerMotor(unittest.TestCase):
    """Der Lenkmotor aus test_traeger_motor (rund 1,6 s Nachlauf, 6 °/s)."""

    def test_where_stanley_swings_the_cerea_point_stays_on_the_line(self):
        stanley, _ = fahrt("stanley", tempo=2.0)
        cerea, mittel = fahrt("cerea", tempo=2.0)
        self.assertGreater(stanley, 0.3)
        self.assertLess(cerea, 0.03)
        self.assertLess(abs(mittel), 0.01)

    def test_faster_needs_less_aggressiveness_exactly_like_in_cerea(self):
        zu_scharf, _ = fahrt(tempo=12 / 3.6)
        ruhiger, _ = fahrt(tempo=12 / 3.6, cerea_lenk_aggr=60)
        self.assertGreater(zu_scharf, 0.3)
        self.assertLess(ruhiger, 0.03)

    def test_i_and_i2_take_out_a_slope(self):
        ohne, mittel_ohne = fahrt(tempo=2.0, hang_deg=1.5, cerea_i=0.0, cerea_i2=0.0)
        mit, mittel_mit = fahrt(tempo=2.0, hang_deg=1.5)
        self.assertGreater(abs(mittel_ohne), 0.05)
        self.assertLess(abs(mittel_mit), 0.01)


class Integral(unittest.TestCase):
    def test_only_while_steering_and_per_direction(self):
        p, i = profil(), CereaIntegral()
        self.assertEqual(i.update(0.1, 0.1, 2.0, False, True, p, "a:hin"), 0.0)
        for _ in range(100):
            i.update(0.1, 0.1, 2.0, True, True, p, "a:hin")
        hin = i.summe
        self.assertLess(hin, 0.0)                      # rechts daneben -> nach links gegenlenken
        i.update(0.0, 0.1, 2.0, True, True, p, "a:her")
        self.assertAlmostEqual(i.summe, 0.0)
        i.update(0.0, 0.1, 2.0, True, True, p, "a:hin")
        self.assertAlmostEqual(i.summe, hin, places=1)
        for _ in range(1000):
            i.update(0.2, 0.1, 2.0, True, True, p, "a:hin")
        self.assertGreaterEqual(i.summe, -2 * cerearegler.GRENZE_DEG)


class AmTraktor(unittest.TestCase):
    def test_the_values_belong_to_the_tractor_and_show_up_live(self):
        for feld in ("cerea_lenk_aggr", "cerea_auffahr_aggr", "cerea_l", "cerea_i", "cerea_i2", "cerea_d"):
            self.assertIn(feld, TRAKTOR_FELDER)
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")
        with tempfile.TemporaryDirectory() as ordner:
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = ordner
            with TestClient(create_app(config)) as c:
                antwort = c.post("/api/traktor", json={"regelart": "cerea", "cerea_lenk_aggr": 80,
                                                       "cerea_d": 0.3})
                self.assertEqual(antwort.status_code, 200)
                traktor = next(t for t in c.get("/api/traktoren").json() if t["aktiv"])
                self.assertEqual(traktor["regelart"], "cerea")
                self.assertEqual(traktor["cerea_lenk_aggr"], 80)
                self.assertEqual(traktor["cerea_i2"], 0.06)
                zustand = c.get("/api/state").json()
                self.assertEqual(zustand["profile"]["cerea_d"], 0.3)


if __name__ == "__main__":
    unittest.main()


class LenkAssistent(unittest.TestCase):
    def test_with_cerea_it_turns_the_aggressiveness_down_and_never_switches_the_controller(self):
        from agripilot import abstimmung as ab
        werte = {"steer_gain": 0.9, "actuator_latency_ms": 0, "regelart": "cerea",
                 "lenk_traegheit_s": 0.3, "cerea_lenk_aggr": 100.0}
        tuner = ab.Lenkabstimmung(lambda: dict(werte), werte.update, nachlauf=lambda: 1.6)
        tuner.einschalten("automatisch")
        tuner.zeiten = [0.0, 30.0]
        pendeln = ab.Befund("schwingt", 0.25, 0.0, 0.3, 8, 6.0, False, False)
        tuner._auswerten(pendeln)
        self.assertEqual(werte["regelart"], "cerea")
        self.assertEqual(werte["cerea_lenk_aggr"], 85)
        self.assertEqual(werte["steer_gain"], 0.9)
        self.assertEqual(tuner.status()["cerea_lenk_aggr"], 85)


class AbDerAntenne(unittest.TestCase):
    def test_l_counts_from_an_antenna_on_the_bonnet_but_never_closer_than_a_wheelbase(self):
        kabine = profil(antenna_forward_m=1.2, cerea_l=0.1)
        haube = profil(antenna_forward_m=3.4, cerea_l=0.1)
        self.assertAlmostEqual(cerearegler.zielabstand(0.0, 0.0, kabine), 2.6 + 0.1)
        self.assertAlmostEqual(cerearegler.zielabstand(0.0, 0.0, haube), 3.4 + 0.1)
