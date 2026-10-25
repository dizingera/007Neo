"""Isolierte Prüfung von Sicherungen vor ihrer Veröffentlichung."""

import sqlite3
from contextlib import closing
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from agripilot import backup
from agripilot.storage import Storage


class SicherungsPruefungTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "quelle.db"
        store = Storage(self.db)
        store.save_field({"name": "Prüffeld", "datum_lat": 48, "datum_lon": 9})
        store.close()
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("INSERT INTO jobs (id, field_id, device_id, started_at, updated_at) "
                        "VALUES ('auftrag', 'feld', 'tablet', 1, 1)")
            con.execute("INSERT INTO fields (id, name, datum_lat, datum_lon, updated_at, deleted) "
                        "VALUES ('weg', 'Gelöscht', 48, 9, 1, 1)")
            con.execute("INSERT INTO jobs (id, field_id, device_id, started_at, updated_at, deleted) "
                        "VALUES ('weg', 'feld', 'tablet', 1, 1, 1)")
        self.zip = self.root / "pruefung.zip"

    def packen(self, daten=None):
        with zipfile.ZipFile(self.zip, "w", zipfile.ZIP_DEFLATED) as archiv:
            archiv.writestr("agripilot.db", self.db.read_bytes() if daten is None else daten)

    def test_echte_datenbank_zaehlt_aktive_inhalte_und_bleibt_unveraendert(self):
        self.packen()
        vorher = self.db.read_bytes()
        with patch.object(backup, "zurueckspielen", side_effect=AssertionError("Restore")):
            result = backup.pruefen(self.zip)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["felder"], result["auftraege"]), (1, 1))
        self.assertEqual(result["fehler"], "")
        self.assertGreater(result["zeit"], 0)
        self.assertEqual(self.db.read_bytes(), vorher)

    def test_leere_agri_datenbank_ist_gueltig(self):
        leer = self.root / "leer.db"
        Storage(leer).close()
        self.packen(leer.read_bytes())
        result = backup.pruefen(self.zip)
        self.assertTrue(result["ok"])
        self.assertEqual((result["felder"], result["auftraege"]), (0, 0))

    def test_fremde_sqlite_und_fehlende_tabelle_werden_abgelehnt(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("DROP TABLE track_points")
        self.packen()
        self.assertFalse(backup.pruefen(self.zip)["ok"])
        fremd = self.root / "fremd.db"
        with closing(sqlite3.connect(fremd)) as con, con:
            con.execute("CREATE TABLE fremd (id INTEGER)")
        self.packen(fremd.read_bytes())
        self.assertFalse(backup.pruefen(self.zip)["ok"])

    def test_fehlende_kaputte_und_abgeschnittene_archive(self):
        self.assertFalse(backup.pruefen(self.zip)["ok"])
        self.zip.write_bytes(b"kein zip")
        self.assertFalse(backup.pruefen(self.zip)["ok"])
        self.packen()
        self.zip.write_bytes(self.zip.read_bytes()[:100])
        self.assertFalse(backup.pruefen(self.zip)["ok"])

    def test_ungueltige_sqlite_und_beschaedigte_seite(self):
        for daten in (b"keine Datenbank", self.db.read_bytes()[:100],
                      self.db.read_bytes()[:100] + b"x" * (len(self.db.read_bytes()) - 100)):
            with self.subTest(laenge=len(daten)):
                self.packen(daten)
                result = backup.pruefen(self.zip)
                self.assertFalse(result["ok"])
                self.assertTrue(result["fehler"])

    def test_beschaedigte_zip_nutzdaten_werden_abgelehnt(self):
        with zipfile.ZipFile(self.zip, "w", zipfile.ZIP_STORED) as archiv:
            archiv.writestr("agripilot.db", self.db.read_bytes())
        daten = bytearray(self.zip.read_bytes())
        offset = daten.index(b"SQLite format 3")
        daten[offset + 100] ^= 0xff
        self.zip.write_bytes(daten)
        self.assertFalse(backup.pruefen(self.zip)["ok"])

    def test_fehlerhafte_deflate_daten_werden_als_ergebnis_gemeldet(self):
        import zlib
        self.packen()
        with patch.object(backup.shutil, "copyfileobj", side_effect=zlib.error("deflate")):
            result = backup.pruefen(self.zip)
        self.assertFalse(result["ok"])
        self.assertTrue(result["fehler"])

    def test_fehlender_und_mehrdeutiger_datenbankeintrag(self):
        with zipfile.ZipFile(self.zip, "w") as archiv:
            archiv.writestr("anders.db", self.db.read_bytes())
        self.assertFalse(backup.pruefen(self.zip)["ok"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.zip, "w") as archiv:
                archiv.writestr("agripilot.db", self.db.read_bytes())
                archiv.writestr("agripilot.db", b"fremd")
        self.assertFalse(backup.pruefen(self.zip)["ok"])

    def test_fremde_pfade_und_konfiguration_werden_nicht_entpackt_oder_gelesen(self):
        self.packen()
        with zipfile.ZipFile(self.zip, "a") as archiv:
            archiv.writestr("../ausbruch.txt", "fremd")
            archiv.writestr("config.yaml", "nicht lesen")
        original = zipfile.ZipFile.open
        gelesen = []
        def lesen(archiv, name, *args, **kwargs):
            gelesen.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
            return original(archiv, name, *args, **kwargs)
        with patch.object(zipfile.ZipFile, "extractall", side_effect=AssertionError("extractall")), \
             patch.object(zipfile.ZipFile, "extract", side_effect=AssertionError("extract")), \
             patch.object(zipfile.ZipFile, "open", lesen):
            self.assertTrue(backup.pruefen(self.zip)["ok"])
        self.assertEqual(gelesen, ["agripilot.db"])
        self.assertFalse((self.root / "ausbruch.txt").exists())

    def test_sichern_liefert_pruefung_mit_zaehlungen(self):
        ziel = self.root / "ziel"
        result = backup.sichern(self.db, None, [ziel])
        self.assertTrue(result["ok"])
        self.assertTrue(result["pruefung"]["ok"])
        self.assertEqual(result["pruefung"]["felder"], 1)
        self.assertEqual(result["pruefung"]["auftraege"], 1)
        self.assertTrue((ziel / result["name"]).is_file())

    def test_schlechte_kopie_wird_nicht_verteilt_und_alte_bleiben(self):
        ziel = self.root / "ziel"
        ziel.mkdir()
        alt = ziel / "agripilot-20000101-000000.zip"
        alt.write_bytes(b"vorhanden")
        neu = self.root / "nicht-anlegen"
        def kaputt(quelle, kopie):
            kopie.write_bytes(b"kaputte Kopie")
        with patch.object(backup, "_datenbank_kopieren", kaputt), \
             patch.object(backup, "aufraeumen", side_effect=AssertionError("Aufräumen")):
            result = backup.sichern(self.db, None, [ziel, neu], behalten=1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["pruefung"]["ok"])
        self.assertEqual(len(result["ziele"]), 2)
        self.assertTrue(all(not z["ok"] and z["fehler"] for z in result["ziele"]))
        self.assertEqual(list(ziel.iterdir()), [alt])
        self.assertEqual(alt.read_bytes(), b"vorhanden")
        self.assertFalse(neu.exists())


if __name__ == "__main__":
    unittest.main()
