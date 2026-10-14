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


class GyroFehlerTests(unittest.TestCase):
    """Im Simulator stand der gelernte Gyro-Fehler am Anschlag (-3 °/s), obwohl
    der simulierte Gyro fehlerfrei ist. Im Stand drehte sich der Traktor dann
    weiter, bis vorn und hinten vertauscht waren - und mit ihnen rechts und
    links beim 10-cm-Versatz."""

    @staticmethod
    def _fahren(kurs, wahr, schritte, rate, tempo=2.5, dt=0.1):
        for _ in range(schritte):
            wahr = (wahr + rate * dt) % 360.0
            kurs.update(wahr, None, tempo, rate, dt)
        return wahr

    def test_exact_data_never_teaches_a_gyro_error(self):
        from agripilot.guidance import HeadingFilter
        kurs = HeadingFilter()
        wahr = 0.0
        for runde in range(40):          # sanfte Schlangenlinien, immer wieder Gerade
            wahr = self._fahren(kurs, wahr, 30, 2.0 if runde % 2 else -2.0)
            wahr = self._fahren(kurs, wahr, 30, 0.0)
            wahr = self._fahren(kurs, wahr, 20, 25.0)   # enge Wende
        self.assertLess(abs(kurs.bias_deg_s), 0.3)

    def test_a_wrong_gyro_error_is_unlearned_on_the_straight(self):
        from agripilot.guidance import HeadingFilter
        kurs = HeadingFilter()
        self._fahren(kurs, 0.0, 5, 0.0)
        kurs.bias_deg_s = -3.0           # so stand es im Simulator
        self._fahren(kurs, 0.0, 600, 0.0)
        self.assertLess(abs(kurs.bias_deg_s), 0.3)

    def test_standing_still_keeps_the_heading(self):
        from agripilot.guidance import HeadingFilter
        kurs = HeadingFilter()
        self._fahren(kurs, 90.0, 50, 0.0)
        for _ in range(600):              # eine Minute Stand, Gyro meldet nichts
            kurs.update(None, None, 0.0, 0.0, 0.1)
        self.assertAlmostEqual(kurs.value, 90.0, delta=2.0)


class SimulatorAntenneTests(unittest.TestCase):
    def test_the_simulator_reports_the_antenna_ahead_of_the_axle(self):
        """Der Simulator bewegt die Hinterachse und meldet die Antenne davor -
        wie am echten Traktor. Vorher meldete er die Achse als Antenne, und die
        Engine rechnete eine Achse dahinter, die in jedem Bogen ausschwenkte."""
        from agripilot.guidance import VehicleProfile
        sim = SimulatorSource(lambda fix: None)
        sim.fahrzeug_setzen(VehicleProfile(antenna_forward_m=1.85, antenna_right_m=0.2, wheelbase_m=2.8))
        sim.teleport(10.0, 20.0, 90.0)          # nach Osten
        ost, nord = sim.antenne()
        self.assertAlmostEqual(ost, 11.85, places=6)
        self.assertAlmostEqual(nord, 19.8, places=6)
        self.assertEqual(sim.wheelbase, 2.8)


class VersatzBildschirmTests(unittest.TestCase):
    """„› 10 cm“ schiebt die Spur dorthin, wo auf dem Bildschirm rechts ist -
    auch in „Norden oben“ und auch, wenn der errechnete Kurs falsch herum steht."""

    def _motor(self, mode, punkte):
        from agripilot.guidance import GuidanceLine
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "v.db"))
        self.addCleanup(store.close)
        motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
        feld = store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 9.6,
                                 "boundary": [(-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)]})
        motor.load_field(feld["id"])
        motor.line = GuidanceLine(mode=mode, points=punkte, spacing=6.0)
        motor.line.derived = True              # nicht speichern
        motor.tool_position = (-2.0, 0.0)
        return motor

    def _verschiebung(self, motor, rechts):
        punkt = motor._lenkpunkt()
        vorher = motor.line.solve(punkt, 0.0, 2.0, motor.profile).cross_track_m
        motor.nudge(0.1, rechts)
        richtung = motor.line.versatz_richtung(punkt)
        return motor.line.nudge_m * (richtung[0] * rechts[0] + richtung[1] * rechts[1]), vorher

    def test_the_line_moves_to_screen_right_whatever_the_line_direction(self):
        for punkte in ([(0.0, -50.0), (0.0, 50.0)], [(0.0, 50.0), (0.0, -50.0)]):
            for rechts in ([1.0, 0.0], [-1.0, 0.0]):     # Bildschirm rechts = Osten / = Westen
                motor = self._motor("ab", punkte)
                schub, _ = self._verschiebung(motor, rechts)
                self.assertAlmostEqual(schub, 0.1, places=6, msg=f"{punkte} {rechts}")

    def test_the_contour_too(self):
        ring = [(-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)]
        for rechts in ([1.0, 0.0], [-1.0, 0.0]):
            motor = self._motor("contour", ring)
            motor.tool_position = (-97.0, 0.0)          # an der Westkante
            schub, _ = self._verschiebung(motor, rechts)
            self.assertAlmostEqual(schub, 0.1, places=6, msg=str(rechts))


class LenkungMarkiertTests(unittest.TestCase):
    def test_arming_the_steering_also_starts_marking(self):
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
            store = Storage(os.path.join(ordner.name, "agripilot.db"))
            self.addCleanup(store.close)
            feld = store.save_field({"name": "F", "datum_lat": BEZUG[0], "datum_lon": BEZUG[1],
                                     "boundary": QUADRAT})
            client.post(f"/api/fields/{feld['id']}/load")
            antwort = client.post("/api/steering/arm", json={"operation": "Säen"}).json()["data"]
            self.assertTrue(antwort["armed"])
            self.assertTrue(antwort["markieren_gestartet"])
            job = client.get("/api/state").json()["job"]
            self.assertEqual(job["operation"], "Säen")
            # Schon markierend: kein zweiter Auftrag
            client.post("/api/steering/disarm")
            zweite = client.post("/api/steering/arm").json()["data"]
            self.assertFalse(zweite["markieren_gestartet"])


class FahrgasseUndWendeTests(unittest.TestCase):
    def setUp(self):
        from agripilot.guidance import GuidanceLine, GuidanceState
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        self.store = Storage(os.path.join(ordner.name, "f.db"))
        self.addCleanup(self.store.close)
        self.motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), self.store)
        feld = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 9.6, "boundary": QUADRAT})
        self.motor.load_field(feld["id"])
        self.motor.line = GuidanceLine(mode="ab", points=[(0.0, -50.0), (0.0, 50.0)], spacing=6.0)
        self.motor.line.fahrgasse_m = 18.0            # jede dritte Spur
        self.GuidanceState = GuidanceState

    def _auf_spur(self, nummer):
        self.motor.guidance = self.GuidanceState(active=True, mode="ab", pass_number=nummer)
        return self.motor.state()["fahrgasse_aktiv"]

    def test_the_tramline_pass_is_reported(self):
        self.assertTrue(self._auf_spur(3))
        self.assertFalse(self._auf_spur(4))
        self.assertTrue(self._auf_spur(-3))
        self.assertTrue(self._auf_spur(0))

    def test_after_a_finished_turn_the_next_goes_the_other_way(self):
        self.assertEqual(self.motor.headland.richtung, "links")
        self.motor._wenderichtung_wechseln()
        self.assertEqual(self.motor.headland.richtung, "rechts")
        self.assertEqual(self.store.get_setting("headland")["richtung"], "rechts")
