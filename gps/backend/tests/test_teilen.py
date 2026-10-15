"""Master -> Arbeiter: Maschinen im Abgleich und das Datenpaket als Datei."""

import base64
import io
import json
import os
import tempfile
import time
import unittest
import warnings
import zipfile

from agripilot import config as config_module
from agripilot import teilen
from agripilot.engine import Engine
from agripilot.storage import Storage


def _geraet(verzeichnis, name):
    store = Storage(os.path.join(verzeichnis, f"{name}.db"))
    config = config_module.load("/kein-solcher-pfad.yaml")
    config.network.device_id = name
    config.network.device_name = name
    return store, Engine(config, store)


class GeraeteTestFall(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.master_store, self.master = _geraet(self.dir.name, "master")
        self.arbeiter_store, self.arbeiter = _geraet(self.dir.name, "arbeiter")
        self.addCleanup(self.master_store.close)
        self.addCleanup(self.arbeiter_store.close)

    def _namen(self, engine):
        return sorted(p["name"] for p in engine.list_profiles())


class MaschinenTests(GeraeteTestFall):
    def test_the_automatic_default_machine_does_not_multiply(self):
        self.arbeiter.profile_merge(self.master.profile_export())
        self.master.profile_merge(self.arbeiter.profile_export())
        self.assertEqual(len(self.master.list_profiles()), 1)
        self.assertEqual(len(self.arbeiter.list_profiles()), 1)

    def test_a_machine_made_on_the_master_arrives_on_the_worker(self):
        self.master.save_profile_as("Fendt 724", {"width_m": 24.0, "wheelbase_m": 2.9})
        ergebnis = self.arbeiter.profile_merge(self.master.profile_export())
        self.assertIn("Fendt 724", self._namen(self.arbeiter))
        self.assertGreaterEqual(ergebnis["neu"], 1)
        fendt = [p for p in self.arbeiter.list_profiles() if p["name"] == "Fendt 724"][0]
        self.assertEqual(fendt["width_m"], 24.0)

    def test_the_selected_machine_on_the_worker_stays_selected(self):
        aktiv_vorher = [p["id"] for p in self.arbeiter.list_profiles() if p["aktiv"]][0]
        self.master.save_profile_as("Fendt 724")
        self.arbeiter.profile_merge(self.master.profile_export())
        aktiv_nachher = [p["id"] for p in self.arbeiter.list_profiles() if p["aktiv"]][0]
        self.assertEqual(aktiv_vorher, aktiv_nachher)

    def test_the_newer_version_of_a_machine_wins_in_both_directions(self):
        self.master.save_profile_as("Fendt", {"width_m": 6.0})
        self.arbeiter.profile_merge(self.master.profile_export())
        time.sleep(0.01)
        # Der Fahrer misst auf dem Traktor die Antennenhöhe neu ...
        fendt_id = [p["id"] for p in self.arbeiter.list_profiles() if p["name"] == "Fendt"][0]
        self.arbeiter.select_profile(fendt_id)
        self.arbeiter.update_profile({"antenna_height_m": 3.3})
        self.master.profile_merge(self.arbeiter.profile_export())          # ... und das geht zum Master
        master_fendt = [p for p in self.master.list_profiles() if p["name"] == "Fendt"][0]
        self.assertEqual(master_fendt["antenna_height_m"], 3.3)
        # Ein älterer Stand überschreibt das nicht.
        alt = {"maschinen": [{**master_fendt, "antenna_height_m": 1.0, "stand": 1.0}], "geloescht": {}}
        self.master.profile_merge(alt)
        master_fendt = [p for p in self.master.list_profiles() if p["name"] == "Fendt"][0]
        self.assertEqual(master_fendt["antenna_height_m"], 3.3)

    def test_selecting_a_machine_does_not_make_it_look_newer(self):
        self.master.save_profile_as("Fendt")
        stand_vorher = [p["stand"] for p in self.master.list_profiles() if p["name"] == "Fendt"][0]
        time.sleep(0.01)
        andere = [p["id"] for p in self.master.list_profiles() if p["name"] != "Fendt"][0]
        self.master.select_profile(andere)
        fendt_id = [p["id"] for p in self.master.list_profiles() if p["name"] == "Fendt"][0]
        self.master.select_profile(fendt_id)
        stand_nachher = [p["stand"] for p in self.master.list_profiles() if p["name"] == "Fendt"][0]
        self.assertEqual(stand_vorher, stand_nachher)

    def test_a_deleted_machine_stays_deleted(self):
        self.master.save_profile_as("Alt")
        self.arbeiter.profile_merge(self.master.profile_export())
        alt_id = [p["id"] for p in self.master.list_profiles() if p["name"] == "Alt"][0]
        time.sleep(0.01)
        self.master.delete_profile(alt_id)
        self.arbeiter.profile_merge(self.master.profile_export())
        self.assertNotIn("Alt", self._namen(self.arbeiter))
        # Und ein später eintreffender, älterer Stand bringt sie nicht zurück.
        self.master.profile_merge({"maschinen": [{"id": alt_id, "name": "Alt", "stand": 1.0}],
                                   "geloescht": {}})
        self.assertNotIn("Alt", self._namen(self.master))

    def test_the_last_machine_is_never_deleted_by_a_merge(self):
        nur = self.arbeiter.list_profiles()[0]["id"]
        self.arbeiter.profile_merge({"maschinen": [], "geloescht": {nur: time.time() + 10}})
        self.assertEqual(len(self.arbeiter.list_profiles()), 1)

    def test_changed_values_of_the_selected_machine_are_loaded(self):
        self.master.save_profile_as("Fendt", {"width_m": 6.0})
        self.arbeiter.profile_merge(self.master.profile_export())
        fendt_id = [p["id"] for p in self.arbeiter.list_profiles() if p["name"] == "Fendt"][0]
        self.arbeiter.select_profile(fendt_id)
        time.sleep(0.01)
        master_id = [p["id"] for p in self.master.list_profiles() if p["name"] == "Fendt"][0]
        self.master.select_profile(master_id)
        self.master.update_profile({"width_m": 12.0})
        self.arbeiter.profile_merge(self.master.profile_export())
        self.assertEqual(self.arbeiter.profile.width_m, 12.0)        # die laufende Maschine, sofort

    def test_garbage_in_the_machine_list_is_ignored(self):
        self.arbeiter.profile_merge({"maschinen": ["x", {"name": "ohne Kennung"}, None,
                                                   {"id": "k1", "name": "Gut", "unbekannt": 1, "stand": 5}]})
        self.assertIn("Gut", self._namen(self.arbeiter))
        self.assertNotIn("ohne Kennung", self._namen(self.arbeiter))


class PaketTests(GeraeteTestFall):
    def _master_mit_daten(self):
        feld = self.master_store.save_field({
            "name": "Hinterm Hof", "datum_lat": 48.0, "datum_lon": 11.0,
            "boundary": [[0, 0], [100, 0], [100, 100], [0, 100]]})
        self.master_store.save_line({"field_id": feld["id"], "name": "Nord", "mode": "ab",
                                     "points": [[10, 0], [10, 100]], "spacing_m": 6.0})
        self.master.save_profile_as("Fendt 724", {"width_m": 24.0})
        return feld

    def test_field_line_and_machine_travel_in_a_file(self):
        feld = self._master_mit_daten()
        paket = teilen.paket_bauen(self.master_store, self.master, "Büro-PC")
        ergebnis = teilen.paket_einspielen(self.arbeiter_store, self.arbeiter, paket)
        self.assertEqual(ergebnis["von"], "Büro-PC")
        self.assertEqual(ergebnis["felder"], 1)
        self.assertEqual(ergebnis["spuren"], 1)
        angekommen = self.arbeiter_store.get_field(feld["id"])
        self.assertEqual(angekommen["name"], "Hinterm Hof")
        self.assertEqual(len(angekommen["boundary"]), 4)
        self.assertEqual(len(self.arbeiter_store.list_lines(feld["id"])), 1)
        self.assertIn("Fendt 724", self._namen(self.arbeiter))

    def test_importing_twice_changes_nothing(self):
        self._master_mit_daten()
        paket = teilen.paket_bauen(self.master_store, self.master)
        teilen.paket_einspielen(self.arbeiter_store, self.arbeiter, paket)
        zweites = teilen.paket_einspielen(self.arbeiter_store, self.arbeiter, paket)
        self.assertEqual(zweites["felder"], 0)
        self.assertEqual(len(self.arbeiter_store.list_fields()), 1)

    def test_a_newer_change_on_the_worker_is_not_overwritten(self):
        feld = self._master_mit_daten()
        paket = teilen.paket_bauen(self.master_store, self.master)
        teilen.paket_einspielen(self.arbeiter_store, self.arbeiter, paket)
        time.sleep(0.01)
        self.arbeiter_store.save_field({**self.arbeiter_store.get_field(feld["id"]),
                                        "name": "Hinterm Hof (Nord)"})
        teilen.paket_einspielen(self.arbeiter_store, self.arbeiter, paket)
        self.assertEqual(self.arbeiter_store.get_field(feld["id"])["name"], "Hinterm Hof (Nord)")

    def test_a_newer_master_version_replaces_the_worker_copy(self):
        feld = self._master_mit_daten()
        teilen.paket_einspielen(self.arbeiter_store, self.arbeiter,
                                teilen.paket_bauen(self.master_store, self.master))
        time.sleep(0.01)
        self.master_store.save_field({**self.master_store.get_field(feld["id"]),
                                      "boundary": [[0, 0], [200, 0], [200, 100], [0, 100]]})
        teilen.paket_einspielen(self.arbeiter_store, self.arbeiter,
                                teilen.paket_bauen(self.master_store, self.master))
        self.assertEqual(self.arbeiter_store.get_field(feld["id"])["boundary"][1], [200, 0])

    def test_jobs_only_travel_when_asked(self):
        feld = self._master_mit_daten()
        job = self.master_store.start_job(feld["id"], "master", "Fendt", "Säen")
        ohne = json.loads(zipfile.ZipFile(io.BytesIO(
            teilen.paket_bauen(self.master_store, self.master))).read("daten.json"))
        mit = json.loads(zipfile.ZipFile(io.BytesIO(
            teilen.paket_bauen(self.master_store, self.master, mit_arbeiten=True))).read("daten.json"))
        self.assertNotIn("jobs", ohne["tabellen"])
        self.assertEqual([j["id"] for j in mit["tabellen"]["jobs"]], [job["id"]])

    def test_devices_never_travel(self):
        self.master_store.touch_device("master", "Büro-PC", "master")
        inhalt = json.loads(zipfile.ZipFile(io.BytesIO(
            teilen.paket_bauen(self.master_store, self.master))).read("daten.json"))
        self.assertNotIn("devices", inhalt["tabellen"])

    def test_bad_files_are_refused_with_a_reason(self):
        def zip_mit(name, inhalt):
            puffer = io.BytesIO()
            with zipfile.ZipFile(puffer, "w") as z:
                z.writestr(name, inhalt)
            return puffer.getvalue()

        faelle = [
            (b"", "Leer"),
            (b"das ist keine zip", "keine Zip"),
            (zip_mit("etwas.txt", "x"), "daten.json fehlt"),
            (zip_mit("daten.json", "{kaputt"), "nicht lesbar"),
            (zip_mit("daten.json", json.dumps({"format": "fremd"})), "kein AgriPilot"),
            (zip_mit("daten.json", json.dumps({"format": "agripilot-daten", "version": 99})), "neueren Version"),
        ]
        for daten, stichwort in faelle:
            with self.assertRaises(teilen.PaketFehler) as fehler:
                teilen.paket_einspielen(self.arbeiter_store, self.arbeiter, daten)
            self.assertIn(stichwort, str(fehler.exception), stichwort)
        self.assertEqual(self.arbeiter_store.list_fields(), [])

    def test_unknown_tables_in_a_package_are_ignored(self):
        puffer = io.BytesIO()
        with zipfile.ZipFile(puffer, "w") as z:
            z.writestr("daten.json", json.dumps({
                "format": "agripilot-daten", "version": 1,
                "tabellen": {"settings": [{"key": "x", "value": "1"}], "devices": [{"id": "x"}]}}))
        ergebnis = teilen.paket_einspielen(self.arbeiter_store, self.arbeiter, puffer.getvalue())
        self.assertEqual(ergebnis["felder"], 0)
        self.assertEqual(self.arbeiter_store.list_devices(), [])


class ServerTests(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore")
        self.dirs = [tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()]
        for d in self.dirs:
            self.addCleanup(d.cleanup)

    def _client(self, nr, rolle="master"):
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.server.data_dir = self.dirs[nr].name
        config.network.device_id = f"geraet{nr}"
        config.network.device_name = ["Büro-PC", "Tablet"][nr]
        config.network.role = rolle
        return TestClient(create_app(config))

    def test_export_and_import_through_the_interface(self):
        with self._client(0) as master, self._client(1, "client") as tablet:
            master.post("/api/fields", json={"name": "Hinterm Hof"})
            master.post("/api/profiles", json={"name": "Fendt 724", "values": {"width_m": 24.0}})
            antwort = master.get("/api/daten/export")
            self.assertEqual(antwort.status_code, 200)
            self.assertIn("agripilot-daten-", antwort.headers["content-disposition"])
            eingespielt = tablet.post("/api/daten/import",
                                      json={"zip": base64.b64encode(antwort.content).decode()})
            self.assertEqual(eingespielt.status_code, 200, eingespielt.text)
            daten = eingespielt.json()["data"]
            self.assertEqual(daten["von"], "Büro-PC")
            self.assertEqual(daten["felder"], 1)
            self.assertEqual([f["name"] for f in tablet.get("/api/fields").json()["fields"]]
                             if isinstance(tablet.get("/api/fields").json(), dict)
                             else [f["name"] for f in tablet.get("/api/fields").json()], ["Hinterm Hof"])
            maschinen = tablet.get("/api/profiles").json()
            namen = [m["name"] for m in (maschinen["profiles"] if isinstance(maschinen, dict) else maschinen)]
            self.assertIn("Fendt 724", namen)

    def test_a_bad_file_gives_a_readable_error(self):
        with self._client(0) as master:
            antwort = master.post("/api/daten/import",
                                  json={"zip": base64.b64encode(b"kein zip").decode()})
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("Zip", antwort.json()["detail"])

    def test_the_sync_endpoint_carries_machines_both_ways(self):
        with self._client(0) as master:
            master.post("/api/profiles", json={"name": "Fendt 724"})
            vom_tablet = {"id": "t1", "name": "Alt-Traktor", "stand": time.time(), "width_m": 9.0}
            antwort = master.post("/api/sync", json={
                "device": {"id": "tablet", "name": "Tablet", "role": "client"}, "since": 0,
                "changes": {}, "maschinen": {"maschinen": [vom_tablet], "geloescht": {}}}).json()
            namen = [m["name"] for m in antwort["maschinen"]["maschinen"]]
            self.assertIn("Fendt 724", namen)        # Master -> Arbeiter
            self.assertIn("Alt-Traktor", namen)      # Arbeiter -> Master

    def test_the_system_status_names_the_address_for_the_workers(self):
        with self._client(0) as master:
            adressen = master.get("/api/system").json()["adressen"]
        self.assertIsInstance(adressen, list)
        for adresse in adressen:
            self.assertRegex(adresse, r"^http://\d+\.\d+\.\d+\.\d+:\d+$")


if __name__ == "__main__":
    unittest.main()
