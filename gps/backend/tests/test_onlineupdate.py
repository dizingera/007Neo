"""Online-Aktualisierung gegen einen kleinen lokalen Server, der GitHub nachspielt."""

import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from agripilot import onlineupdate as ou

EXE = b"MZ" + b"\x00" * 5000


def _release(tag, basis, mit_datei=True, **extra):
    r = {"tag_name": tag, "draft": False, "prerelease": False, "body": f"Notizen {tag}",
         "assets": []}
    if mit_datei:
        r["assets"].append({"name": "AgriPilot-Setup.exe", "size": len(EXE),
                            "browser_download_url": f"{basis}/datei/{tag}.exe",
                            "digest": "sha256:" + hashlib.sha256(EXE).hexdigest()})
    r.update(extra)
    return r


class FalschesGitHub:
    """Spielt /repos/x/y/releases und die Dateien nach."""

    def __init__(self):
        self.releases = []
        self.datei = EXE
        self.status = 200
        self.pruefsummen = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.end_headers()
                    return
                if "/releases" in self.path:
                    daten = json.dumps(outer.releases).encode()
                elif self.path.endswith(".sha256"):
                    daten = outer.pruefsummen.get(self.path, b"").encode() if isinstance(
                        outer.pruefsummen.get(self.path, ""), str) else b""
                else:
                    daten = outer.datei
                self.send_response(200)
                self.send_header("Content-Length", str(len(daten)))
                self.end_headers()
                self.wfile.write(daten)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.basis = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class VersionsvergleichTests(unittest.TestCase):
    def test_versions_are_compared_numerically(self):
        self.assertGreater(ou.version_zahlen("agripilot-v1.10.0"), ou.version_zahlen("1.9.3"))
        self.assertEqual(ou.version_zahlen("v1.2"), (1, 2))
        self.assertEqual(ou.version_zahlen("kaputt"), ())


class NeuesteTests(unittest.TestCase):
    def setUp(self):
        self.gh = FalschesGitHub()
        self.addCleanup(self.gh.stop)

    def _pruefen(self, aktuell="1.1.0"):
        return ou.pruefen(aktuell, "x/y", self.gh.basis)

    def test_picks_the_highest_release_of_this_program(self):
        b = self.gh.basis
        self.gh.releases = [_release("agripilot-v1.2.0", b), _release("agripilot-v1.10.0", b),
                            _release("agripilot-v1.9.0", b)]
        erg = self._pruefen()
        self.assertEqual(erg["neu"], "1.10.0")
        self.assertTrue(erg["verfuegbar"])
        self.assertEqual(erg["notizen"], "Notizen agripilot-v1.10.0")

    def test_other_projects_drafts_and_prereleases_are_ignored(self):
        b = self.gh.basis
        self.gh.releases = [_release("myterra-v9.0.0", b), _release("agripilot-v3.0.0", b, draft=True),
                            _release("agripilot-v2.0.0", b, prerelease=True),
                            _release("agripilot-v1.5.0", b, mit_datei=False),
                            _release("agripilot-v1.2.0", b)]
        self.assertEqual(self._pruefen()["neu"], "1.2.0")

    def test_same_or_older_version_is_not_available(self):
        self.gh.releases = [_release("agripilot-v1.1.0", self.gh.basis)]
        erg = self._pruefen("1.1.0")
        self.assertEqual(erg["neu"], "1.1.0")
        self.assertFalse(erg["verfuegbar"])

    def test_no_release_at_all(self):
        self.gh.releases = []
        erg = self._pruefen()
        self.assertIsNone(erg["neu"])
        self.assertFalse(erg["verfuegbar"])
        self.assertEqual(erg["fehler"], "")

    def test_errors_become_text_not_exceptions(self):
        self.gh.status = 403
        self.assertIn("Begrenzung", self._pruefen()["fehler"])
        self.gh.status = 500
        self.assertIn("500", self._pruefen()["fehler"])
        self.assertIn("Verbindung", ou.pruefen("1.1.0", "x/y", "http://127.0.0.1:9")["fehler"])


class HerunterladenTests(unittest.TestCase):
    def setUp(self):
        self.gh = FalschesGitHub()
        self.addCleanup(self.gh.stop)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.gh.releases = [_release("agripilot-v1.2.0", self.gh.basis)]

    def _info(self):
        return ou.neueste("x/y", self.gh.basis)

    def test_a_good_download_is_verified_and_kept(self):
        fortschritt = []
        pfad = ou.herunterladen(self._info(), Path(self.dir.name),
                                fortschritt=lambda a, b: fortschritt.append((a, b)))
        self.assertEqual(pfad.read_bytes(), EXE)
        self.assertEqual(pfad.name, "AgriPilot-Setup-1.2.0.exe")
        self.assertEqual(fortschritt[-1][0], len(EXE))
        self.assertEqual([p.name for p in Path(self.dir.name).glob("*.teil")], [])

    def test_a_wrong_checksum_is_rejected_and_nothing_is_left(self):
        info = self._info()
        self.gh.datei = b"MZ" + b"\x01" * 5000        # verstümmelt
        with self.assertRaises(ou.OnlineFehler) as fehler:
            ou.herunterladen(info, Path(self.dir.name))
        self.assertIn("Prüfsumme", str(fehler.exception))
        self.assertEqual(list(Path(self.dir.name).iterdir()), [])

    def test_a_file_that_is_not_a_program_is_rejected(self):
        daten = b"<html>Fehlerseite</html>" + b"x" * 100
        self.gh.datei = daten
        info = self._info()
        info["sha256"] = hashlib.sha256(daten).hexdigest()
        with self.assertRaises(ou.OnlineFehler) as fehler:
            ou.herunterladen(info, Path(self.dir.name))
        self.assertIn("kein Windows-Programm", str(fehler.exception))

    def test_no_checksum_means_no_install(self):
        info = self._info()
        info["sha256"] = ""
        info["pruefsumme_url"] = ""
        with self.assertRaises(ou.OnlineFehler):
            ou.herunterladen(info, Path(self.dir.name))

    def test_checksum_from_a_separate_file_works(self):
        info = self._info()
        info["sha256"] = ""
        info["pruefsumme_url"] = f"{self.gh.basis}/datei/x.sha256"
        self.gh.pruefsummen["/datei/x.sha256"] = hashlib.sha256(EXE).hexdigest() + "  AgriPilot-Setup.exe\n"
        self.assertTrue(ou.herunterladen(info, Path(self.dir.name)).exists())


