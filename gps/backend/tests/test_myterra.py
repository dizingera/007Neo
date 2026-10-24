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


MYTERRA_LISTE = [
    {"id": "tractor-jd7530", "type": "Traktor", "active": True, "name": "John Deere 7530 Premium", "hp": 204},
    {"id": "tractor-ihc956", "type": "Traktor", "active": True, "name": "IHC 956 XL", "hp": 95},
    {"id": "impl-disc3", "type": "Bodenbearbeitung", "active": True, "name": "Scheibenegge 3 m", "widthM": 3},
    {"id": "impl-cultivator250", "type": "Bodenbearbeitung", "active": True, "name": "Grubber 2,50 m", "widthM": 2.5},
    {"id": "impl-guettler", "type": "Bodenbearbeitung", "active": True, "name": "Güttler Großfederzahnegge", "widthM": 0},
    {"id": "impl-alt", "type": "Saat", "active": False, "name": "Alte Drille"},
    {"id": "combine-dominator98", "type": "Mähdrescher", "active": True, "name": "Claas Dominator 98 SL"},
]


class MaschinenUebernahmeTests(unittest.TestCase):
    def _client(self, ordner):
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.server.data_dir = ordner
        return TestClient(create_app(config))

    def test_tractors_become_tractors_and_implements_machines(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            vorher = next(p for p in c.get("/api/profiles").json() if p["aktiv"])
            traktor_vorher = next(t for t in c.get("/api/traktoren").json() if t["aktiv"])
            antwort = c.post("/api/myterra/maschinen", json={
                "maschinen": MYTERRA_LISTE, "traktor": "John Deere 7530 Premium"}).json()["data"]
            self.assertEqual(antwort["neu"], ["Scheibenegge 3 m", "Grubber 2,50 m",
                                              "Güttler Großfederzahnegge", "Schneidwerk Claas Dominator 98 SL"])
            self.assertEqual(antwort["ohne_breite"], ["Güttler Großfederzahnegge",
                                                      "Schneidwerk Claas Dominator 98 SL"])
            self.assertEqual(antwort["traktoren_neu"], ["Claas Dominator 98 SL", "IHC 956 XL",
                                                        "John Deere 7530 Premium"])
            profile = {p["name"]: p for p in c.get("/api/profiles").json()}
            self.assertEqual(profile["Grubber 2,50 m"]["width_m"], 2.5)
            self.assertEqual(profile["Grubber 2,50 m"]["anbaugeraet"], "Grubber 2,50 m")
            self.assertNotIn("Alte Drille", profile)                 # außer Betrieb
            self.assertNotIn("IHC 956 XL", profile)                   # ein Traktor ist keine Maschine
            traktoren = {t["name"]: t for t in c.get("/api/traktoren").json()}
            self.assertIn("IHC 956 XL", traktoren)
            self.assertNotIn("Grubber 2,50 m", traktoren)
            # Gewählte Maschine und gewählter Traktor bleiben gewählt.
            self.assertTrue(profile[vorher["name"]]["aktiv"])
            self.assertTrue(traktoren[traktor_vorher["name"]]["aktiv"])
            katalog = c.get("/api/myterra/maschinen").json()
            self.assertEqual(katalog["traktoren"], ["Claas Dominator 98 SL", "IHC 956 XL",
                                                    "John Deere 7530 Premium"])
            self.assertNotIn("Claas Dominator 98 SL", katalog["geraete"])
            # Der Mähdrescher ist ein Fahrzeug, sein Schneidwerk sitzt vorn.
            self.assertEqual(traktoren["Claas Dominator 98 SL"]["bauform"], "maehdrescher")
            self.assertEqual(traktoren["Claas Dominator 98 SL"]["marke"], "claas")
            self.assertNotIn("Claas Dominator 98 SL", profile)
            schneidwerk = profile["Schneidwerk Claas Dominator 98 SL"]
            self.assertEqual(schneidwerk["geraet_art"], "schneidwerk")
            self.assertLess(schneidwerk["tool_trailing_m"], 0)
            self.assertEqual(schneidwerk["anbaugeraet"], "")

    def test_running_it_again_changes_nothing_that_was_set(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            c.post("/api/myterra/maschinen", json={"maschinen": MYTERRA_LISTE})
            egge = next(p for p in c.get("/api/profiles").json() if p["name"] == "Güttler Großfederzahnegge")
            c.post(f"/api/profiles/{egge['id']}/select")
            c.post("/api/profile", json={"width_m": 6.0})            # selbst gemessen
            zweites = c.post("/api/myterra/maschinen", json={"maschinen": MYTERRA_LISTE}).json()["data"]
            self.assertEqual(zweites["neu"], [])
            self.assertEqual(len(zweites["vorhanden"]), 4)
            self.assertEqual(zweites["traktoren_neu"], [])
            egge = next(p for p in c.get("/api/profiles").json() if p["name"] == "Güttler Großfederzahnegge")
            self.assertEqual(egge["width_m"], 6.0)

    def test_an_older_machine_inherits_nothing_and_unknown_widths_copy_the_chosen_one(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            # Eine Maschine aus einer älteren Version: ohne die neuen Felder.
            alt = c.post("/api/profiles", json={"name": "SuperMax", "werte": {"width_m": 4.0}}).json()["data"]
            store = c.app.state.app.engine.store
            liste = store.get_setting("vehicle_profiles")
            for p in liste:
                if p["id"] == alt["id"]:
                    p.pop("traktor", None)
                    p.pop("anbaugeraet", None)
            store.set_setting("vehicle_profiles", liste)
            c.post(f"/api/profiles/{alt['id']}/select")
            c.post("/api/myterra/maschinen", json={"maschinen": MYTERRA_LISTE,
                                                   "traktor": "John Deere 7530 Premium"})
            profile = {p["name"]: p for p in c.get("/api/profiles").json()}
            self.assertTrue(profile["SuperMax"]["aktiv"])
            self.assertEqual(profile["SuperMax"]["anbaugeraet"], "")    # nichts geerbt
            self.assertEqual(profile["SuperMax"]["traktor"], "")
            self.assertEqual(profile["SuperMax"]["width_m"], 4.0)
            # Ohne Breite aus MyTerra: die der gewählten Maschine, nicht die des Grubbers davor.
            self.assertEqual(profile["Güttler Großfederzahnegge"]["width_m"], 4.0)
            self.assertEqual(profile["Grubber 2,50 m"]["width_m"], 2.5)

    def test_a_combine_from_an_older_import_becomes_vehicle_and_its_header(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            # So legte der Abgleich vor 1.5.75 ihn an: als Gerät, die Breite selbst eingetragen.
            c.post("/api/profiles", json={"name": "Claas Dominator 98 SL", "werte": {
                "width_m": 4.5, "anbaugeraet": "Claas Dominator 98 SL"}})
            c.post("/api/traktoren", json={"name": "Claas Dominator 98 SL"})
            c.post("/api/myterra/maschinen", json={"maschinen": MYTERRA_LISTE})
            profile = {p["name"]: p for p in c.get("/api/profiles").json()}
            self.assertNotIn("Claas Dominator 98 SL", profile)
            self.assertEqual(profile["Schneidwerk Claas Dominator 98 SL"]["width_m"], 4.5)
            self.assertEqual(profile["Schneidwerk Claas Dominator 98 SL"]["geraet_art"], "schneidwerk")
            traktoren = {t["name"]: t for t in c.get("/api/traktoren").json()}
            self.assertEqual(traktoren["Claas Dominator 98 SL"]["bauform"], "maehdrescher")

    def test_choosing_the_combine_brings_its_header_and_back(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            c.post("/api/myterra/maschinen", json={"maschinen": MYTERRA_LISTE})
            grubber = next(p for p in c.get("/api/profiles").json() if p["name"] == "Grubber 2,50 m")
            c.post(f"/api/profiles/{grubber['id']}/select")
            traktoren = {t["name"]: t for t in c.get("/api/traktoren").json()}
            c.post(f"/api/traktoren/{traktoren['Claas Dominator 98 SL']['id']}/select")
            gewaehlt = next(p for p in c.get("/api/profiles").json() if p["aktiv"])
            self.assertEqual(gewaehlt["name"], "Schneidwerk Claas Dominator 98 SL")
            zustand = c.get("/api/state").json()["profile"]
            self.assertEqual(zustand["bauform"], "maehdrescher")
            self.assertLess(zustand["tool_trailing_m"], 0)
            # Zurück zum Traktor: das Gerät, das er zuletzt hatte.
            c.post(f"/api/traktoren/{traktoren['IHC 956 XL']['id']}/select")
            gewaehlt = next(p for p in c.get("/api/profiles").json() if p["aktiv"])
            self.assertEqual(gewaehlt["name"], "Grubber 2,50 m")

    def test_a_new_combine_without_header_gets_one(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            c.post("/api/traktoren", json={"name": "Lexion 760"})
            c.post("/api/traktor", json={"bauform": "maehdrescher"})
            gewaehlt = next(p for p in c.get("/api/profiles").json() if p["aktiv"])
            self.assertEqual(gewaehlt["name"], "Schneidwerk Lexion 760")
            self.assertEqual(gewaehlt["geraet_art"], "schneidwerk")

    def test_unknown_tractor_and_missing_list_are_refused(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            self.assertEqual(c.post("/api/myterra/maschinen", json={}).status_code, 400)
            fehler = c.post("/api/myterra/maschinen", json={"maschinen": MYTERRA_LISTE, "traktor": "Fendt 724"})
            self.assertEqual(fehler.status_code, 400)
            self.assertIn("Fendt 724", fehler.json()["detail"])


class MaschineImBerichtTests(BerichtTests):
    def test_report_names_tractor_and_implement_as_myterra_does(self):
        self.store.set_setting("vehicle_profiles", [
            {"id": "a", "name": "Fendt 724", "traktor": "John Deere 7530 Premium",
             "anbaugeraet": "Grubber 2,50 m"}])
        b = self._bericht()
        self.assertEqual(b["maschine"], "John Deere 7530 Premium")
        self.assertEqual(b["anbaugeraet"], "Grubber 2,50 m")
        self.assertEqual(b["agripilot_maschine"], "Fendt 724")

    def test_without_assignment_the_machine_name_goes(self):
        b = self._bericht()
        self.assertEqual(b["maschine"], "Fendt 724")
        self.assertEqual(b["anbaugeraet"], "")


def _rechteck(lat, lon, breite_m=100.0, laenge_m=200.0):
    """Ein Schlag als GeoJSON-Ring (lon, lat), etwa breite x laenge Meter."""
    dlat = laenge_m / 111_320.0
    import math as _m
    dlon = breite_m / (111_320.0 * _m.cos(_m.radians(lat)))
    return [[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat], [lon, lat]]


def _schlag(name, lat=48.41, lon=9.60, flik="DEBWLI00D2RZVSMW", **extra):
    return {"name": name, "flik": flik, "flaeche": 2.0,
            "geo": {"type": "Polygon", "coordinates": [_rechteck(lat, lon)]}, **extra}


class SchlagUebernahmeTests(unittest.TestCase):
    def _client(self, ordner):
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.server.data_dir = ordner
        return TestClient(create_app(config))

    def test_fields_come_with_the_myterra_name_outline_and_flik(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            antwort = c.post("/api/myterra/schlaege", json={"schlaege": [
                _schlag("Roggenäcker / Nr. 1"),
                _schlag("Hinterm Hof / Nr. 2", lat=48.42),
                {"name": "Ohne Umriss / Nr. 3", "geo": None},
            ]}).json()["data"]
            self.assertEqual(antwort["neu"], ["Roggenäcker / Nr. 1", "Hinterm Hof / Nr. 2"])
            self.assertEqual(antwort["ohne_umriss"], ["Ohne Umriss / Nr. 3"])
            felder = {f["name"]: f for f in c.get("/api/fields").json()}
            roggen = felder["Roggenäcker / Nr. 1"]
            self.assertAlmostEqual(roggen["area_ha"], 2.0, delta=0.05)
            self.assertEqual(roggen["note"], "aus MyTerra · FLIK DEBWLI00D2RZVSMW")
            self.assertGreaterEqual(len(roggen["boundary"]), 4)

    def test_a_field_in_two_parts_becomes_two_fields(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            zwei = {"name": "Talwiese", "geo": {"type": "MultiPolygon", "coordinates": [
                [_rechteck(48.41, 9.60)], [_rechteck(48.415, 9.61)]]}}
            antwort = c.post("/api/myterra/schlaege", json={"schlaege": [zwei]}).json()["data"]
            self.assertEqual(antwort["neu"], ["Talwiese (1)", "Talwiese (2)"])

    def test_the_same_field_under_an_old_name_is_renamed_not_doubled(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            from agripilot.shapefile import Umriss
            engine = c.app.state.app.engine
            ring = [(lat, lon) for lon, lat in _rechteck(48.41, 9.60)]
            alt = engine.import_fields([Umriss("AuingerBaum1", ring)])[0]
            weit_weg = engine.import_fields([Umriss("Anderswo", [(lat, lon + 0.01) for lat, lon in ring])])[0]
            antwort = c.post("/api/myterra/schlaege", json={"schlaege": [
                _schlag("Auinger Baum 1 / Nr. 11")]}).json()["data"]
            self.assertEqual(antwort["umbenannt"], ["AuingerBaum1 → Auinger Baum 1 / Nr. 11"])
            self.assertEqual(antwort["neu"], [])
            felder = {f["name"]: f for f in c.get("/api/fields").json()}
            self.assertNotIn("AuingerBaum1", felder)
            self.assertEqual(felder["Auinger Baum 1 / Nr. 11"]["id"], alt["id"])   # Spuren bleiben dran
            self.assertEqual(felder["Anderswo"]["id"], weit_weg["id"])          # nicht angefasst

    def test_a_coarse_old_outline_is_still_recognised(self):
        # Echter Fall: "AuingerBaum1" kam als grober Umriss (wenige Ecken, ungleich verteilt),
        # MyTerra hat denselben Schlag fein - der Durchschnitt der Ecken liegt weit auseinander.
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            from agripilot.shapefile import Umriss
            engine = c.app.state.app.engine
            fein = _rechteck(48.41, 9.60, 100.0, 400.0)
            # Viele Punkte auf der Nordkante: zieht den Eckendurchschnitt nach Norden.
            west, ost, nord = fein[0][0], fein[1][0], fein[2][1]
            nordkante = [[ost - (ost - west) * i / 40, nord] for i in range(41)]
            fein_ring = [fein[0], fein[1]] + nordkante + [fein[0]]
            grob = [(lat, lon) for lon, lat in fein]
            alt = engine.import_fields([Umriss("AuingerBaum1", grob)])[0]
            antwort = c.post("/api/myterra/schlaege", json={"schlaege": [
                {"name": "Auinger Baum 1 / Nr. 11", "geo": {"type": "Polygon", "coordinates": [fein_ring]}}]}).json()["data"]
            self.assertEqual(antwort["umbenannt"], ["AuingerBaum1 → Auinger Baum 1 / Nr. 11"])
            felder = c.get("/api/fields").json()
            self.assertEqual([(f["name"], f["id"]) for f in felder], [("Auinger Baum 1 / Nr. 11", alt["id"])])

    def test_running_it_again_updates_instead_of_doubling(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            liste = {"schlaege": [_schlag("Roggenäcker / Nr. 1")]}
            erstes = c.post("/api/myterra/schlaege", json=liste).json()["data"]
            zweites = c.post("/api/myterra/schlaege", json=liste).json()["data"]
            self.assertEqual(erstes["neu"], ["Roggenäcker / Nr. 1"])
            self.assertEqual(zweites["neu"], [])
            self.assertEqual(zweites["aktualisiert"], ["Roggenäcker / Nr. 1"])
            self.assertEqual(len(c.get("/api/fields").json()), 1)

    def test_missing_list_is_refused(self):
        with tempfile.TemporaryDirectory() as ordner, self._client(ordner) as c:
            self.assertEqual(c.post("/api/myterra/schlaege", json={}).status_code, 400)


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
