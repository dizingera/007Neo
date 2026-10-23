"""Fahrgassen der Aussaat über den Büro-Master für alle Tablets erhalten."""

from contextlib import ExitStack
import tempfile
import time
import unittest
from unittest import mock

from test_tablet_lenkung import abgleichen, geraet


class FahrgassenAbgleichTests(unittest.TestCase):
    def setUp(self):
        self.ressourcen = ExitStack()
        self.addCleanup(self.ressourcen.close)
        self.master, self.a, self.b = [
            self.ressourcen.enter_context(geraet(
                self.ressourcen.enter_context(tempfile.TemporaryDirectory()), rolle, name))
            for rolle, name in [("master", "buero"), ("client", "tablet-a"), ("client", "tablet-b")]
        ]
        self.motor_a = self.a.app.state.app.engine
        self.motor_b = self.b.app.state.app.engine
        self.feld = self.motor_a.store.save_field({
            "name": "Winterweizen", "datum_lat": 48.0, "datum_lon": 11.0,
            "boundary": [[0, 0], [300, 0], [300, 150], [0, 150]], "area_ha": 4.5})

    def saat(self, jahr=2027, x=10):
        spur = self.motor_a.store.save_line({
            "field_id": self.feld["id"], "name": "Aussaat", "mode": "ab",
            "points": [[x, 0], [x, 300]], "spacing_m": 3.0,
            "fahrgassen": [-42.0, -21.0, 0.0]})
        antwort = self.a.post(f"/api/lines/{spur['id']}", json={"saisonspur": True, "saison": jahr})
        self.assertEqual(antwort.status_code, 200, antwort.text)
        return antwort.json()["data"]

    def verteilen(self):
        abgleichen(self.a, self.master)
        abgleichen(self.b, self.master)

    def laden_b(self):
        antwort = self.b.post(f"/api/fields/{self.feld['id']}/load")
        self.assertEqual(antwort.status_code, 200, antwort.text)
        return self.b.get("/api/state").json()["line"]

    def test_offline_ausgesaete_fahrgassen_erreichen_b_trotz_fortgeschrittenem_cursor(self):
        saat = self.saat()
        # A hat schon gesät, aber noch nicht an den Master übertragen. B gleicht dazwischen ab.
        abgleichen(self.b, self.master)
        self.verteilen()
        self.assertIsNotNone(self.motor_b.store.get_field(self.feld["id"]))
        erhalten = self.motor_b.store.get_line(saat["id"])
        self.assertIsNotNone(erhalten)
        self.assertEqual(erhalten["fahrgassen"], saat["fahrgassen"])
        self.assertEqual(erhalten["updated_at"], saat["updated_at"])
        self.assertEqual(erhalten["aussaat_at"], saat["aussaat_at"])

    def test_erntejahr_bleibt_bei_kalenderwechsel_und_geraetewechsel(self):
        saat = self.saat(2026)
        self.motor_a.store.save_line({"field_id": self.feld["id"], "name": "Spätere Hilfsspur",
            "mode": "ab", "points": [[99, 0], [99, 300]], "spacing_m": 3.0})
        self.verteilen()
        self.b.post("/api/profile", json={"width_m": 21.0})
        with mock.patch("agripilot.engine.time.localtime", return_value=time.struct_time(
                (2027, 4, 1, 12, 0, 0, 3, 91, -1))):
            spur = self.laden_b()
        self.assertEqual(spur["id"], saat["id"])
        self.assertEqual(spur["saison"], 2026)
        self.assertEqual(spur["fahrgassen"], [-42.0, -21.0, 0.0])
        self.assertEqual(spur["spacing_m"], 21.0)

    def test_umbenennen_alter_spur_veroeffentlicht_keine_neue_aussaat(self):
        alt = self.saat(2026)
        neu = self.saat(2027, 15)
        self.a.post(f"/api/lines/{alt['id']}", json={"name": "Historische Aussaat"})
        self.verteilen()
        self.assertEqual(self.laden_b()["id"], neu["id"])
        self.assertEqual(self.motor_b.store.get_line(alt["id"])["aussaat_at"], alt["aussaat_at"])

    def test_neue_aussaat_im_gleichen_erntejahr_ersetzt_ruhende_spur(self):
        alt = self.saat()
        self.verteilen()
        self.assertEqual(self.laden_b()["id"], alt["id"])
        neu = self.saat(x=30)
        self.verteilen()
        self.assertEqual(self.motor_b.line.id, neu["id"])
        self.assertEqual(self.motor_b.line.points, [(30, 0), (30, 300)])
        self.assertIsNone(self.motor_b.state()["saisonspur_neu"])

    def test_laufende_arbeit_verhindert_automatischen_spurwechsel(self):
        alt = self.saat()
        self.verteilen()
        self.laden_b()
        self.motor_b.start_job("Düngen")
        neu = self.saat(x=40)
        self.verteilen()
        self.assertEqual(self.motor_b.line.id, alt["id"])
        self.assertEqual(self.motor_b.state()["saisonspur_neu"]["id"], neu["id"])
        self.motor_b.stop_job()
        abgleichen(self.b, self.master)
        self.assertEqual(self.motor_b.line.id, neu["id"])

    def test_freigegebene_lenkung_verhindert_automatischen_spurwechsel(self):
        alt = self.saat()
        self.verteilen()
        self.laden_b()
        neu = self.saat(x=50)
        with mock.patch.object(self.motor_b.steering, "armed", True):
            self.verteilen()
            self.assertEqual(self.motor_b.line.id, alt["id"])
            self.assertEqual(self.motor_b.state()["saisonspur_neu"]["id"], neu["id"])
        abgleichen(self.b, self.master)
        self.assertEqual(self.motor_b.line.id, neu["id"])

    def test_saatplan_mit_erntejahr_und_exakter_liste_wird_weitergegeben(self):
        start = self.a.get(f"/api/fields/{self.feld['id']}/saatplan").json()["data"]["optionen"][0]["id"]
        planen = self.a.post(f"/api/fields/{self.feld['id']}/saatplan", json={
            "start": start, "breite_m": 3.0, "fahrgasse_m": 21.0, "randspuren": 0})
        self.assertEqual(planen.status_code, 200, planen.text)
        antwort = self.a.post(f"/api/fields/{self.feld['id']}/saatplan/uebernehmen", json={"saison": 2027})
        self.assertEqual(antwort.status_code, 200, antwort.text)
        saat = antwort.json()["data"]
        self.assertEqual(saat["saison"], 2027)
        self.assertTrue(saat["fahrgassen"])
        self.verteilen()
        self.assertEqual(self.laden_b()["fahrgassen"], saat["fahrgassen"])

    def test_explizite_neue_aussaat_zaehlt_unabhaengig_von_jahreszahl(self):
        self.saat(2027)
        neu = self.saat(2026, 60)
        self.verteilen()
        self.assertEqual(self.laden_b()["id"], neu["id"])

    def test_einmal_empfangene_aussaat_bleibt_nach_neustart_ohne_master(self):
        saat = self.saat()
        self.verteilen()
        from agripilot.engine import Engine
        neu = Engine(self.b.app.state.app.config, self.motor_b.store)
        neu.load_field(self.feld["id"])
        self.assertEqual(neu.line.id, saat["id"])
        self.assertEqual(neu.line.fahrgassen_liste, saat["fahrgassen"])

    def test_ungueltiges_erntejahr_veraendert_keine_aussaat(self):
        saat = self.saat()
        for jahr in [True, 2027.5, "2027.5", 0, 1999, 2201]:
            antwort = self.a.post(f"/api/lines/{saat['id']}", json={"saisonspur": True, "saison": jahr})
            self.assertEqual(antwort.status_code, 400, antwort.text)
            self.assertEqual(self.motor_a.store.get_line(saat["id"])["aussaat_at"], saat["aussaat_at"])

    def test_naechstes_erntejahr_bewahrt_vorherigen_saatplan(self):
        start = self.motor_a.saatplan(self.feld["id"])["optionen"][0]["id"]
        self.motor_a.saatplan_planen(self.feld["id"], {"start": start, "breite_m": 3.0,
            "fahrgasse_m": 21.0, "randspuren": 0})
        vorher = self.motor_a.saatplan_uebernehmen(self.feld["id"], 2026)
        nachher = self.motor_a.saatplan_uebernehmen(self.feld["id"], 2027)
        self.assertEqual(self.motor_a.store.season_line(self.feld["id"], 2026)["id"], vorher["id"])
        self.assertEqual(self.motor_a.store.season_line(self.feld["id"])["id"], nachher["id"])

    def test_altbestand_erhaelt_aussaatzeit_beim_ersten_oeffnen(self):
        import sqlite3
        from pathlib import Path
        from agripilot.storage import Storage
        pfad = Path(self.ressourcen.enter_context(tempfile.TemporaryDirectory())) / "alt.db"
        with sqlite3.connect(pfad) as db:
            db.execute("CREATE TABLE lines (id TEXT PRIMARY KEY, field_id TEXT, name TEXT, mode TEXT, "
                       "points TEXT, spacing_m REAL, nudge_m REAL, saison INTEGER, fahrgasse_m REAL, "
                       "fahrgassen TEXT, updated_at REAL, deleted INTEGER)")
            db.execute("INSERT INTO lines VALUES ('s','f','Saat','ab','[[0,0],[0,100]]',3,0,2026,21,'',123,0)")
        db.close()
        alt = Storage(pfad)
        try:
            self.assertEqual(alt.season_line("f")["aussaat_at"], 123)
            alt.update_line("s", name="Nur umbenannt")
        finally:
            alt.close()
        alt = Storage(pfad)
        try:
            self.assertEqual(alt.season_line("f")["aussaat_at"], 123)
        finally:
            alt.close()

    def test_normaler_spurversatz_bewahrt_gemeinsame_fahrgassen_und_aussaatzeit(self):
        saat = self.saat()
        self.verteilen()
        self.laden_b()
        antwort = self.b.post("/api/guidance/nudge", json={"metres": 0.1})
        self.assertEqual(antwort.status_code, 200, antwort.text)
        abgleichen(self.b, self.master)
        abgleichen(self.a, self.master)
        for client in (self.a, self.b, self.master):
            stand = client.app.state.app.store.get_line(saat["id"])
            self.assertEqual(stand["saison"], saat["saison"])
            self.assertEqual(stand["fahrgasse_m"], saat["fahrgasse_m"])
            self.assertEqual(stand["fahrgassen"], saat["fahrgassen"])
            self.assertEqual(stand["aussaat_at"], saat["aussaat_at"])
            self.assertAlmostEqual(stand["nudge_m"], 0.1)

    def test_zurueckgenommene_neue_aussaat_entfernt_den_wartehinweis(self):
        alt = self.saat()
        self.verteilen()
        self.laden_b()
        self.motor_b.start_job("Spritzen")
        neu = self.saat(x=70)
        self.verteilen()
        self.assertEqual(self.motor_b.state()["saisonspur_neu"]["id"], neu["id"])
        self.a.post(f"/api/lines/{neu['id']}", json={"saisonspur": False})
        self.verteilen()
        self.assertEqual(self.motor_b.line.id, alt["id"])
        self.assertIsNone(self.motor_b.state()["saisonspur_neu"])

    def test_neues_erntejahr_auf_derselben_ab_spur_bewahrt_alten_stand(self):
        alt = self.saat(2026)
        self.motor_a.load_field(self.feld["id"])
        antwort = self.a.post(f"/api/lines/{alt['id']}", json={"saisonspur": True, "saison": 2027})
        self.assertEqual(antwort.status_code, 200, antwort.text)
        neu = antwort.json()["data"]
        self.assertNotEqual(neu["id"], alt["id"])
        self.assertEqual(self.motor_a.line.id, neu["id"])
        self.assertEqual(self.motor_a.store.season_line(self.feld["id"], 2026)["id"], alt["id"])
        self.verteilen()
        self.assertEqual(self.laden_b()["id"], neu["id"])


if __name__ == "__main__":
    unittest.main()