class ServerTests(unittest.TestCase):
    """Die Wege durch die Anwendung: suchen, Sperren, laden und einspielen."""

    def setUp(self):
        import warnings
        from unittest import mock
        warnings.filterwarnings("ignore")
        self.gh = FalschesGitHub()
        self.addCleanup(self.gh.stop)
        self.gh.releases = [_release("agripilot-v9.0.0", self.gh.basis)]
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.mock = mock

    def _client(self):
        from fastapi.testclient import TestClient
        from agripilot import config as config_module
        from agripilot.server import create_app
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.server.data_dir = self.dir.name
        config.server.update_api = self.gh.basis
        config.server.update_repo = "x/y"
        config.network.device_id = "pi-test"
        return TestClient(create_app(config))

    @staticmethod
    def _halten(client):
        """Der Simulator fährt von allein; aktualisiert wird nur im Stand."""
        import time
        client.app.state.app.source.set_speed_kmh(0.0)
        time.sleep(0.6)

    def test_checking_reports_the_new_version(self):
        with self._client() as client:
            antwort = client.get("/api/update/online").json()
        self.assertTrue(antwort["verfuegbar"])
        self.assertEqual(antwort["neu"], "9.0.0")
        self.assertFalse(antwort["als_programm"])

    def test_installing_is_refused_outside_the_exe_version(self):
        with self._client() as client:
            antwort = client.post("/api/update/online")
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("Programmversion", antwort.json()["detail"])

    def test_installing_is_refused_while_steering_is_armed(self):
        with self.mock.patch("agripilot.update.als_programm", return_value=True), self._client() as client:
            self.mock.patch.object(client.app.state.app.steering, "armed", True).start()
            self.addCleanup(self.mock.patch.stopall)
            antwort = client.post("/api/update/online")
        self.assertEqual(antwort.status_code, 409)
        self.assertIn("Lenkung", antwort.json()["detail"])

    def test_installing_is_refused_while_driving(self):
        import time
        from agripilot import nmea
        with self.mock.patch("agripilot.update.als_programm", return_value=True), self._client() as client:
            motor = client.app.state.app.engine
            fix = nmea.Fix(lat=48.0, lon=11.0, fix_quality=4, speed_ms=2.5, course_deg=0.0)
            fix.received_at = time.time()
            motor.fix = fix
            motor.on_fix = lambda f: None            # der Simulator soll sie nicht überschreiben
            antwort = client.post("/api/update/online")
        self.assertEqual(antwort.status_code, 409)
        self.assertIn("anhalten", antwort.json()["detail"])

    def test_a_good_update_is_downloaded_verified_and_handed_to_the_setup(self):
        gestartet = []
        with self.mock.patch("agripilot.update.als_programm", return_value=True), \
                self.mock.patch("agripilot.exe.setup_starten", side_effect=gestartet.append), \
                self._client() as client:
            self._halten(client)
            antwort = client.post("/api/update/online")
            self.assertEqual(antwort.status_code, 200, antwort.text)
        self.assertEqual(antwort.json()["data"]["version"], "9.0.0")
        self.assertEqual(len(gestartet), 1)
        self.assertEqual(gestartet[0].read_bytes(), EXE)
        self.assertEqual(gestartet[0].parent.name, "update")

    def test_a_corrupt_download_is_not_started(self):
        self.gh.datei = b"MZ" + b"\x01" * 5000
        gestartet = []
        with self.mock.patch("agripilot.update.als_programm", return_value=True), \
                self.mock.patch("agripilot.exe.setup_starten", side_effect=gestartet.append), \
                self._client() as client:
            self._halten(client)
            antwort = client.post("/api/update/online")
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("Prüfsumme", antwort.json()["detail"])
        self.assertEqual(gestartet, [])

    def test_already_up_to_date_does_nothing(self):
        self.gh.releases = [_release("agripilot-v0.1.0", self.gh.basis)]
        gestartet = []
        with self.mock.patch("agripilot.update.als_programm", return_value=True), \
                self.mock.patch("agripilot.exe.setup_starten", side_effect=gestartet.append), \
                self._client() as client:
            self._halten(client)
            antwort = client.post("/api/update/online")
        self.assertTrue(antwort.json()["data"]["schon_aktuell"])
        self.assertEqual(gestartet, [])


if __name__ == "__main__":
    unittest.main()
