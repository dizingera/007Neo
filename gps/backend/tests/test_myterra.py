"""Arbeitsberichte für MyTerra: Tätigkeit erkennen, Bericht bauen, nur Neues übergeben."""

import json
import os
import tempfile
import time
import unittest
import warnings

from agripilot import config as config_module
from agripilot import myterra
from agripilot.storage import Storage


class TaetigkeitTests(unittest.TestCase):
    def test_free_text_becomes_a_myterra_activity(self):
        faelle = {
            "Grubbern": "boden", "1. Stoppelsturz": "boden", "Pflügen": "boden",
            "Scheibenegge": "boden", "Säen": "saat", "Drillen Weizen": "saat", "Walzen": "walzen",
            "Düngen": "duengung", "Gülle ausbringen": "duengung", "Kalk streuen": "duengung",
            "Spritzen": "psm", "Pflanzenschutz Herbizid": "psm", "Mähen": "ernte",
            "Raps dreschen": "ernte", "Ballen pressen": "ernte",
            "": "sonstiges", "Transport": "sonstiges", "Arbeit": "sonstiges",
        }
        for text, erwartet in faelle.items():
            self.assertEqual(myterra.taetigkeit_aus(text), erwartet, text)

    def test_every_result_is_an_activity_myterra_knows(self):
        for text in ("x", "Gülle", "Walze", "Grubber", "Spritze", "Drille", "Mähdrescher"):
            self.assertIn(myterra.taetigkeit_aus(text), myterra.TAETIGKEITEN)


class BerichtTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Storage(os.path.join(self.dir.name, "m.db"))
        self.addCleanup(self.store.close)
        self.feld = self.store.save_field({"name": "Hinterm Hof", "datum_lat": 48.0, "datum_lon": 11.0,
                                           "area_ha": 3.4, "note": "aus Shapefile"})
        self.store.touch_device("tablet1", "Tablet Fendt", "client")

    def _arbeit(self, operation="Grubbern", flaeche=3.1, doppelt=0.1, ende=True, punkte=60, tablet="tablet1"):
        job = self.store.start_job(self.feld["id"], tablet, "Fendt 724", operation)
        start = time.mktime((2026, 10, 12, 8, 0, 0, 0, 0, -1))
        self.store.update_job(job["id"], started_at=start,
                              **({"ended_at": start + 5400, "distance_m": 12400.0, "area_ha": flaeche,
                                  "overlap_ha": doppelt, "working_time_s": 4800.0} if ende else {}))
        self.store.add_track_points(job["id"], [
            (start + i, 48.0 + i * 1e-5, 11.0 + i * 1e-5, 500.0, 3.0, 0.0, 4, 0.01) for i in range(punkte)])
        return job["id"]

    def _bericht(self, **kw):
        job_id = self._arbeit(**kw)
        daten, _ = myterra.paket_bauen(self.store, "Büro", [job_id], markieren=False)
        return json.loads(daten)["berichte"][0]

    def test_the_report_carries_everything_myterra_needs(self):
        b = self._bericht()
        self.assertTrue(b["id"].startswith("agripilot-"))
        self.assertEqual(b["schlag"]["name"], "Hinterm Hof")
        self.assertEqual(b["schlag"]["flaeche_ha"], 3.4)
        self.assertEqual(b["datum"], "2026-10-12")
        self.assertEqual(b["dauer_h"], 1.5)
        self.assertEqual(b["arbeitszeit_h"], 1.33)
        self.assertEqual(b["flaeche_ha"], 3.1)
        self.assertEqual(b["doppelt_ha"], 0.1)
        self.assertEqual(b["ueberlappung_prozent"], 3.1)
        self.assertEqual(b["strecke_km"], 12.4)
        self.assertEqual(b["taetigkeit"], "boden")
        self.assertEqual(b["taetigkeit_text"], "Grubbern")
        self.assertEqual(b["maschine"], "Fendt 724")
        self.assertEqual(b["geraet"], "Tablet Fendt")

    def test_the_track_is_a_geojson_line_in_lon_lat_and_not_too_long(self):
        b = self._bericht(punkte=2000)
        self.assertEqual(b["spur"]["type"], "LineString")
        koordinaten = b["spur"]["coordinates"]
        self.assertLessEqual(len(koordinaten), myterra.MAX_SPURPUNKTE)
        lon, lat = koordinaten[0]
        self.assertAlmostEqual(lat, 48.0, delta=0.01)
        self.assertAlmostEqual(lon, 11.0, delta=0.01)

    def test_a_job_without_a_track_has_no_track(self):
        self.assertIsNone(self._bericht(punkte=0)["spur"])

    def test_only_finished_jobs_with_area_are_offered(self):
        fertig = self._arbeit()
        self._arbeit(ende=False)                     # läuft noch
        self._arbeit(flaeche=0.0)                    # nichts markiert
        self.assertEqual([a["id"] for a in myterra.arbeiten(self.store)], [fertig])

    def test_the_file_is_valid_json_with_format_and_version(self):
        self._arbeit()
        daten, genommen = myterra.paket_bauen(self.store, "Tablet Fendt")
        inhalt = json.loads(daten.decode("utf-8"))
        self.assertEqual((inhalt["format"], inhalt["version"], inhalt["von"]),
                         ("agripilot-arbeitsbericht", 1, "Tablet Fendt"))
        self.assertEqual(len(inhalt["berichte"]), 1)
        self.assertEqual(len(genommen), 1)

    def test_only_new_jobs_are_handed_over_the_next_time(self):
        erste = self._arbeit()
        _, genommen = myterra.paket_bauen(self.store, "x")
        self.assertEqual(genommen, [erste])
        zweite = self._arbeit("Walzen")
        daten, genommen = myterra.paket_bauen(self.store, "x")
        self.assertEqual(genommen, [zweite])
        _, genommen = myterra.paket_bauen(self.store, "x")
        self.assertEqual(genommen, [])

    def test_chosen_jobs_can_be_sent_again(self):
        job_id = self._arbeit()
        myterra.paket_bauen(self.store, "x")
        daten, genommen = myterra.paket_bauen(self.store, "x", [job_id])
        self.assertEqual(genommen, [job_id])
        self.assertEqual(json.loads(daten)["berichte"][0]["id"], f"agripilot-{job_id}")

    def test_everything_again_is_possible(self):
        self._arbeit()
        myterra.paket_bauen(self.store, "x")
        _, genommen = myterra.paket_bauen(self.store, "x", nur_neue=False)
        self.assertEqual(len(genommen), 1)

    def test_a_dry_run_marks_nothing(self):
        self._arbeit()
        myterra.paket_bauen(self.store, "x", markieren=False)
        self.assertEqual(len(myterra.paket_bauen(self.store, "x")[1]), 1)

    def test_the_list_shows_what_was_handed_over(self):
        job_id = self._arbeit()
        self.assertIsNone(myterra.arbeiten(self.store)[0]["uebergeben"])
        myterra.paket_bauen(self.store, "x")
        self.assertIsNotNone(myterra.arbeiten(self.store)[0]["uebergeben"])
        self.assertEqual(myterra.arbeiten(self.store)[0]["id"], job_id)


class ServerTests(unittest.TestCase):
    def test_the_file_comes_through_the_interface(self):
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")
        with tempfile.TemporaryDirectory() as ordner:
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = ordner
            config.network.device_id = "pi-test"
            config.network.device_name = "Büro-PC"
            with TestClient(create_app(config)) as c:
                c.post("/api/fields", json={"name": "Hinterm Hof"})
                liste = c.get("/api/myterra/arbeiten").json()
                self.assertEqual(liste, [])
                antwort = c.get("/api/myterra/export")
                self.assertEqual(antwort.status_code, 200)
                self.assertIn("agripilot-arbeiten-", antwort.headers["content-disposition"])
                self.assertEqual(json.loads(antwort.content)["von"], "Büro-PC")
                self.assertEqual(json.loads(antwort.content)["berichte"], [])


if __name__ == "__main__":
    unittest.main()
