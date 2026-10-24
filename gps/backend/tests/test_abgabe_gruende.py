"""Warum die Lenkung abgegeben hat - je Arbeit gezählt (ab 1.5.81)."""

import time
import unittest

from agripilot import nmea
from agripilot.actuators import NullOutput
from agripilot.config import SteeringConfig
from agripilot.export import _genauigkeit_spalten
from agripilot.genauigkeit import Genauigkeit
from agripilot.guidance import GuidanceState
from agripilot.steering import SteeringController, grund_kurz


def fix(speed=3.0, wann=None, quality=4):
    return nmea.Fix(lat=48.0, lon=11.0, fix_quality=quality, speed_ms=speed,
                    received_at=wann if wann is not None else time.time())


def spur(quer=0.05):
    return GuidanceState(active=True, cross_track_m=quer, steer_angle_deg=-1.0)


class Gruende(unittest.TestCase):
    def setUp(self):
        out = NullOutput()
        out.ready = True
        self.lenkung = SteeringController(SteeringConfig(enabled=True, output="none"), out)
        self.lenkung.arm()

    def lenkt(self, jetzt):
        self.assertTrue(self.lenkung.update(spur(), fix(wann=jetzt), now=jetzt).engaged)

    def test_reasons_are_counted_without_their_numbers_and_wanted_stops_not_at_all(self):
        jetzt = time.time()
        self.lenkt(jetzt)
        self.lenkung.update(spur(quer=2.0), fix(wann=jetzt + 0.1), now=jetzt + 0.1)   # zu weit
        self.lenkt(jetzt + 0.2)
        self.lenkung.update(spur(quer=1.8), fix(wann=jetzt + 0.3), now=jetzt + 0.3)   # zu weit, andere Zahl
        self.lenkt(jetzt + 0.4)
        self.lenkung.pausieren("GNSS-Ausreißer im Positionsfilter")
        self.lenkt(jetzt + 0.5)
        self.lenkung.update(spur(), fix(speed=0.0, wann=jetzt + 0.6), now=jetzt + 0.6)  # zu langsam: gewollt
        self.assertEqual(self.lenkung.abgabe_gruende,
                         {"zu weit von der Spur": 2, "GNSS-Ausreißer im Positionsfilter": 1})
        self.assertEqual(self.lenkung.abgaben, 3)

    def test_per_job_only_what_happened_since_its_start(self):
        self.lenkung.abgabe_gruende = {"zu weit von der Spur": 4}
        g = Genauigkeit()
        g.zuruecksetzen(4, self.lenkung.abgabe_gruende)
        g.messen(0.02, True, True, 0.1)
        self.lenkung.abgabe_gruende = {"zu weit von der Spur": 5, "keine GPS-Daten": 2}
        ergebnis = g.ergebnis(7, self.lenkung.abgabe_gruende)
        self.assertEqual(ergebnis["abgabe_gruende"], {"keine GPS-Daten": 2, "zu weit von der Spur": 1})
        self.assertEqual(_genauigkeit_spalten(ergebnis)[-1], "2x keine GPS-Daten, 1x zu weit von der Spur")

    def test_short_reason(self):
        self.assertEqual(grund_kurz("RTK nötig, aktuell: RTK float"), "RTK nötig")
        self.assertEqual(grund_kurz("GPS-Daten veraltet (1.3 s)"), "GPS-Daten veraltet")


if __name__ == "__main__":
    unittest.main()
