"""Neigungssensor: Kalibrieren wie bei Cerea (M1, 180° wenden, M2) und das gelernte
Vorzeichen der Drehrate über den Neustart."""

import tempfile
import unittest

from agripilot.config import Config
from agripilot.engine import Engine
from agripilot.imu import ImuSource
from agripilot.storage import Storage


class Sensor(ImuSource):
    def messen(self, roll, pitch=0.0):
        self._publish(roll, pitch, None, 0.0)


class M1M2(unittest.TestCase):
    def test_on_a_slope_the_mount_error_is_the_mean_of_both_directions(self):
        s = Sensor()
        montage, boden = 1.5, 3.0
        s.messen(montage + boden, 0.4 + 2.0)
        s.messpunkt_m1()
        s.messen(montage - boden, 0.4 - 2.0)             # dieselbe Stelle, um 180° gedreht
        ergebnis = s.messpunkt_m2()
        self.assertAlmostEqual(ergebnis["roll_offset"], montage)
        self.assertAlmostEqual(ergebnis["pitch_offset"], 0.4)
        self.assertAlmostEqual(ergebnis["boden_quer_deg"], boden)
        s.messen(montage)                                  # danach: eben heißt 0
        self.assertAlmostEqual(s.attitude.roll_deg, 0.0)

    def test_it_also_works_after_an_earlier_zeroing(self):
        s = Sensor()
        s.messen(0.7)
        s.level_here()                                     # früher auf schiefem Boden genullt
        s.messen(2.0 + 0.0)
        s.messpunkt_m1()
        s.messen(2.0 - 0.0)
        self.assertAlmostEqual(s.messpunkt_m2()["roll_offset"], 2.0)

    def test_m2_needs_m1_first(self):
        s = Sensor()
        s.messen(1.0)
        with self.assertRaises(RuntimeError):
            s.messpunkt_m2()


class VorzeichenGemerkt(unittest.TestCase):
    def test_a_learned_yaw_sign_survives_the_restart(self):
        with tempfile.TemporaryDirectory() as ordner:
            store = Storage(ordner + "/x.db")
            try:
                self.assertEqual(Engine(Config(), store).heading_filter.gier_vorzeichen, 1.0)
                store.set_setting("imu_gier_vorzeichen", -1.0)
                self.assertEqual(Engine(Config(), store).heading_filter.gier_vorzeichen, -1.0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
