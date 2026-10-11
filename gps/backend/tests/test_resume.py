"""Fortsetzen nach Neustart: Feld, Spur und laufende Arbeit überleben Stromausfall."""

import os
import tempfile
import time
import unittest

from agripilot import config as config_module
from agripilot import geo, nmea
from agripilot.engine import Engine, CHECKPOINT_S, RESUME_MAX_AGE_S
from agripilot.storage import Storage


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "resume.db")
        self.store = Storage(self.path)
        self.plane = geo.LocalPlane(48.0, 11.0)
        self.engine = self._engine(self.store)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def _engine(self, store):
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.network.device_id = "pi-test"
        return Engine(config, store)

    def _restart(self):
        """Zündung aus, Zündung an: neue Datenbankverbindung, neues Programm."""
        self.store.close()
        self.store = Storage(self.path)
        self.engine = self._engine(self.store)
        return self.engine

    def _fix(self, east, north, when):
        lat, lon = self.plane.to_wgs(east, north)
        fix = nmea.Fix(lat=lat, lon=lon, fix_quality=4, speed_ms=3.0,
                       course_deg=0.0, altitude=500.0)
        fix.received_at = when
        return fix

    def _field_with_line(self):
        feld = self.store.save_field({"name": "Hinterm Hof", "datum_lat": 48.0,
                                      "datum_lon": 11.0})
        self.engine.load_field(feld["id"])
        self.engine.update_profile({"width_m": 6.0, "antenna_forward_m": 0.0,
                                    "tool_trailing_m": 0.0})
        self.engine.on_fix(self._fix(0.0, -10.0, 900.0))
        self.engine.set_ab_from_heading(0.0, "Hauptspur")
        return feld

    def _work(self, start=1000.0, meters=60):
        moment = start
        for n in range(meters):
            self.engine.on_fix(self._fix(0.0, float(n), moment))
            moment += 1.0 / 3.0
        return moment

    def test_field_and_line_come_back(self):
        feld = self._field_with_line()
        linie = self.engine.line.id
        motor = self._restart()
        self.assertIsNone(motor.field)
        meldung = motor.resume_session()
        self.assertEqual(motor.field["id"], feld["id"])
        self.assertEqual(motor.line.id, linie)
        self.assertIn("Hinterm Hof", meldung["text"])

    def test_nothing_to_resume_on_a_fresh_install(self):
        self.assertIsNone(self.engine.resume_session())

    def test_running_job_is_picked_up_with_its_area(self):
        self._field_with_line()
        self.engine.start_job("Grubbern")
        jetzt = self._work()
        self.engine.tick(now=time.time() + CHECKPOINT_S + 1)       # Sicherungspunkt
        self.engine.warte_auf_sicherung()
        flaeche = self.engine.coverage.area_ha
        self.assertGreater(flaeche, 0.0)
        job_id = self.engine.job["id"]

        motor = self._restart()
        meldung = motor.resume_session()
        self.assertEqual(motor.job["id"], job_id)
        self.assertIsNone(self.store.get_job(job_id)["ended_at"])
        self.assertAlmostEqual(motor.coverage.area_ha, flaeche, places=3)
        self.assertGreater(motor.distance_m, 0.0)
        self.assertTrue(meldung["arbeit"])

    def test_checkpoint_is_throttled(self):
        self._field_with_line()
        self.engine.start_job("")
        jetzt = self._work()
        self.engine.tick(now=time.time() + CHECKPOINT_S + 1)
        self.engine.warte_auf_sicherung()
        erste = self.store.get_job(self.engine.job["id"])["updated_at"]
        time.sleep(0.01)
        self.engine.tick(now=time.time() + CHECKPOINT_S + 2)       # zu früh für den nächsten
        self.engine.warte_auf_sicherung()
        self.assertEqual(self.store.get_job(self.engine.job["id"])["updated_at"], erste)

    def test_ended_job_is_not_resumed(self):
        self._field_with_line()
        self.engine.start_job("")
        self._work()
        self.engine.stop_job()
        motor = self._restart()
        meldung = motor.resume_session()
        self.assertIsNone(motor.job)
        self.assertFalse(meldung["arbeit"])

    def test_old_job_is_closed_not_resumed(self):
        self._field_with_line()
        job = self.engine.start_job("")
        self.store.update_job(job["id"])
        self.store.db.execute("UPDATE jobs SET updated_at=? WHERE id=?",
                              (time.time() - RESUME_MAX_AGE_S - 60, job["id"]))
        self.store.db.commit()
        motor = self._restart()
        motor.resume_session()
        self.assertIsNone(motor.job)
        self.assertIsNotNone(self.store.get_job(job["id"])["ended_at"])

    def test_deleted_field_is_skipped_quietly(self):
        feld = self._field_with_line()
        self.store.delete_field(feld["id"])
        motor = self._restart()
        self.assertIsNone(motor.resume_session())
        self.assertIsNone(motor.field)

    def test_contour_comes_back(self):
        feld = self._field_with_line()
        quadrat = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
        self.store.save_field({**self.store.get_field(feld["id"]),
                               "boundary": [list(p) for p in quadrat]})
        self.engine.load_field(feld["id"])
        self.engine.use_contour()
        motor = self._restart()
        motor.resume_session()
        self.assertEqual(motor.line.mode, "contour")

    def test_nudge_survives(self):
        self._field_with_line()
        self.engine.nudge(0.3)
        motor = self._restart()
        motor.resume_session()
        self.assertAlmostEqual(motor.line.nudge_m, 0.3, places=3)

    def test_pack_roundtrip_and_speed_for_a_large_field(self):
        from agripilot.coverage import CoverageMap
        karte = CoverageMap()
        for ix in range(400):
            for iy in range(250):
                karte.cells.add((ix - 200, iy - 100))      # 100 000 Zellen, ~25 ha
        start = time.perf_counter()
        blob = karte.pack()
        dauer = time.perf_counter() - start
        self.assertEqual(CoverageMap.unpack(blob).cells, karte.cells)
        self.assertLess(dauer, 0.15)       # vorher gut eine halbe Sekunde

    def test_steering_is_never_armed_by_resume(self):
        self._field_with_line()
        motor = self._restart()
        motor.resume_session()
        self.assertIsNone(motor.steering)      # der Server legt sie an und lässt sie unscharf


class ServerRestartTest(unittest.TestCase):
    """Ganze Anwendung: Server beenden, neu starten, Stand ist wieder da."""

    def test_app_resumes_field_line_and_job(self):
        import warnings
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")
        with tempfile.TemporaryDirectory() as directory:
            def konfig():
                config = config_module.load("/kein-solcher-pfad.yaml")
                config.server.data_dir = directory
                config.network.device_id = "pi-test"
                return config

            with TestClient(create_app(konfig())) as client:
                time.sleep(0.7)
                client.post("/api/fields", json={"name": "Testacker"})
                client.post("/api/guidance/a")
                client.post("/api/guidance/a-plus", json={"heading": 0, "name": "Nord"})
                client.post("/api/job/start", json={"operation": "Grubbern"})
                time.sleep(1.0)

            with TestClient(create_app(konfig())) as client:
                state = client.get("/api/state").json()
                self.assertEqual(state["field"]["name"], "Testacker")
                self.assertEqual(state["line"]["name"], "Nord")
                self.assertIsNotNone(state["job"])
                self.assertFalse(state["steering"]["armed"])


if __name__ == "__main__":
    unittest.main()
