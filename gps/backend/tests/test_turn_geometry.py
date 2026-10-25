"""Wendegeometrie: fahrbar, richtige Nachbarspur, Zielpunkt vor der Maschine.

Die Fälle stammen aus dem Dauerlauf (scripts/dauerlauf.py): die frühere Ω-Wende
aus einer Bézierkurve hatte Bögen unter einem Meter Radius und wurde in jedem
Lauf verlassen; auf der langen Geraden einer U-Wende lag der Zielpunkt hinter
der Maschine.
"""

import math
import os
import tempfile
import time
import unittest

from agripilot import config as config_module
from agripilot import geo, headland, nmea
from agripilot.engine import Engine
from agripilot.storage import Storage


class OmegaGeometryTests(unittest.TestCase):
    def _omega(self, versatz, radius=4.2, links=True):
        return headland.plan_omega((100.0, 100.0), 0.0, versatz, radius, 12.0, links)

    def test_no_bend_is_tighter_than_the_turning_circle(self):
        for versatz in (3.0, 4.0, 6.0, 8.0):
            pfad = self._omega(versatz)
            self.assertGreaterEqual(headland.engster_radius(pfad), 4.2 * 0.97,
                                    f"Versatz {versatz}")

    def test_it_ends_on_the_neighbouring_pass_heading_back(self):
        for versatz in (3.0, 4.0, 6.0, 8.0):
            for links in (True, False):
                pfad = self._omega(versatz, links=links)
                seite = -1.0 if links else 1.0
                self.assertAlmostEqual(pfad[-1][0], 100.0 + seite * versatz, delta=0.5,
                                       msg=f"Versatz {versatz} links={links}")
                kurs = geo.heading_deg(pfad[-3], pfad[-1])
                self.assertAlmostEqual(abs(geo.angle_difference(kurs, 180.0)), 0.0, delta=6.0)

    def test_it_starts_straight_ahead(self):
        pfad = self._omega(6.0)
        self.assertAlmostEqual(geo.heading_deg(pfad[0], pfad[2]), 0.0, delta=3.0)

    def test_a_wide_offset_is_a_u_turn(self):
        pfad = headland.plan_omega((0.0, 0.0), 0.0, 12.0, 4.0, 14.0, True)
        u = headland.plan_u_turn((0.0, 0.0), 0.0, 12.0, 4.0, True)
        self.assertEqual(pfad, u)

    def test_engster_radius_of_a_circle_and_a_line(self):
        kreis = [(5.0 * math.cos(t / 20.0), 5.0 * math.sin(t / 20.0)) for t in range(0, 120)]
        self.assertAlmostEqual(headland.engster_radius(kreis), 5.0, delta=0.2)
        gerade = [(0.0, float(y)) for y in range(30)]
        self.assertTrue(math.isinf(headland.engster_radius(gerade)))


class FollowerTargetTests(unittest.TestCase):
    def test_the_target_lies_ahead_of_the_machine_on_a_long_straight(self):
        """Gemessen wurde ab dem Teilstück-Anfang: auf einer 9-m-Geraden lag der
        Zielpunkt hinter der Maschine, und sie lenkte voll herum."""
        folger = headland.TurnFollower(pfad=[(0.0, 0.0), (0.0, 9.0), (0.0, 18.0)])
        ziel = folger._zielpunkt(0, (0.0, 6.0), 2.5)
        self.assertAlmostEqual(ziel[1], 8.5, places=6)

    def test_the_target_crosses_into_the_next_segment(self):
        folger = headland.TurnFollower(pfad=[(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)])
        ziel = folger._zielpunkt(0, (0.0, 0.5), 1.2)
        self.assertAlmostEqual(ziel[1], 1.7, places=6)

    def test_the_end_of_the_route_is_the_limit(self):
        folger = headland.TurnFollower(pfad=[(0.0, 0.0), (0.0, 1.0)])
        self.assertEqual(folger._zielpunkt(0, (0.0, 0.5), 9.0), (0.0, 1.0))


class DrivabilityInEngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Storage(os.path.join(self.dir.name, "t.db"))
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(self.store.close)
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.network.device_id = "pi-test"
        self.engine = Engine(config, self.store)
        feld = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 11.0,
                                      "boundary": [[0, 0], [300, 0], [300, 400], [0, 400]]})
        self.engine.load_field(feld["id"])
        self.engine.update_profile({"width_m": 6.0, "antenna_forward_m": 0.0,
                                    "tool_trailing_m": 0.0, "wheelbase_m": 2.6,
                                    "max_steer_deg": 35.0})
        plane = geo.LocalPlane(48.0, 11.0)
        lat, lon = plane.to_wgs(150.0, 380.0)
        fix = nmea.Fix(lat=lat, lon=lon, fix_quality=4, speed_ms=2.0, course_deg=0.0,
                       altitude=500.0)
        fix.received_at = time.time()
        self.engine.on_fix(fix)

    def test_a_turning_circle_below_the_machine_limit_is_refused(self):
        self.engine.update_headland({"aktiv": True, "muster": "omega", "richtung": "links",
                                     "radius_m": 2.0, "ueberspringen": 1})
        plan = self.engine.plan_turn()
        self.assertFalse(plan["fahrbar"])
        self.assertLess(plan["engster_radius_m"], plan["kleinster_radius_m"])
        with self.assertRaises(RuntimeError) as fehler:
            self.engine.start_turn()
        self.assertIn("nicht fahren", str(fehler.exception))

    def test_a_sensible_turning_circle_is_drivable(self):
        self.engine.update_headland({"aktiv": True, "muster": "omega", "richtung": "links",
                                     "radius_m": 4.2, "ueberspringen": 1})
        plan = self.engine.plan_turn()
        self.assertTrue(plan["fahrbar"])
        self.assertAlmostEqual(plan["kleinster_radius_m"], 2.6 / math.tan(math.radians(35.0)),
                               places=1)


if __name__ == "__main__":
    unittest.main()
