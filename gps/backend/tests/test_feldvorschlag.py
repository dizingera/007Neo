"""Feld erkennen: steht der Traktor in einem bekannten Feld, wird es vorgeschlagen."""

import os
import tempfile
import time
import unittest

from agripilot import config as config_module
from agripilot import geo, nmea
from agripilot.engine import Engine, FELD_VORSCHLAG_S
from agripilot.storage import Storage

QUADRAT = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]


class FeldVorschlagTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Storage(os.path.join(self.dir.name, "f.db"))
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.network.device_id = "pi-test"
        self.engine = Engine(config, self.store)
        # Zwei Felder, rund 1,1 km auseinander.
        self.a = self.store.save_field({"name": "Hofacker", "datum_lat": 48.000,
                                        "datum_lon": 11.000, "boundary": QUADRAT})
        self.b = self.store.save_field({"name": "Bachwiese", "datum_lat": 48.010,
                                        "datum_lon": 11.000, "boundary": QUADRAT})

        self._uhr = time.time()

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def _fix(self, lat, lon, when=None):
        fix = nmea.Fix(lat=lat, lon=lon, fix_quality=4, speed_ms=0.0,
                       course_deg=0.0, altitude=500.0)
        fix.received_at = when if when is not None else time.time()
        return fix

    def _in_feld(self, feld):
        plane = geo.LocalPlane(feld["datum_lat"], feld["datum_lon"])
        return plane.to_wgs(50.0, 50.0)

    def _stehen(self, lat, lon, sekunden):
        """Der Traktor steht ``sekunden`` lang; jede Sekunde ein Fix und ein Tick."""
        for _ in range(int(sekunden) + 1):
            self._uhr += 1.0
            self.engine.on_fix(self._fix(lat, lon, self._uhr))
            self.engine.tick(now=self._uhr)
        return self._uhr

    def test_suggests_the_field_the_tractor_stands_in(self):
        lat, lon = self._in_feld(self.b)
        self._stehen(lat, lon, FELD_VORSCHLAG_S + 1)
        vorschlag = self.engine.feld_vorschlag
        self.assertIsNotNone(vorschlag)
        self.assertEqual(vorschlag["id"], self.b["id"])
        self.assertEqual(vorschlag["name"], "Bachwiese")

    def test_not_immediately_but_after_a_few_seconds(self):
        lat, lon = self._in_feld(self.b)
        self._stehen(lat, lon, 1)
        self.assertIsNone(self.engine.feld_vorschlag)

    def test_no_suggestion_when_already_in_the_loaded_field(self):
        self.engine.load_field(self.b["id"])
        lat, lon = self._in_feld(self.b)
        self._stehen(lat, lon, FELD_VORSCHLAG_S + 1)
        self.assertIsNone(self.engine.feld_vorschlag)

    def test_suggests_the_other_field_when_driving_over(self):
        self.engine.load_field(self.a["id"])
        lat, lon = self._in_feld(self.b)
        self._stehen(lat, lon, FELD_VORSCHLAG_S + 1)
        self.assertEqual(self.engine.feld_vorschlag["id"], self.b["id"])

    def test_no_suggestion_on_the_road(self):
        self._stehen(48.005, 11.000, FELD_VORSCHLAG_S + 1)     # zwischen den Feldern
        self.assertIsNone(self.engine.feld_vorschlag)

    def test_no_suggestion_while_a_job_is_running(self):
        self.engine.load_field(self.a["id"])
        self.engine.on_fix(self._fix(*self._in_feld(self.a)))
        self.engine.start_job("Grubbern")
        lat, lon = self._in_feld(self.b)
        self._stehen(lat, lon, FELD_VORSCHLAG_S + 1)
        self.assertIsNone(self.engine.feld_vorschlag)

    def test_suggestion_goes_away_when_leaving(self):
        lat, lon = self._in_feld(self.b)
        self._stehen(lat, lon, FELD_VORSCHLAG_S + 1)
        self.assertIsNotNone(self.engine.feld_vorschlag)
        self._stehen(48.005, 11.000, 2)
        self.assertIsNone(self.engine.feld_vorschlag)

    def test_deleted_field_is_not_suggested(self):
        self.store.delete_field(self.b["id"])
        self.engine.refresh_field_cache()
        lat, lon = self._in_feld(self.b)
        self._stehen(lat, lon, FELD_VORSCHLAG_S + 1)
        self.assertIsNone(self.engine.feld_vorschlag)

    def test_state_carries_the_suggestion(self):
        lat, lon = self._in_feld(self.b)
        self._stehen(lat, lon, FELD_VORSCHLAG_S + 1)
        self.assertEqual(self.engine.state()["feld_vorschlag"]["name"], "Bachwiese")


if __name__ == "__main__":
    unittest.main()
