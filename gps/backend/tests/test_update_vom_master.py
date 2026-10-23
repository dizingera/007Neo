"""Arbeiter holen das Update vom Master: er gibt sein eigenes Programm heraus."""

import hashlib
import io
import tempfile
import unittest
import urllib.error
import warnings
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from agripilot import __version__ as VERSION
from agripilot import config as config_module
from agripilot import onlineupdate

TUNNEL = {"cf-connecting-ip": "203.0.113.7", "cf-ray": "8a1b2c3d4e5f-FRA"}
CODE = "K7M2-9PQX"
INHALT = b"MZ" + b"AgriPilot" * 5000


class _Antwort(io.BytesIO):
    def __init__(self, daten, kopf):
        super().__init__(daten)
        self.headers = kopf

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class UpdateVomMasterTests(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore")
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.exe = Path(self.dir.name) / "AgriPilot.exe"
        self.exe.write_bytes(INHALT)
        for ziel, wert in (("agripilot.update.als_programm", lambda: True),
                           ("agripilot.server.sys.executable", str(self.exe))):
            p = mock.patch(ziel, wert)
            p.start()
            self.addCleanup(p.stop)

    def _master(self, token=CODE):
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.server.data_dir = self.dir.name
        config.server.update_online = False
        config.network.device_id = "buero"
        config.network.role = "master"
        config.network.sync_token = token
        return TestClient(create_app(config))

    def _ueber(self, client, extra_kopf=None):
        """urlopen, das statt ins Netz an den Test-Master geht."""
        def urlopen(anfrage, timeout=None):
            kopf = {**dict(anfrage.header_items()), **(extra_kopf or {})}
            antwort = client.get(urlsplit(anfrage.full_url).path, headers=kopf)
            if antwort.status_code >= 400:
                raise urllib.error.HTTPError(anfrage.full_url, antwort.status_code, "", {}, None)
            return _Antwort(antwort.content, antwort.headers)
        return mock.patch("agripilot.onlineupdate.urllib.request.urlopen", urlopen)

    def test_the_master_offers_itself(self):
        with self._master() as c:
            antwort = c.get("/api/sync/setup", headers={"X-AgriPilot-Token": CODE})
            self.assertEqual(antwort.status_code, 200)
            daten = antwort.json()
            self.assertEqual(daten["version"], VERSION)
            self.assertEqual(daten["groesse"], len(INHALT))
            self.assertEqual(daten["sha256"], hashlib.sha256(INHALT).hexdigest())
            datei = c.get("/api/sync/setup/datei", headers={"X-AgriPilot-Token": CODE})
            self.assertEqual(datei.content, INHALT)

    def test_without_the_code_there_is_nothing(self):
        with self._master() as c:
            self.assertEqual(c.get("/api/sync/setup").status_code, 401)
            self.assertEqual(c.get("/api/sync/setup/datei",
                                   headers={"X-AgriPilot-Token": "falsch"}).status_code, 401)

    def test_through_the_tunnel_with_the_code_it_works(self):
        with self._master() as c:
            kopf = {**TUNNEL, "X-AgriPilot-Token": CODE}
            self.assertEqual(c.get("/api/sync/setup", headers=kopf).status_code, 200)
            self.assertEqual(c.get("/api/sync/setup/datei", headers=kopf).status_code, 200)
            # Die Bedienung bleibt von außen gesperrt.
            self.assertEqual(c.get("/api/update", headers=kopf).status_code, 403)

    def test_a_worker_finds_and_loads_the_newer_version(self):
        with self._master() as c, self._ueber(c, TUNNEL):
            ergebnis, info = onlineupdate.vom_master("1.0.0", "https://hof.example", CODE)
            self.assertEqual(ergebnis["fehler"], "")
            self.assertTrue(ergebnis["verfuegbar"])
            self.assertEqual(ergebnis["neu"], VERSION)
            pfad = onlineupdate.herunterladen(info, Path(self.dir.name) / "update")
        self.assertEqual(pfad.read_bytes(), INHALT)

    def test_same_version_is_not_offered(self):
        with self._master() as c, self._ueber(c):
            ergebnis, _ = onlineupdate.vom_master(VERSION, "http://hof.example", CODE)
        self.assertFalse(ergebnis["verfuegbar"])
        self.assertEqual(ergebnis["fehler"], "")

    def test_a_wrong_code_is_told(self):
        with self._master() as c, self._ueber(c):
            ergebnis, info = onlineupdate.vom_master("1.0.0", "http://hof.example", "falsch")
        self.assertIsNone(info)
        self.assertIn("Arbeitercode", ergebnis["fehler"])

    def test_a_worker_asks_its_master_before_github(self):
        from agripilot.server import create_app
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.server.data_dir = self.dir.name
        config.network.role = "client"
        config.network.master_url = "https://hof.example"
        config.network.sync_token = CODE
        app = create_app(config).state.app
        self.addCleanup(app.store.close)
        neu = ({"verfuegbar": True, "neu": "9.9.9", "fehler": "", "quelle": "Master"}, {"version": "9.9.9"})
        with mock.patch("agripilot.onlineupdate.vom_master", return_value=neu) as m, \
                mock.patch("agripilot.onlineupdate.suche") as github:
            ergebnis = app.online_pruefen()
        m.assert_called_once_with(VERSION, "https://hof.example", CODE)
        github.assert_not_called()
        self.assertEqual(ergebnis["quelle"], "Master")
        self.assertEqual(app.online_info, {"version": "9.9.9"})

    def test_no_master_address_no_attempt(self):
        ergebnis, info = onlineupdate.vom_master("1.0.0", "", CODE)
        self.assertIsNone(info)
        self.assertTrue(ergebnis["fehler"])


class MasterAusDemQuelltextTests(unittest.TestCase):
    """Der Pi läuft aus dem Quelltext: er gibt das abgelegte Setup heraus."""

    def setUp(self):
        warnings.filterwarnings("ignore")
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch("agripilot.update.als_programm", lambda: False)
        p.start()
        self.addCleanup(p.stop)
        self.ordner = Path(self.dir.name) / "setup"

    _master = UpdateVomMasterTests._master
    _ueber = UpdateVomMasterTests._ueber

    def _ablegen(self, name, inhalt=INHALT):
        self.ordner.mkdir(exist_ok=True)
        (self.ordner / name).write_bytes(inhalt)

    def test_without_a_file_there_is_nothing_to_hand_out(self):
        with self._master() as c:
            antwort = c.get("/api/sync/setup", headers={"X-AgriPilot-Token": CODE})
            self.assertEqual(antwort.status_code, 404)
            self.assertIn("ablegen", antwort.json()["detail"])

    def test_the_newest_file_wins_by_number_not_by_letter(self):
        self._ablegen("AgriPilot-Setup-1.5.9.exe", b"MZalt")
        self._ablegen("AgriPilot-Setup-1.5.10.exe")
        self._ablegen("AgriPilot-Setup-9.9.9.exe.teil", b"halb")
        self._ablegen("notizen.txt", b"x")
        with self._master() as c:
            daten = c.get("/api/sync/setup", headers={"X-AgriPilot-Token": CODE}).json()
            self.assertEqual(daten["version"], "1.5.10")
            self.assertEqual(daten["sha256"], hashlib.sha256(INHALT).hexdigest())
            datei = c.get("/api/sync/setup/datei", headers={"X-AgriPilot-Token": CODE})
            self.assertEqual(datei.content, INHALT)
            self.assertIn("1.5.10", datei.headers["content-disposition"])
            self.assertEqual(c.get("/api/sync/setup").status_code, 401)

    def test_a_worker_gets_it_through_the_tunnel(self):
        self._ablegen("AgriPilot-Setup-99.0.0.exe")
        with self._master() as c, self._ueber(c, TUNNEL):
            ergebnis, info = onlineupdate.vom_master(VERSION, "https://agripilot.example", CODE)
            self.assertTrue(ergebnis["verfuegbar"])
            self.assertEqual(ergebnis["neu"], "99.0.0")
            pfad = onlineupdate.herunterladen(info, Path(self.dir.name) / "update")
        self.assertEqual(pfad.read_bytes(), INHALT)

    def test_a_missing_folder_is_not_an_error(self):
        self.assertIsNone(onlineupdate.abgelegtes_setup(self.ordner / "gibt-es-nicht"))


if __name__ == "__main__":
    unittest.main()
