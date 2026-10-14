"""Im Simulator fährt der Traktor zum geladenen Schlag - auch wenn der weit vom
Startort des Simulators liegt (MyTerra-Schläge liegen 145 km von München)."""

import os
import tempfile
import unittest

from agripilot import config as config_module
from agripilot.engine import Engine
from agripilot.geo import LocalPlane
from agripilot.gnss import SimulatorSource
from agripilot.storage import Storage

# Ein Schlag bei Magolsheim, ein Quadrat von 200 m um den Feldbezug.
BEZUG = (48.42, 9.62)
QUADRAT = [(-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)]


class SimulatorFeldTests(unittest.TestCase):
    def setUp(self):
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        self.store = Storage(os.path.join(ordner.name, "s.db"))
        self.addCleanup(self.store.close)
        self.motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), self.store)
        self.simulator = SimulatorSource(lambda fix: None)   # startet in München
        self.motor.simulator = self.simulator
        feld = self.store.save_field({"name": "Brünnele", "datum_lat": BEZUG[0],
                                      "datum_lon": BEZUG[1], "boundary": QUADRAT})
        self.motor.load_field(feld["id"])

    def _simulator_in_feldebene(self):
        lat, lon = self.simulator.plane.to_wgs(self.simulator.east, self.simulator.north)
        return LocalPlane(*BEZUG).to_local(lat, lon)

    def test_the_tractor_lands_in_the_middle_of_a_far_away_field(self):
        ziel = self.motor.startpunkt_wgs()
        self.simulator.versetzen(*ziel)
        ost, nord = self._simulator_in_feldebene()
        self.assertLess(abs(ost), 0.01)
        self.assertLess(abs(nord), 0.01)

    def test_relocating_straightens_the_wheels(self):
        self.simulator.set_steer(25.0)
        self.simulator.versetzen(*self.motor.startpunkt_wgs())
        self.assertEqual(self.simulator.steer_deg, 0.0)

    def test_a_line_far_from_its_field_is_not_used_as_start(self):
        """So eine Spur entsteht, wenn der Simulator an anderem Ort fuhr - der
        Traktor landete dann 146 km neben dem Schlag."""
        from types import SimpleNamespace
        self.motor.line = SimpleNamespace(mode="ab", nudge_m=0.0,
                                          points=[(146876.0, -30320.0), (146876.0, -30000.0)])
        self.assertEqual(self.motor.startpunkt_im_feld(), (0.0, 0.0, 0.0))

    def test_a_line_inside_the_field_is_the_start(self):
        from types import SimpleNamespace
        self.motor.line = SimpleNamespace(mode="ab", nudge_m=0.0, points=[(-50.0, -90.0), (-50.0, 90.0)])
        ost, nord, kurs = self.motor.startpunkt_im_feld()
        self.assertEqual((ost, nord), (-50.0, -90.0))
        self.assertAlmostEqual(kurs, 0.0)

    def test_without_a_field_there_is_no_start_point(self):
        self.motor.field = None
        self.assertIsNone(self.motor.startpunkt_wgs())


class SimulatorServerTests(unittest.TestCase):
    def test_loading_a_field_moves_the_simulated_tractor_there(self):
        import warnings
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.server.data_dir = ordner.name
        config.gnss.source = "simulator"
        with TestClient(create_app(config)) as client:
            # Ein zweiter Zugang auf dieselbe Datenbank legt den Schlag an.
            store = Storage(os.path.join(ordner.name, "agripilot.db"))
            self.addCleanup(store.close)
            feld = store.save_field({"name": "Brünnele", "datum_lat": BEZUG[0],
                                     "datum_lon": BEZUG[1], "boundary": QUADRAT})
            self.assertEqual(client.post(f"/api/fields/{feld['id']}/load").status_code, 200)
            # Die nächste Position kommt mit dem nächsten Takt des Simulators.
            import time
            for _ in range(40):
                ost, nord = client.get("/api/state").json()["position"] or (1e9, 1e9)
                if abs(ost) < 20.0 and abs(nord) < 20.0:
                    break
                time.sleep(0.05)
            # Er fährt schon weiter, aber nur ein paar Meter.
            self.assertLess(abs(ost), 20.0)
            self.assertLess(abs(nord), 20.0)


if __name__ == "__main__":
    unittest.main()


class RichtungTests(unittest.TestCase):
    def _filter(self):
        from agripilot.guidance import HeadingFilter
        kurs = HeadingFilter()
        for _ in range(5):
            kurs.update(0.0, None, 2.5)
        return kurs

    def test_a_teleport_jump_is_taken_for_reversing_without_a_reset(self):
        """So kam es zu dem Traktor, der rückwärts fuhr: drei Kurse 180 Grad daneben."""
        kurs = self._filter()
        for _ in range(3):
            kurs.update(180.0, None, 2.5)
        self.assertTrue(kurs.rueckwaerts)
        kurs.reset()
        kurs.update(180.0, None, 2.5)
        self.assertFalse(kurs.rueckwaerts)
        self.assertEqual(kurs.value, 180.0)

    def test_the_driver_can_turn_the_direction_round(self):
        kurs = self._filter()
        for _ in range(3):
            kurs.update(180.0, None, 2.5)
        self.assertTrue(kurs.rueckwaerts)
        self.assertAlmostEqual(kurs.value, 0.0)
        kurs.umkehren()
        self.assertFalse(kurs.rueckwaerts)
        self.assertAlmostEqual(kurs.value, 180.0)
        # Und es bleibt dabei: der nächste Kurs passt jetzt zur Fahrt.
        kurs.update(180.0, None, 2.5)
        self.assertFalse(kurs.rueckwaerts)
        self.assertAlmostEqual(kurs.value, 180.0, places=3)


class FremdeZellenTests(unittest.TestCase):
    def test_merged_cells_are_reported_apart_from_the_own_swath(self):
        """Die Anzeige malt die eigene Fahrt selbst als Streifen; aus Zellen malt
        sie nur, was ein anderer Traktor beigetragen hat."""
        from agripilot.coverage import CoverageMap, build_sections
        eigen = CoverageMap()
        eigen.add_swath((0.0, 0.0), (0.0, 2.0), 0.0, build_sections(2.0, 1))
        fremd = CoverageMap()
        fremd.add_swath((10.0, 0.0), (10.0, 2.0), 0.0, build_sections(2.0, 1))
        eigen.merge(fremd)
        self.assertEqual(len(eigen.drain_new_cells()), 16)
        self.assertEqual(len(eigen.drain_fremde_zellen()), 16)
        self.assertEqual(eigen.drain_fremde_zellen(), [])


class SimulatorLenkungTests(unittest.TestCase):
    def test_the_simulator_steers_without_a_motor_being_enabled(self):
        import warnings
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.server.data_dir = ordner.name
        config.gnss.source = "simulator"
        config.steering.enabled = False          # kein Motor freigegeben
        with TestClient(create_app(config)) as client:
            client.post("/api/steering/arm")
            lenkung = client.get("/api/state").json()["steering"]
            self.assertTrue(lenkung["configured"])
            self.assertTrue(lenkung["simulator"])
            self.assertTrue(lenkung["armed"])

    def test_a_real_receiver_still_needs_the_motor_enabled(self):
        import asyncio
        from agripilot.actuators import NullOutput
        from agripilot.steering import SteeringController
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.steering.enabled = False
        lenkung = SteeringController(config.steering, NullOutput())
        asyncio.run(lenkung.start())
        self.assertIn("deaktiviert", lenkung.arm())
        self.assertFalse(lenkung.armed)
