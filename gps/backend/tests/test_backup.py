"""Sicherung: konsistente Kopie, mehrere Ziele, Aufräumen, Zurückspielen."""

import os
import sqlite3
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from agripilot import backup
from agripilot.storage import Storage


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.db = self.root / "daten" / "agripilot.db"
        self.store = Storage(self.db)
        self.store.save_field({"name": "Hinterm Hof", "datum_lat": 48.0, "datum_lon": 11.0})
        self.config = self.root / "config.yaml"
        self.config.write_text("server:\n  port: 8080\n", encoding="utf-8")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_backup_contains_database_and_config(self):
        ziel = self.root / "sicherung"
        ergebnis = backup.sichern(self.db, self.config, [ziel])
        self.assertTrue(ergebnis["ok"])
        datei = ziel / ergebnis["name"]
        with zipfile.ZipFile(datei) as archiv:
            self.assertEqual(sorted(archiv.namelist()), ["agripilot.db", "config.yaml"])

    def test_copy_is_a_readable_database_with_the_data(self):
        ziel = self.root / "sicherung"
        ergebnis = backup.sichern(self.db, self.config, [ziel])
        entpackt = self.root / "entpackt"
        with zipfile.ZipFile(ziel / ergebnis["name"]) as archiv:
            archiv.extractall(entpackt)
        verbindung = sqlite3.connect(entpackt / "agripilot.db")
        try:
            namen = [r[0] for r in verbindung.execute("SELECT name FROM fields")]
        finally:
            verbindung.close()
        self.assertEqual(namen, ["Hinterm Hof"])

    def test_unreachable_target_does_not_stop_the_others(self):
        gut = self.root / "gut"
        kaputt = self.root / "datei"
        kaputt.write_text("ich bin kein Ordner")
        ergebnis = backup.sichern(self.db, self.config, [kaputt / "unter", gut])
        self.assertTrue(ergebnis["ok"])
        self.assertFalse(ergebnis["ziele"][0]["ok"])
        self.assertTrue(ergebnis["ziele"][0]["fehler"])
        self.assertTrue(ergebnis["ziele"][1]["ok"])

    def test_only_the_newest_are_kept(self):
        ziel = self.root / "sicherung"
        for n in range(6):
            backup.sichern(self.db, self.config, [ziel], behalten=3, jetzt=1_700_000_000 + n * 60)
        namen = [p.name for p in backup.liste(ziel)]
        self.assertEqual(len(namen), 3)
        self.assertEqual(namen, sorted(namen, reverse=True))
        self.assertIn(time.strftime("%Y%m%d-%H%M%S", time.localtime(1_700_000_000 + 5 * 60)), namen[0])

    def test_due_when_none_or_old(self):
        ziel = self.root / "sicherung"
        self.assertTrue(backup.faellig(ziel))
        ergebnis = backup.sichern(self.db, self.config, [ziel])
        self.assertFalse(backup.faellig(ziel))
        alt = ziel / ergebnis["name"]
        gestern = time.time() - 25 * 3600
        os.utime(alt, (gestern, gestern))
        self.assertTrue(backup.faellig(ziel))

    def test_restore_brings_back_deleted_data_and_keeps_the_old_state(self):
        ziel = self.root / "sicherung"
        ergebnis = backup.sichern(self.db, self.config, [ziel])
        feld = self.store.list_fields()[0]
        self.store.delete_field(feld["id"])
        self.store.close()
        backup.zurueckspielen(ziel / ergebnis["name"], self.db, self.config)
        self.store = Storage(self.db)
        self.assertEqual([f["name"] for f in self.store.list_fields()], ["Hinterm Hof"])
        self.assertTrue(Path(str(self.db) + ".vorher").exists())

    def test_no_half_written_file_is_left_behind(self):
        ziel = self.root / "sicherung"
        backup.sichern(self.db, self.config, [ziel])
        self.assertEqual([p.name for p in ziel.iterdir() if p.name.endswith(".teil")], [])

    def test_restore_rejects_foreign_zip(self):
        fremd = self.root / "fremd.zip"
        with zipfile.ZipFile(fremd, "w") as archiv:
            archiv.writestr("etwas.txt", "x")
        with self.assertRaises(ValueError):
            backup.zurueckspielen(fremd, self.db, None)


class BackupApiTest(unittest.TestCase):
    def test_post_backup_writes_zip_with_current_data(self):
        import warnings
        from fastapi.testclient import TestClient
        from agripilot import config as config_module
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")
        with tempfile.TemporaryDirectory() as directory:
            usb = Path(directory) / "usb"
            usb.mkdir()
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = directory
            config.server.backup_dir = str(usb)
            config.network.device_id = "pi-test"
            with TestClient(create_app(config)) as client:
                client.post("/api/fields", json={"name": "Sicherungsacker"})
                antwort = client.post("/api/backup")
                self.assertEqual(antwort.status_code, 200)
                status = client.get("/api/backup").json()
                self.assertEqual(len(status["orte"]), 2)
                self.assertTrue(all(o["letzte"] for o in status["orte"]))
            zips = backup.liste(usb)
            self.assertEqual(len(zips), 1)
            entpackt = Path(directory) / "x"
            with zipfile.ZipFile(zips[0]) as archiv:
                archiv.extractall(entpackt)
            verbindung = sqlite3.connect(entpackt / "agripilot.db")
            try:
                namen = [r[0] for r in verbindung.execute("SELECT name FROM fields")]
            finally:
                verbindung.close()
            self.assertEqual(namen, ["Sicherungsacker"])


if __name__ == "__main__":
    unittest.main()
