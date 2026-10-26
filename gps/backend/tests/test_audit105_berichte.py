"""Audit105: öffentliche Berichte/Diagnose mit ausschließlich temporären Daten."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agripilot import arbeitsbericht, planpruefung
from agripilot.ausbau_diagnose import DiagnoseService
from agripilot.config import Config
from agripilot.guidance import VehicleProfile
from agripilot.probefahrt import Probefahrt
from agripilot.storage import Storage


class BerichteAudit105Test(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Storage(Path(self.tmp.name) / "bericht.db")
        self.field = self.store.save_field(dict(name="Prüffeld", datum_lat=48., datum_lon=9.,
            boundary=[[0, 0], [10, 0], [10, 10], [0, 10]]))
        self.job = self.store.start_job(self.field["id"], "audit-test", operation="Neujahrsarbeit")
        self.store.update_job(self.job["id"], working_time_s=3600, area_ha=.01)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_pdf_jahresauswahl_passt_zum_angezeigten_berliner_datum(self):
        stamp = datetime(2025, 12, 31, 23, 30, tzinfo=timezone.utc).timestamp()
        self.store.update_job(self.job["id"], started_at=stamp)
        with patch("zoneinfo.ZoneInfo", return_value=timezone(timedelta(hours=1))):
            pdf = arbeitsbericht.bericht_pdf(self.store, self.field["id"], jahr=2026)
            alt = arbeitsbericht.bericht_pdf(self.store, self.field["id"], jahr=2025)
        self.assertIn(b"Neujahrsarbeit", pdf)
        self.assertIn(b"01.01.2026, 00:30", pdf)
        self.assertNotIn(b"Neujahrsarbeit", alt)

    def test_pdf_jahreslimit_zaehlt_nur_ausgewaehlte_auftraege(self):
        self.store.update_job(self.job["id"], started_at=datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
        for i in range(3):
            job = self.store.start_job(self.field["id"], "audit-test", operation=f"Altauftrag {i}")
            self.store.update_job(job["id"], started_at=datetime(2025, 6, i+1, tzinfo=timezone.utc).timestamp())
        with patch.object(arbeitsbericht, "MAX_JOBS", 2):
            pdf = arbeitsbericht.bericht_pdf(self.store, self.field["id"], jahr=2026)
            with self.assertRaises(ValueError):
                arbeitsbericht.bericht_pdf(self.store, self.field["id"])
            with self.assertRaises(ValueError):
                arbeitsbericht.bericht_pdf(self.store, self.field["id"], jahr=2025)
        self.assertIn(b"Neujahrsarbeit", pdf)
        self.assertNotIn(b"Altauftrag", pdf)

    def test_kosten_abgeleitete_werte_bleiben_json_faehig(self):
        eingaben = dict(diesel_liter=10, diesel_preis_eur_l=1.5,
                        betriebsmittel_eur=5, stundensatz_eur=10000)
        for zeit, flaeche, feld in ((1e308, .01, "gesamt_eur"), (3600, 5e-324, "kosten_eur_ha")):
            with self.subTest(feld=feld):
                self.store.update_job(self.job["id"], working_time_s=zeit, area_ha=flaeche)
                gespeichert = arbeitsbericht.kosten_speichern(self.store, self.job["id"], eingaben)
                gelesen = arbeitsbericht.kosten_lesen(self.store, self.job["id"])
                self.assertIsNone(gelesen[feld])
                if feld == "gesamt_eur":
                    self.assertFalse(gelesen["vollstaendig"])
                json.dumps(gespeichert, allow_nan=False)
                json.dumps(gelesen, allow_nan=False)
                self.assertEqual(10, gelesen["diesel_liter"])


class PlanAudit105Test(unittest.TestCase):
    def setUp(self):
        self.field = {"boundary": [[0, 0], [100, 0], [100, 100], [0, 100]]}
        self.plan = {"spuren": [{"stuecke": [[[10, 10], [90, 10]]]}], "ringe": []}

    def test_ausgelassene_hauptfeldstuecke_nie_vollstaendig(self):
        teile = self.plan["spuren"][0]["stuecke"]
        teile.extend([[[20, 20], [20, 20]]] * (planpruefung.MAX_SEGMENTE - 1))
        teile.append([[-20, 10], [-10, 10]])
        vorher = deepcopy(self.plan)
        result = planpruefung.pruefen(self.plan, self.field, 3)
        self.assertFalse(result["vollstaendig"])
        self.assertFalse(result["ok"])
        self.assertEqual(vorher, self.plan)

    def test_fehlerhafte_hinweiscontainer_werden_als_unvollstaendig_gemeldet(self):
        faelle = (("konflikte", 7), ("konflikte", {"text": "Warnung"}),
                  ("konflikte", [{"text": "Warnung", "punkte": 7}]),
                  ("konflikte", [{"text": "Warnung", "punkte": {"x": 2}}]),
                  ("hinweise", 7), ("hinweise", {"text": "Warnung"}))
        for key, value in faelle:
            with self.subTest(key=key, value=value):
                plan = {**self.plan, key: value}
                result = planpruefung.pruefen(plan, self.field, 3)
                self.assertFalse(result["vollstaendig"])
                self.assertFalse(result["ok"])
                json.dumps(result, allow_nan=False)

    def test_gekuerzte_konfliktliste_nie_vollstaendig(self):
        self.plan["konflikte"] = [{"art": "rand", "text": "Rand prüfen"}] * 100
        self.plan["konflikte"].append({"art": "rest", "text": "Weitere Problemstelle", "punkte": [[50, 50]]})
        result = planpruefung.pruefen(self.plan, self.field, 3)
        self.assertFalse(result["vollstaendig"])
        self.assertFalse(result["ok"])
        self.assertLessEqual(len(result["befunde"]), planpruefung.MAX_BEFUNDE)

    def test_bool_ist_weder_arbeitsbreite_noch_spurkoordinate(self):
        self.assertFalse(planpruefung.pruefen(self.plan, self.field, True)["ok"])
        self.plan["spuren"][0]["stuecke"] = [[[True, 10], [90, 10]]]
        result = planpruefung.pruefen(self.plan, self.field, 3)
        self.assertFalse(result["ok"])
        self.assertFalse(result["vollstaendig"])


class DiagnoseAudit105Test(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Storage(Path(self.tmp.name) / "diagnose.db")
        self.cfg = Config()
        self.cfg.gnss.source = "serial"
        self.cfg.imu.source = "aus"
        self.cfg.network.device_id = "audit-test"
        self.p = Probefahrt(lambda bericht: None)
        self.e = SimpleNamespace(profile=VehicleProfile(), simulator=None, probefahrt=self.p,
            steering=SimpleNamespace(status=lambda: {"simulator": False}), imu=None, fix=None)
        self.e.probefahrt_bedienen = lambda aktion: self.p.bedienen(aktion, self.konfig())
        self.app = SimpleNamespace(engine=self.e, store=self.store, config=self.cfg)
        self.d = DiagnoseService(self.app)

    def tearDown(self):
        self.d.schliessen()
        self.store.close()
        self.tmp.cleanup()

    def konfig(self):
        return {"traktor": "t-test", "geraet": "g-test", "profil": {},
                "motor": {"gain_p": self.cfg.phidget.gain_p}, "uebung": False}

    def messen(self, beginn):
        for i in range(61):
            self.p.beobachten(beginn+i*.5, .1, 1.5, True, ["f-test", "linie", 0, False],
                              self.konfig(), imu_frisch=False)
        self.d.beobachte({}, {}, jetzt=beginn+31)

    def test_unterbrochene_nachher_messung_laesst_sich_neu_starten(self):
        start = time.time()+1
        self.d.fahrserie_start("gerade", bestaetigt=True)
        self.messen(start)
        vorher = deepcopy(self.p.vorher)
        self.d.fahrserie_start("gerade", "nachher", True)
        self.cfg.phidget.gain_p += .01
        self.p.beobachten(start+40, .1, 1.5, True, ["f-test", "linie", 0, False], self.konfig())
        self.d.beobachte({}, {}, jetzt=start+41)
        self.assertEqual("bereit", self.p.phase)
        self.d.fahrserie_start("gerade", "nachher", True)
        self.messen(start+50)
        self.assertEqual(vorher, self.p.vorher)
        ergebnisse = self.d.fahrserie_status()["ergebnisse"]
        self.assertEqual(1, len(ergebnisse))
        self.assertTrue(ergebnisse[0]["etappe_gueltig"])

    def test_fremd_ersetzte_vorher_messung_wird_nicht_der_etappe_zugeordnet(self):
        start = time.time()+1
        self.d.fahrserie_start("gerade", bestaetigt=True)
        self.messen(start)
        # Der allgemeine Probefahrt-Bedienweg kann dieselbe Messinstanz ersetzen.
        self.e.probefahrt_bedienen("vorher")
        self.messen(start+50)
        with self.assertRaises(ValueError):
            self.d.fahrserie_start("gerade", "nachher", True)
        self.assertFalse(self.p.sammelt)
        self.assertEqual([], self.d.fahrserie_status()["ergebnisse"])

    def test_fremde_vorher_waehrend_nachher_verhindert_wiederaufnahme(self):
        start = time.time()+1
        self.d.fahrserie_start("gerade", bestaetigt=True)
        self.messen(start)
        self.d.fahrserie_start("gerade", "nachher", True)
        self.e.probefahrt_bedienen("vorher")
        self.messen(start+50)
        with self.assertRaises(ValueError):
            self.d.fahrserie_start("gerade", "nachher", True)
        self.assertFalse(self.p.sammelt)
        self.assertEqual([], self.d.fahrserie_status()["ergebnisse"])

    def test_defekter_gespeicherter_plan_ist_ausdruecklich_unvollstaendig(self):
        field = self.store.save_field(dict(name="Prüffeld", datum_lat=48., datum_lon=9.,
            boundary=[[0, 0], [100, 0], [100, 100], [0, 100]]))
        self.store.set_setting("saatplan:"+field["id"], {"plan": "defekt"})
        self.d.planpruefung_start()
        self.d._thread.join(5)
        self.assertFalse(self.d._thread.is_alive())
        status = self.d.planpruefung_status()
        self.assertEqual("fertig", status["status"])
        result = status["felder"][0]
        self.assertEqual("fehler", result["status"])
        self.assertIs(result.get("vollstaendig"), False)
        self.assertFalse(result.get("ok", False))


if __name__ == "__main__":
    unittest.main()
