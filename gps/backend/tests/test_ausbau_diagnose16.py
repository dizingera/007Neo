"""Gegenfälle für Diagnose16; ausschließlich temporäre Daten, keine Hardware."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agripilot.ausbau_diagnose import DiagnoseService, ETAPPEN, KEY, MAX_KANTEN
from agripilot.config import Config
from agripilot.guidance import VehicleProfile
from agripilot.probefahrt import Probefahrt
from agripilot.storage import Storage


class Diagnose16Test(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Storage(Path(self.tmp.name)/"test.db")
        self.cfg = Config()
        self.cfg.gnss.source = "serial"
        self.cfg.network.role = "client"
        self.cfg.network.device_id = "test-geraet"
        self.store.set_setting("traktor_aktiv", "t1")
        self.store.set_setting("vehicle_profile_active", "g1")
        self.p = Probefahrt(lambda b: None)
        self.calls = []
        self.e = SimpleNamespace(profile=VehicleProfile(), simulator=None, probefahrt=self.p,
                                 steering=SimpleNamespace(status=lambda: {"simulator": False}),
                                 imu=None, fix=SimpleNamespace(speed_ms=0, received_at=time.time()))
        def bedienen(aktion):
            self.calls.append(aktion)
            if getattr(self.e, "gesperrt", False):
                raise ValueError("Lenkung nicht eingerichtet")
            return self.p.bedienen(aktion, self.konfig())
        self.e.probefahrt_bedienen = bedienen
        self.app = SimpleNamespace(engine=self.e, store=self.store, config=self.cfg,
                                   sync_client=None, source=SimpleNamespace(healthy=True))
        self.d = DiagnoseService(self.app)

    def tearDown(self):
        self.d.schliessen()
        self.store.close()
        self.tmp.cleanup()

    def konfig(self):
        return {"traktor": "t1", "geraet": "g1", "profil": {}, "uebung": self.e.simulator is not None}

    def messung(self, tempo=1.5, roll=3):
        t = time.time()+1
        for i in range(61):
            self.p.beobachten(t+i*.5, .1, tempo, True, ["f", "linie", 0, False],
                             self.konfig(), roll=roll, imu_frisch=True)
        self.d.beobachte({}, {}, jetzt=t+31)

    def fahrt(self, etappe="gerade", tempo=1.5, roll=3):
        self.d.fahrserie_start(etappe, bestaetigt=True)
        self.messung(tempo, roll)
        self.d.fahrserie_start(etappe, "nachher", True)
        self.messung(tempo, roll)

    def test_vorschriften_und_strikte_bestaetigung(self):
        self.assertTrue(self.d.fahrserie_status()["vorschriften"])
        for wert in (False, "ja", 1, None):
            with self.assertRaises(ValueError):
                self.d.fahrserie_start("gerade", bestaetigt=wert)
        self.assertEqual([], self.calls)

    def test_engine_guard_nicht_umgangen(self):
        self.e.gesperrt = True
        with self.assertRaises(ValueError):
            self.d.fahrserie_start("gerade", bestaetigt=True)
        self.assertFalse(self.p.sammelt)
        self.assertIsNone(self.d.fahrserie_status()["aktiv"])

    def test_fremde_laufende_probefahrt_bleibt(self):
        self.p.bedienen("vorher", self.konfig())
        with self.assertRaises(ValueError):
            self.d.fahrserie_start("hang", bestaetigt=True)
        self.assertTrue(self.p.sammelt)
        self.assertEqual([], self.calls)

    def test_nachher_braucht_eigene_vorher(self):
        with self.assertRaises(ValueError):
            self.d.fahrserie_start("gerade", "nachher", True)
        self.d.fahrserie_start("gerade", bestaetigt=True)
        self.messung()
        with self.assertRaises(ValueError):
            self.d.fahrserie_start("hang", "nachher", True)

    def test_echte_30s_zuordnung_persistiert(self):
        self.fahrt()
        self.d.flush()
        neu = DiagnoseService(self.app)
        try:
            f = neu.fahrserie_status()["ergebnisse"]
            self.assertEqual(1, len(f))
            self.assertEqual("gerade", f[0]["etappe"])
            self.assertTrue(f[0]["etappe_gueltig"])
            self.assertGreaterEqual(f[0]["nachher"]["dauer_s"], 30)
            self.assertIsNone(neu.fahrserie_status()["aktiv"])
        finally:
            neu.schliessen()

    def test_zu_kurze_fahrt_ergibt_keinen_bericht(self):
        self.d.fahrserie_start("gerade", bestaetigt=True)
        for i in range(20):
            self.p.beobachten(time.time()+i*.1, .1, 1.5, True, [1], self.konfig())
        self.d.beobachte({}, {})
        self.assertEqual([], self.d.fahrserie_status()["ergebnisse"])

    def test_sim_und_replay_niemals_reale_abnahme(self):
        for quelle in ("simulator", "replay", "aus"):
            self.cfg.gnss.source = quelle
            with self.assertRaises(ValueError):
                self.d.abnahme_bestaetigen(True)
        self.cfg.gnss.source = "serial"
        self.e.simulator = object()
        self.fahrt()
        self.assertTrue(self.d.fahrserie_status()["ergebnisse"][0]["uebung"])
        with self.assertRaises(ValueError):
            self.d.abnahme_bestaetigen(True)

    def test_alle_etappen_real_explizit_und_aenderung_veraltet(self):
        for e in ETAPPEN:
            self.fahrt(e)
        with self.assertRaises(ValueError):
            self.d.abnahme_bestaetigen()
        self.assertTrue(self.d.abnahme_bestaetigen(True)["real_geprueft"])
        self.cfg.phidget.gain_p += .01
        self.assertEqual("veraltet", self.d.abnahme_status()["status"])
        self.assertFalse(self.d.abnahme_status()["real_geprueft"])

    def test_fehlende_etappen_verhindern_abnahme(self):
        self.fahrt()
        with self.assertRaises(ValueError):
            self.d.abnahme_bestaetigen(True)
        self.assertEqual(4, len(self.d.abnahme_status()["fehlende_etappen"]))

    def test_hang_ohne_neigung_zaehlt_nicht(self):
        self.fahrt("hang", roll=0)
        self.assertFalse(self.d.fahrserie_status()["ergebnisse"][0]["etappe_gueltig"])
        self.assertIn("hang", self.d.abnahme_status()["fehlende_etappen"])

    def test_langsam_nicht_bei_arbeitsgeschwindigkeit(self):
        self.fahrt("langsam", tempo=3)
        self.assertFalse(self.d.fahrserie_status()["ergebnisse"][0]["etappe_gueltig"])

    def test_wiederanfahrt_braucht_stillstand(self):
        self.e.fix.speed_ms = 3
        with self.assertRaises(ValueError):
            self.d.fahrserie_start("wiederanfahrt", bestaetigt=True)
        s, sys = self.sensor(time.time())
        s["fix"]["speed_ms"] = 0
        self.d.beobachte(s, sys)
        self.d.fahrserie_start("wiederanfahrt", bestaetigt=True)
        self.assertTrue(self.p.sammelt)

    def test_fingerprint_geometrie_regler_kalibrierung_ids(self):
        def anders(fn):
            alt = self.d.fingerprint()
            fn()
            self.assertNotEqual(alt, self.d.fingerprint())
        anders(lambda: setattr(self.e.profile, "antenna_height_m", 4))
        anders(lambda: setattr(self.e.profile, "cerea_lenk_aggr", 120))
        anders(lambda: setattr(self.cfg.phidget, "counts_per_deg", 80))
        anders(lambda: self.store.set_setting("imu_offsets", {"roll_offset": .5}))
        anders(lambda: self.store.set_setting("vehicle_profile_active", "g2"))
        anders(lambda: setattr(self.cfg.imu, "uid", "sensor2"))
        anders(lambda: setattr(self.cfg.phidget, "serial_number", 123))
        alt = self.d.fingerprint()
        self.e.profile.name = "Anderer Anzeigename"
        self.assertEqual(alt, self.d.fingerprint())

    def test_abbrechen_aendert_keine_einstellung(self):
        fp = self.d.fingerprint()
        self.d.fahrserie_start("gerade", bestaetigt=True)
        self.d.fahrserie_abbrechen()
        self.assertFalse(self.p.sammelt)
        self.assertEqual(fp, self.d.fingerprint())

    def feld(self, nr="f1"):
        return self.store.save_field({"id": nr, "name": nr, "boundary": [[0,0],[20,0],[20,20],[0,20]],
                                     "datum_lat": 48, "datum_lon": 9})

    def warten(self):
        self.d._thread.join(5)
        self.assertFalse(self.d._thread.is_alive())
        return self.d.planpruefung_status()

    def test_plan_echte_kreuzung_karte_breite_aus_speicherung(self):
        self.feld()
        self.e.profile.width_m = 99
        plan = {"spuren": [{"stuecke": [[[1,10],[19,10]]]}],
                "ringe": [{"punkte": [[5,5],[15,5],[15,15],[5,15],[5,5]]}]}
        self.store.set_setting("saatplan:f1", {"vorgabe": {"breite_m": 3}, "plan": plan})
        vorher = self.store.get_setting("saatplan:f1")
        self.d.planpruefung_start()
        status = self.warten()
        f = status["felder"][0]
        self.assertEqual(3, f["breite_m"])
        self.assertTrue(f["befunde"])
        self.assertTrue(any(b.get("punkte") for b in f["befunde"]))
        self.assertEqual(vorher, self.store.get_setting("saatplan:f1"))
        self.assertEqual("fertig", status["status"])

    def test_kein_plan_keine_erfundenen_acht_kreuzungen(self):
        self.feld()
        self.d.planpruefung_start()
        f = self.warten()["felder"][0]
        self.assertEqual("kein_plan", f["status"])
        self.assertEqual([], f["befunde"])

    def test_breite_fehlt_nicht_heutige_benutzen(self):
        self.feld()
        self.store.set_setting("saatplan:f1", {"plan": {"spuren": [], "ringe": []}})
        self.d.planpruefung_start()
        f = self.warten()["felder"][0]
        self.assertIsNone(f["breite_m"])
        self.assertFalse(f["vollstaendig"])

    def test_saisonplan_ohne_entwurf_mit_eigener_breite(self):
        self.feld()
        self.store.save_line({"field_id": "f1", "name": "Saatplan alt", "mode": "plan",
            "spacing_m": 4, "saison": 2025, "points": [[0,0],[1,0]],
            "spuren": [{"art": "A", "punkte": [[1,10],[19,10]]},
                       {"art": "V", "geschlossen": True,
                        "punkte": [[5,5],[15,5],[15,15],[5,15],[5,5]]}]})
        self.d.planpruefung_start()
        f = self.warten()["felder"][0]
        self.assertEqual("geprueft", f["status"])
        self.assertEqual(4, f["plaene"][0]["breite_m"])
        self.assertEqual(2025, f["plaene"][0]["saison"])
        self.assertTrue(f["befunde"])

    def test_ungueltiger_plan_isoliert_naechstes_feld_wird_geprueft(self):
        self.feld("f1")
        self.feld("f2")
        self.store.set_setting("saatplan:f1", {"plan": "kaputt"})
        self.d.planpruefung_start()
        status = self.warten()
        self.assertEqual(2, status["fertig"])
        daten = {f["feld_id"]: f["status"] for f in status["felder"]}
        self.assertEqual("fehler", daten["f1"])
        self.assertEqual("kein_plan", daten["f2"])

    def test_feldlimit_sichtbar_kein_vollstaendigkeitsschwindel(self):
        self.feld("f1")
        self.feld("f2")
        with patch("agripilot.ausbau_diagnose.MAX_FELDER", 1):
            self.d.planpruefung_start()
            status = self.warten()
        self.assertTrue(status["begrenzt"])
        self.assertEqual(1, status["fertig"])

    def test_eingefrorener_fix_trotz_gesunder_quelle(self):
        s, sys = self.sensor()
        self.d.beobachte(s, sys, jetzt=105)
        k = {x["kanal"]: x["zustand"] for x in self.d.verlauf()["kanten"]}
        self.assertEqual("ausfall", k["gps"])
        self.assertEqual("ausfall", k["rtk"])
        self.assertEqual("ok", k["imu"])

    def test_fehlende_fixzeit_keine_frische_erfinden(self):
        s, sys = self.sensor()
        s["fix"].pop("received_at")
        self.d.beobachte(s, sys, jetzt=100)
        self.assertEqual("unbekannt", self.d.verlauf()["kanten"][0]["zustand"])

    def test_einzelne_simulierte_imu_verhindert_reale_abnahme(self):
        self.cfg.imu.source = "simulator"
        self.assertTrue(self.d.abnahme_status()["uebung"])
        with self.assertRaises(ValueError):
            self.d.abnahme_bestaetigen(True)

    def test_nan_koordinaten_keine_gesunde_gps_meldung(self):
        s, sys = self.sensor()
        s["fix"]["lat"] = float("nan")
        self.d.beobachte(s, sys, jetzt=100)
        self.assertEqual("ausfall", self.d.verlauf()["kanten"][0]["zustand"])

    def test_376_historische_plaene_desselben_feldes_werden_alle_geprueft(self):
        self.feld()
        ids = set()
        for i in range(376):
            linie = self.store.save_line({"field_id": "f1", "name": f"Historischer Plan {i}",
                "mode": "plan", "spacing_m": 3, "saison": 2000+i,
                "points": [[1,1],[19,1]],
                "spuren": [{"art": "A", "punkte": [[1,1],[19,1]]}]})
            ids.add(linie["id"])
        self.d.planpruefung_start()
        feld = self.warten()["felder"][0]
        self.assertEqual(ids, {p["plan_id"] for p in feld["plaene"]})
        self.assertEqual(376, len(feld["plaene"]))
        self.assertFalse(feld["begrenzt"])
        self.assertTrue(feld["vollstaendig"])

    def test_ueberschrittene_plangrenze_bleibt_sichtbar_unvollstaendig(self):
        self.feld()
        for i in range(3):
            self.store.save_line({"field_id": "f1", "name": f"Plan {i}",
                "mode": "plan", "spacing_m": 3, "points": [[1,1],[19,1]],
                "spuren": [{"art": "A", "punkte": [[1,1],[19,1]]}]})
        with patch("agripilot.ausbau_diagnose.MAX_PLAENE_PRO_FELD", 2):
            self.d.planpruefung_start()
            feld = self.warten()["felder"][0]
        self.assertEqual(2, len(feld["plaene"]))
        self.assertTrue(feld["begrenzt"])
        self.assertFalse(feld["vollstaendig"])

    def test_nur_ein_job_und_abbruch(self):
        self.feld()
        import threading
        an = threading.Event()
        weiter = threading.Event()
        def pruefen(*args):
            an.set()
            weiter.wait(3)
            return {"befunde": []}
        with patch("agripilot.ausbau_diagnose.planpruefung.pruefen", side_effect=pruefen):
            self.store.set_setting("saatplan:f1", {"plan": {"spuren": [1]}})
            self.d.planpruefung_start()
            self.assertTrue(an.wait(2))
            with self.assertRaises(ValueError):
                self.d.planpruefung_start()
            self.d.planpruefung_abbrechen()
            weiter.set()
            self.assertEqual("abgebrochen", self.warten()["status"])

    def sensor(self, zeit=100):
        state = {"fix": {"lat": 48, "lon": 9, "fix_quality": 4, "speed_ms": 2, "received_at": zeit},
                 "imu": {"fresh": True, "healthy": True}}
        system = {"gnss": {"healthy": True}, "role": "client", "sync": {"last_success": zeit}}
        return state, system

    def test_getrennte_kanten_und_keine_tick_dbio(self):
        s, sys = self.sensor()
        with patch.object(self.store, "get_setting", side_effect=AssertionError("Tick-DB-Lesen")), patch.object(self.store, "set_setting") as setz:
            for _ in range(1000):
                self.d.beobachte(s, sys, jetzt=100)
            self.assertEqual(4, len(self.d.verlauf()["kanten"]))
            setz.assert_not_called()
        s["fix"]["fix_quality"] = 5
        self.d.beobachte(s, sys, jetzt=101)
        self.assertEqual("rtk", self.d.verlauf()["kanten"][-1]["kanal"])
        s["imu"]["fresh"] = False
        self.d.beobachte(s, sys, jetzt=102)
        self.assertEqual("imu", self.d.verlauf()["kanten"][-1]["kanal"])
        self.d.beobachte(s, sys, jetzt=131)
        self.assertEqual("master", self.d.verlauf()["kanten"][-1]["kanal"])

    def test_fehlertexte_tokens_nie_persistiert(self):
        s, sys = self.sensor()
        sys.update(arbeitercode="GEHEIM", sync_token="GEHEIM")
        sys["sync"].update(last_error="GEHEIM", master_url="https://GEHEIM")
        self.d.beobachte(s, sys, jetzt=100)
        self.d.flush()
        self.assertNotIn("GEHEIM", json.dumps(self.store.get_setting(KEY)))

    def test_fix_veraltet_trotz_rtk_getrennt_imu_gut(self):
        s, sys = self.sensor()
        sys["gnss"]["healthy"] = False
        self.d.beobachte(s, sys, jetzt=100)
        kanten = {x["kanal"]: x["zustand"] for x in self.d.verlauf()["kanten"]}
        self.assertEqual("ausfall", kanten["gps"])
        self.assertEqual("ausfall", kanten["rtk"])
        self.assertEqual("ok", kanten["imu"])

    def test_unbekannt_keine_konstruierte_ausfalldauer_neustart(self):
        s, sys = self.sensor()
        self.d.beobachte(s, sys, jetzt=100)
        self.d.flush()
        neu = DiagnoseService(self.app)
        try:
            neu.beobachte({}, {}, jetzt=500)
            self.assertTrue(all(x["neustart"] for x in neu.verlauf()["kanten"][-4:]))
            k = {x["kanal"]: x["zustand"] for x in neu.verlauf()["kanten"][-4:]}
            self.assertEqual("unbekannt", k["master"])
            self.assertEqual("unbekannt", k["imu"])
        finally:
            neu.schliessen()

    def test_verlauf_begrenzt_und_kopie(self):
        s, sys = self.sensor()
        for i in range(600):
            s["imu"]["fresh"] = bool(i % 2)
            self.d.beobachte(s, sys, jetzt=100)
        self.assertEqual(MAX_KANTEN, len(self.d.verlauf()["kanten"]))
        kopie = self.d.verlauf()
        kopie["kanten"].clear()
        self.assertEqual(MAX_KANTEN, len(self.d.verlauf()["kanten"]))

    def test_noarg_beobachten_ohne_systemstatus_oder_db(self):
        self.e.fix = SimpleNamespace(to_dict=lambda: self.sensor()[0]["fix"])
        with patch.object(self.store, "get_setting", side_effect=AssertionError("DB")):
            self.d.beobachten()
        self.assertEqual(4, len(self.d.verlauf()["kanten"]))


if __name__ == "__main__":
    unittest.main()
