"""Einschlag bei Tempo begrenzen (Querbeschleunigung)."""

import math
import time
import unittest

from agripilot.actuators import SimulatorOutput, SteerContext
from agripilot.config import SteeringConfig
from agripilot.guidance import GuidanceState
from agripilot.nmea import Fix
from agripilot.steering import SteeringController, tempo_grenze_deg


class TempoGrenze(unittest.TestCase):
    def test_numbers(self):
        # 15 km/h, Radstand 2,7 m, 1,5 m/s²: rund 13°
        self.assertAlmostEqual(tempo_grenze_deg(15 / 3.6, 2.7, 1.5), 13.1, delta=0.2)
        # Wendetempo 6 km/h: über 40° - der Anschlag (35°) begrenzt zuerst
        self.assertGreater(tempo_grenze_deg(6 / 3.6, 2.7, 1.5), 40)
        self.assertIsNone(tempo_grenze_deg(4.0, 2.7, 0.0))           # aus
        self.assertIsNone(tempo_grenze_deg(0.2, 2.7, 1.5))           # Schritttempo

    def test_lateral_acceleration_is_kept(self):
        for kmh in (8, 12, 18, 25):
            v = kmh / 3.6
            d = tempo_grenze_deg(v, 2.7, 1.5)
            self.assertAlmostEqual(v * v * math.tan(math.radians(d)) / 2.7, 1.5, places=6)

    def _lenkung(self, a_max=1.5):
        cfg = SteeringConfig(enabled=True, output="none", max_querbeschleunigung_ms2=a_max,
                             max_speed_ms=8.0)
        ausgang = SimulatorOutput()
        ausgang.ready = True
        lenkung = SteeringController(cfg, ausgang, simulator=True)
        lenkung.armed = True
        lenkung.max_rate_deg_s = 1000.0
        return lenkung

    def _befehl(self, lenkung, tempo, winkel):
        g = GuidanceState(active=True, steer_angle_deg=winkel, cross_track_m=0.0)
        fix = Fix(lat=48.4, lon=9.6, fix_quality=4, speed_ms=tempo, received_at=time.time())
        return lenkung.update(g, fix, SteerContext(wheelbase_m=2.7))

    def test_fast_command_is_capped_slow_is_not(self):
        lenkung = self._lenkung()
        schnell = self._befehl(lenkung, 15 / 3.6, 30.0)
        self.assertAlmostEqual(schnell.angle_deg, 13.1, delta=0.2)
        self.assertTrue(lenkung.status()["tempo_begrenzt"])
        lenkung = self._lenkung()                  # frisch: ohne Lenkgeschwindigkeits-Grenze vom Sprung davor
        langsam = self._befehl(lenkung, 6 / 3.6, -30.0)
        self.assertAlmostEqual(langsam.angle_deg, -30.0, places=6)
        self.assertFalse(lenkung.status()["tempo_begrenzt"])

    def test_switched_off_leaves_everything(self):
        lenkung = self._lenkung(a_max=0.0)
        self.assertAlmostEqual(self._befehl(lenkung, 15 / 3.6, 30.0).angle_deg, 30.0, places=6)


if __name__ == "__main__":
    unittest.main()
