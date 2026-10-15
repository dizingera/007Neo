"""Zwischenspeichern ohne Pause: die Lenkschleife wartet nicht auf die Festplatte.

Gemessen im Dauerlauf: das Sichern der bearbeiteten Fläche kostete auf einem PC
50 bis 150 ms, in Ausreißern über eine halbe Sekunde - und lief auf demselben
Faden wie Position und Lenkung. Jetzt schreibt ein eigener Faden.
"""

import os
import tempfile
import time
import unittest

from agripilot import config as config_module
from agripilot import geo, nmea
from agripilot.engine import CHECKPOINT_S, Engine
from agripilot.storage import Storage


class AsyncCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "ck.db")
        self.store = Storage(self.path)
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.network.device_id = "pi-test"
        self.engine = Engine(config, self.store)
        self.plane = geo.LocalPlane(48.0, 11.0)
        feld = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 11.0})
        self.engine.load_field(feld["id"])
        self.engine.update_profile({"width_m": 6.0, "antenna_forward_m": 0.0,
                                    "tool_trailing_m": 0.0})
        self.uhr = time.time()
        self.engine.on_fix(self._fix(0.0, -5.0))
        self.engine.set_ab_from_heading(0.0, "N")
        self.engine.start_job("Test")

    def tearDown(self):
        self.engine.close()
        self.store.close()
        self.dir.cleanup()

    def _fix(self, east, north):
        lat, lon = self.plane.to_wgs(east, north)
        self.uhr += 0.1
        fix = nmea.Fix(lat=lat, lon=lon, fix_quality=4, speed_ms=3.0, course_deg=0.0,
                       altitude=500.0)
        fix.received_at = self.uhr
        return fix

    def _arbeiten(self, meter=80):
        for n in range(meter):
            self.engine.on_fix(self._fix(0.0, float(n)))

    def test_a_slow_disk_does_not_stall_tick_or_position(self):
        self._arbeiten()
        original = Storage.update_job

        def langsam(self_, *a, **k):
            time.sleep(0.4)
            return original(self_, *a, **k)

        Storage.update_job = langsam
        try:
            t0 = time.perf_counter()
            self.engine.tick(now=time.time() + CHECKPOINT_S + 1)
            self.engine.on_fix(self._fix(0.0, 90.0))
            dauer = time.perf_counter() - t0
            self.assertLess(dauer, 0.15, f"Lenkschleife wartete {dauer * 1000:.0f} ms")
        finally:
            self.engine.warte_auf_sicherung()
            Storage.update_job = original

    def test_the_data_arrives_in_the_database(self):
        self._arbeiten()
        self.engine.tick(now=time.time() + CHECKPOINT_S + 1)
        self.engine.warte_auf_sicherung()
        job = self.store.get_job(self.engine.job["id"])
        self.assertGreater(job["area_ha"], 0.0)
        self.assertTrue(self.store.get_job_coverage(job["id"]))
        self.assertGreater(len(self.store.track_points(job["id"])), 0)

    def test_a_snapshot_is_not_changed_by_work_that_follows(self):
        """Der Faden bekommt eine Kopie der Zellen, nicht die laufende Karte."""
        self._arbeiten(40)
        flaeche_vorher = self.engine.coverage.area_ha
        original = Storage.update_job

        def langsam(self_, *a, **k):
            time.sleep(0.2)
            return original(self_, *a, **k)

        Storage.update_job = langsam
        try:
            self.engine.tick(now=time.time() + CHECKPOINT_S + 1)
            self._arbeiten(40)                      # weiter, während geschrieben wird
        finally:
            self.engine.warte_auf_sicherung()
            Storage.update_job = original
        job = self.store.get_job(self.engine.job["id"])
        self.assertAlmostEqual(job["area_ha"], flaeche_vorher, places=3)

    def test_only_one_write_is_in_flight(self):
        self._arbeiten()
        zaehler = {"n": 0}
        original = Storage.update_job

        def zaehlen(self_, *a, **k):
            zaehler["n"] += 1
            time.sleep(0.2)
            return original(self_, *a, **k)

        Storage.update_job = zaehlen
        try:
            jetzt = time.time()
            self.engine.tick(now=jetzt + CHECKPOINT_S + 1)
            self.engine.tick(now=jetzt + 2 * CHECKPOINT_S + 5)    # vorige noch nicht fertig
        finally:
            self.engine.warte_auf_sicherung()
            Storage.update_job = original
        self.assertEqual(zaehler["n"], 1)

    def test_suspend_job_waits_until_everything_is_written(self):
        self._arbeiten()
        self.engine.suspend_job()
        job = self.store.get_job(self.engine.job["id"])
        self.assertGreater(job["area_ha"], 0.0)
        self.assertGreater(len(self.store.track_points(job["id"])), 0)

    def test_stop_job_has_all_track_points(self):
        self._arbeiten()
        job_id = self.engine.job["id"]
        self.engine.stop_job()
        self.assertGreaterEqual(len(self.store.track_points(job_id)), 5)

    def test_a_failing_write_is_reported_not_swallowed(self):
        self._arbeiten()
        original = Storage.update_job

        def kaputt(self_, *a, **k):
            raise OSError("Platte voll")

        Storage.update_job = kaputt
        try:
            self.engine.tick(now=time.time() + CHECKPOINT_S + 1)
            self.engine.warte_auf_sicherung()
        finally:
            Storage.update_job = original
        self.assertTrue(any("Platte voll" in m for m in self.engine.messages))


if __name__ == "__main__":
    unittest.main()
