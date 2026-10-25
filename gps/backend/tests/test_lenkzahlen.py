import math
import time
import unittest
from agripilot.actuators import NullOutput
from agripilot.config import SteeringConfig
from agripilot.guidance import GuidanceState
from agripilot.nmea import Fix
from agripilot.steering import SteeringController


class AufzeichnenderAusgang(NullOutput):
    def command(self, engaged, angle_deg, context):
        self.letzter_befehl = (engaged, angle_deg, context)


class UngueltigeLenkzahlen(unittest.TestCase):
    def test_nonfinite_values_stop_the_output(self):
        for feld in ('steer_angle_deg', 'cross_track_m', 'speed_ms', 'received_at'):
            for wert in (math.nan, math.inf, -math.inf):
                with self.subTest(feld=feld, wert=wert):
                    out = AufzeichnenderAusgang()
                    out.ready = True
                    c = SteeringController(SteeringConfig(enabled=True), out)
                    c.arm()
                    g = GuidanceState(active=True, cross_track_m=0.05, steer_angle_deg=2.0)
                    f = Fix(lat=48.0, lon=11.0, fix_quality=4, speed_ms=2.0, received_at=time.time())
                    self.assertTrue(c.update(g, f).engaged)
                    setattr(g if feld in ('steer_angle_deg', 'cross_track_m') else f, feld, wert)
                    cmd = c.update(g, f)
                    self.assertFalse(cmd.engaged)
                    self.assertEqual(cmd.angle_deg, 0.0)
                    aktiv, winkel, kontext = out.letzter_befehl
                    self.assertFalse(aktiv)
                    self.assertEqual(winkel, 0.0)
                    self.assertTrue(math.isfinite(kontext.speed_ms))
                    self.assertTrue(math.isfinite(kontext.cross_track_m))
                    # Gültige Folgedaten müssen wieder den normalen Betrieb erlauben.
                    ziel = g if feld in ('steer_angle_deg', 'cross_track_m') else f
                    setattr(ziel, feld, time.time() if feld == 'received_at' else 0.05 if feld == 'cross_track_m' else 2.0)
                    self.assertTrue(c.update(g, f).engaged)
