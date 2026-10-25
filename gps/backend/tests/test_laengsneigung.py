"""Richtung der Längsneigung aus RTK-Höhe und Strecke (laengsneigung.py)."""

import math
import random
import tempfile
import unittest

from agripilot.config import Config
from agripilot.engine import Engine
from agripilot.imu import Attitude, terrain_offset
from agripilot.laengsneigung import Laengsneigung
from agripilot.storage import Storage


def hangfahrt(pruefer, sensor_bug_hoch_positiv, steigung_deg=6.0, strecke_m=600.0, rtk=True):
    """Hin und her über einen Hang, 10 Hz bei 2 m/s; liefert die Rückgaben von update."""
    zufall = random.Random(3)
    rueckgaben = []
    x, richtung = 0.0, 1.0
    for _ in range(int(strecke_m / 0.2)):
        x += 0.2 * richtung
        if x > 100 or x < 0:
            richtung = -richtung                                 # Wende: jetzt bergab
            pruefer.update((x, 0.0), None, None, 0.0, rtk)      # beim Wenden steht er kurz
        hoehe = 500.0 + math.tan(math.radians(steigung_deg)) * x + zufall.gauss(0, 0.01)
        bug_hoch = steigung_deg * richtung                       # bergauf Bug oben
        pitch = bug_hoch if sensor_bug_hoch_positiv else -bug_hoch
        rueckgaben.append(pruefer.update((x, 0.0), hoehe, pitch + zufall.gauss(0, 0.3), 2.0, rtk))
    return [r for r in rueckgaben if r is not None]


class Richtung(unittest.TestCase):
    def test_a_sensor_counting_nose_down_positive_is_turned_round(self):
        p = Laengsneigung()
        self.assertEqual(hangfahrt(p, sensor_bug_hoch_positiv=False), [-1.0])
        self.assertEqual(p.vorzeichen, -1.0)
        self.assertTrue(p.bestaetigt)
        self.assertIn("umgedreht", p.meldung)

    def test_the_expected_direction_is_confirmed_and_left_alone(self):
        p = Laengsneigung()
        self.assertEqual(hangfahrt(p, sensor_bug_hoch_positiv=True), [])
        self.assertEqual(p.vorzeichen, 1.0)
        self.assertTrue(p.bestaetigt)

    def test_flat_ground_or_no_rtk_decides_nothing(self):
        for steigung, rtk in ((0.5, True), (6.0, False)):
            p = Laengsneigung()
            self.assertEqual(hangfahrt(p, True, steigung_deg=steigung, rtk=rtk), [])
            self.assertFalse(p.bestaetigt)

    def test_the_sign_turns_the_along_track_offset_round(self):
        lage = Attitude(roll_deg=0.0, pitch_deg=5.0)
        _, vorn = terrain_offset(lage, 3.0)
        _, umgedreht = terrain_offset(lage, 3.0, pitch_sign=-1.0)
        self.assertAlmostEqual(vorn, -umgedreht)


class Gemerkt(unittest.TestCase):
    def test_a_learned_pitch_sign_survives_the_restart(self):
        with tempfile.TemporaryDirectory() as ordner:
            store = Storage(ordner + "/x.db")
            try:
                self.assertEqual(Engine(Config(), store).laengsneigung.vorzeichen, 1.0)
                store.set_setting("imu_pitch_vorzeichen", -1.0)
                self.assertEqual(Engine(Config(), store).laengsneigung.vorzeichen, -1.0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()


class Physik(unittest.TestCase):
    def test_uphill_the_ground_point_lies_ahead_of_the_tilted_antenna(self):
        # Bergauf, Nase 5° oben, Antenne 3 m hoch, Fahrt nach Norden: die Antenne neigt
        # nach hinten, der Punkt am Boden liegt 3 * sin 5° = 26 cm weiter vorn.
        with tempfile.TemporaryDirectory() as ordner:
            store = Storage(ordner + "/x.db")
            try:
                e = Engine(Config(), store)
                e.profile.antenna_height_m = 3.0
                lage = Attitude(pitch_deg=5.0)
                lage.received_at = __import__("time").time()
                boden = e._compensate_terrain((0.0, 0.0), 0.0, lage)
                self.assertAlmostEqual(boden[0], 0.0, places=6)
                self.assertAlmostEqual(boden[1], 3.0 * math.sin(math.radians(5.0)), places=6)
            finally:
                store.close()
