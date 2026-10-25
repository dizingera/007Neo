"""Betriebsbuch an der öffentlichen Schnittstelle, einschließlich echter SQLite-Persistenz."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agripilot.betriebsbuch import Betriebsbuch, SCHLUESSEL
from agripilot.storage import Storage


class Speicher:
    def __init__(self, daten=None):
        self.daten = deepcopy(daten or {})
        self.lesen = 0
        self.schreiben = 0

    def get_setting(self, key, default=None):
        self.lesen += 1
        return deepcopy(self.daten.get(key, default))

    def set_setting(self, key, wert):
        self.schreiben += 1
        # Tatsächlich JSON-kompatibel, keine lebenden Referenzen im Fake.
        self.daten[key] = json.loads(json.dumps(wert, allow_nan=False))


def zustand(armed=True, engaged=True, reason="lenkt", **extra):
    return {"steering": {"armed": armed, "engaged": engaged, "reason": reason},
            "traktor_id": "traktor-1", "geraet_id": "geraet-2",
            "field": {"id": "feld-3"}, "job": {"id": "arbeit-4"}, **extra}


class BetriebsbuchTests(unittest.TestCase):
    def setUp(self):
        self.store = Speicher()
        self.buch = Betriebsbuch(self.store)

    def test_ruhe_und_messwertschwankungen_ohne_datenbankzugriffe(self):
        self.buch.observation(zustand(False, False, "nicht scharf"), 0)
        for i in range(1, 1000):
            self.buch.observation(zustand(False, False, "nicht scharf", fix={"speed_ms": i / 10}), i / 10)
        self.assertEqual((1, 0), (self.store.lesen, self.store.schreiben))
        self.assertEqual([], self.buch.verlauf()["unterbrechungen"])

    def test_pause_dauer_kanten_und_wiederanlauf(self):
        self.buch.observation(zustand(), 10)
        self.buch.observation(zustand(True, False, "zu schnell"), 11)
        for i in range(12, 30):
            self.buch.observation(zustand(True, False, "zu schnell"), i)
        e = self.buch.verlauf()["unterbrechungen"][0]
        self.assertEqual("Pause", e["klasse"])
        self.assertEqual(18, e["dauer_s"])
        self.assertTrue(e["offen"])
        self.assertEqual(1, self.store.schreiben)
        self.buch.observation(zustand(), 30)
        e = self.buch.verlauf()["unterbrechungen"][0]
        self.assertEqual(19, e["dauer_s"])
        self.assertEqual(30, e["ende"])
        self.assertFalse(e["offen"])
        self.assertEqual(2, self.store.schreiben)
        self.assertEqual(1, self.store.lesen)

    def test_abschalten_und_fehler_klassifiziert(self):
        for grund, klasse in (("vom Fahrer ausgeschaltet", "Aus"),
                              ("Fahrer hat eingegriffen", "Aus"),
                              ("Lenkausgang gestört: Zugangsdaten geheim", "Störung")):
            with self.subTest(grund=grund):
                buch = Betriebsbuch(Speicher())
                buch.observation(zustand(), 0)
                buch.observation(zustand(False, False, grund), 1)
                self.assertEqual(klasse, buch.verlauf()["unterbrechungen"][0]["klasse"])
                self.assertNotIn("geheim", json.dumps(buch.verlauf()))

    def test_grundwerte_keine_tickweise_neuen_eintraege(self):
        self.buch.observation(zustand(True, False, "zu weit von der Spur (1.20 m)"), 1)
        self.buch.observation(zustand(True, False, "zu weit von der Spur (1.21 m)"), 2)
        self.assertEqual(1, self.store.schreiben)
        self.buch.observation(zustand(True, False, "RTK nötig, aktuell: Float"), 3)
        es = self.buch.verlauf()["unterbrechungen"]
        self.assertEqual(2, len(es))
        self.assertEqual("RTK nötig", es[0]["grund"])
        self.assertEqual(2, es[1]["dauer_s"])

    def test_gespannwechsel_beendet_alte_pause(self):
        self.buch.observation(zustand(True, False, "zu langsam"), 0)
        self.buch.observation(zustand(True, False, "zu langsam", geraet_id="geraet-5"), 1)
        neu, alt = self.buch.verlauf()["unterbrechungen"]
        self.assertEqual("geraet-5", neu["snapshot"]["geraet_id"])
        self.assertEqual("geraet-2", alt["snapshot"]["geraet_id"])
        self.assertFalse(alt["offen"])
        self.assertEqual(1, alt["dauer_s"])
        self.assertNotEqual(neu["id"], alt["id"])

    def test_snapshot_strikt_nicht_geheim_und_unabhaengig(self):
        state = zustand(True, False, "beliebige Fehlermeldung mit TOKEN", token="GEHEIM",
                        fix={"speed_ms": 1.2, "accuracy_m": float("nan"), "password": "GEHEIM"},
                        imu={"fresh": True, "roll_deg": 2.5, "status": "GEHEIM"},
                        system={"sync_token": "GEHEIM"})
        self.buch.observation(state, 0)
        state["fix"]["speed_ms"] = 9
        ergebnis = self.buch.verlauf()
        e = ergebnis["unterbrechungen"][0]
        self.assertEqual("unbekannt", e["grund"])
        self.assertEqual(1.2, e["snapshot"]["fix"]["speed_ms"])
        self.assertNotIn("accuracy_m", e["snapshot"]["fix"])
        self.assertNotIn("GEHEIM", json.dumps(ergebnis))
        e["snapshot"]["fix"]["speed_ms"] = 99
        self.assertEqual(1.2, self.buch.verlauf()["unterbrechungen"][0]["snapshot"]["fix"]["speed_ms"])

    def test_beobachtungsluecke_und_rueckwaertsuhr(self):
        self.buch.observation(zustand(True, False, "zu langsam"), 10)
        self.buch.observation(zustand(True, False, "zu langsam"), 12)
        self.buch.observation(zustand(), 11)  # Uhr zurück: keine negative Dauer.
        self.assertEqual(1, self.store.schreiben)
        self.buch.observation(zustand(), 30)
        e = self.buch.verlauf()["unterbrechungen"][0]
        self.assertEqual(12, e["ende"])
        self.assertEqual(2, e["dauer_s"])
        self.assertIn("unbekannt", e["abschluss"])

    def test_luecke_neue_pause_statt_lange_dauer(self):
        self.buch.observation(zustand(True, False, "zu langsam"), 0)
        self.buch.observation(zustand(True, False, "zu langsam"), 30)
        neu, alt = self.buch.verlauf()["unterbrechungen"]
        self.assertEqual(30, neu["zeitpunkt"])
        self.assertEqual(0, alt["dauer_s"])
        self.assertFalse(alt["offen"])

    def test_neustart_beendet_offene_pause_ohne_erfundene_dauer(self):
        self.buch.observation(zustand(True, False, "zu langsam"), 10)
        buch = Betriebsbuch(self.store)
        e = buch.verlauf()["unterbrechungen"][0]
        self.assertFalse(e["offen"])
        self.assertIsNone(e["ende"])
        self.assertIsNone(e["dauer_s"])
        self.assertEqual("Neustart: Ende unbekannt", e["abschluss"])
        anzahl = self.store.schreiben
        Betriebsbuch(self.store)
        self.assertEqual(anzahl, self.store.schreiben)

    def test_einstellungen_echte_differenz_quelle_ids_zeitpunkt(self):
        vorher = {"cerea_d": 0.45, "regelart": "stanley", "wheelbase_m": 2.6}
        nachher = {**vorher, "cerea_d": 0.5, "regelart": "cerea"}
        with patch("agripilot.betriebsbuch.time.time", return_value=123):
            e = self.buch.einstellung_aendern(vorher, nachher, "fahrer", {"traktor_id": "t1", "geraet_id": "g1"})
        self.assertEqual(123, e["zeitpunkt"])
        self.assertEqual("fahrer", e["quelle"])
        self.assertEqual({"traktor_id": "t1", "geraet_id": "g1"}, e["kontext"])
        self.assertEqual({"cerea_d", "regelart"}, set(e["aenderungen"]))
        self.assertEqual({"vorher": 0.45, "nachher": 0.5}, e["aenderungen"]["cerea_d"])
        e["aenderungen"]["cerea_d"]["nachher"] = 99
        self.assertEqual(0.5, self.buch.verlauf()["einstellungen"][0]["aenderungen"]["cerea_d"]["nachher"])
        self.assertEqual(0.45, vorher["cerea_d"])

    def test_unveraendert_ungueltig_fehlend_und_geheim_nicht_speichern(self):
        for neu in (1, True, float("nan"), float("inf"), 10 ** 1000, "TOKEN", {}, []):
            with self.subTest(neu=neu):
                self.assertIsNone(self.buch.record({"cerea_d": 1}, {"cerea_d": neu}, "fahrer", {}))
        self.assertIsNone(self.buch.record({"regelart": "cerea"}, {"regelart": "TOKEN"}, "fahrer", {}))
        self.assertIsNone(self.buch.record({"cerea_d": 1}, {}, "fahrer", {}))
        self.assertIsNone(self.buch.record({"password": "alt"}, {"password": "neu"}, "fahrer", {}))
        self.assertEqual(0, self.store.schreiben)
        e = self.buch.record({"cerea_d": 1, "sync_token": "alt"}, {"cerea_d": 2, "sync_token": "GEHEIM"},
                             "GEHEIM", {"traktor_id": "t1", "password": "GEHEIM", "geraet_id": {"token": "GEHEIM"}})
        self.assertEqual("unbekannt", e["quelle"])
        self.assertNotIn("GEHEIM", json.dumps(self.store.daten))

    def test_beide_verlaeufe_begrenzt_und_neueste_zuerst(self):
        buch = Betriebsbuch(self.store, limit=3)
        for i in range(10):
            buch.observation(zustand(), i * 2)
            buch.observation(zustand(True, False, "zu langsam"), i * 2 + 1)
            buch.record({"cerea_d": i}, {"cerea_d": i + 1}, "automatik", {})
        self.assertEqual(3, len(self.store.daten[SCHLUESSEL]["unterbrechungen"]))
        self.assertEqual(3, len(self.store.daten[SCHLUESSEL]["einstellungen"]))
        self.assertEqual(19, buch.verlauf()["unterbrechungen"][0]["zeitpunkt"])
        self.assertEqual(1, len(buch.verlauf(1)["einstellungen"]))
        self.assertEqual([], buch.verlauf(0)["unterbrechungen"])
        # Defaultausgabe hat auch bei größerem Speicherlimit höchstens 100.
        gross = Betriebsbuch(Speicher(), limit=120)
        for i in range(110):
            gross.record({"cerea_d": i}, {"cerea_d": i + 1}, "fahrer", {})
        self.assertEqual(100, len(gross.verlauf()["einstellungen"]))

    def test_defekte_daten_und_ungueltige_zustaende(self):
        for daten in (None, [], "kaputt", {"unterbrechungen": [None, {}, 42], "einstellungen": "kaputt"}):
            buch = Betriebsbuch(Speicher({SCHLUESSEL: daten}))
            for state in (None, {}, {"steering": {}}, {"steering": {"armed": 1, "engaged": False}}):
                buch.observation(state, 0)
            self.assertEqual({"unterbrechungen": [], "einstellungen": []}, buch.verlauf())
        with self.assertRaises(ValueError):
            self.buch.observation(zustand(), float("nan"))

    def test_gespeicherte_eintraege_erneut_filtern(self):
        self.buch.record({"cerea_d": 1}, {"cerea_d": 2}, "fahrer", {})
        daten = self.store.daten[SCHLUESSEL]["einstellungen"][0]
        daten["password"] = "GEHEIM"
        daten["aenderungen"]["sync_token"] = {"vorher": "alt", "nachher": "GEHEIM"}
        daten["kontext"]["token"] = "GEHEIM"
        self.assertNotIn("GEHEIM", json.dumps(Betriebsbuch(self.store).verlauf()))

    def test_stoerung_bei_weiter_scharfer_lenkung_und_wiederanlauf(self):
        self.buch.observation(zustand(), 1)
        self.buch.observation(zustand(True, False, "Lenkausgang gestört: interner Fehler"), 2)
        self.buch.observation(zustand(), 4)
        e = self.buch.verlauf()["unterbrechungen"][0]
        self.assertEqual("Störung", e["klasse"])
        self.assertEqual(2, e["dauer_s"])
        self.assertFalse(e["offen"])

    def test_neuladen_filtert_manipulierte_ids_und_snapshot(self):
        self.buch.observation(zustand(True, False, "zu langsam"), 1)
        e = self.store.daten[SCHLUESSEL]["unterbrechungen"][0]
        e["snapshot"]["sync_token"] = "GEHEIM"
        e["snapshot"]["imu"] = {"roll_deg": 1, "status": "GEHEIM"}
        buch = Betriebsbuch(self.store)
        self.assertNotIn("GEHEIM", json.dumps(buch.verlauf()))
        self.store.daten[SCHLUESSEL]["unterbrechungen"][0]["id"] = "GEHEIM"
        self.assertEqual([], Betriebsbuch(self.store).verlauf()["unterbrechungen"])

    def test_echte_storage_persistenz(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Storage(Path(tmp) / "betriebsbuch.db")
            buch = Betriebsbuch(store)
            buch.observation(zustand(), 0)
            buch.observation(zustand(True, False, "zu langsam"), 1)
            buch.observation(zustand(), 3)
            buch.record({"wheelbase_m": 2.6}, {"wheelbase_m": 2.7}, "kalibrierung", {})
            store.db.close()
            store = Storage(Path(tmp) / "betriebsbuch.db")
            geladen = Betriebsbuch(store).verlauf()
            self.assertEqual(2, geladen["unterbrechungen"][0]["dauer_s"])
            self.assertEqual(2.7, geladen["einstellungen"][0]["aenderungen"]["wheelbase_m"]["nachher"])
            store.db.close()


if __name__ == "__main__":
    unittest.main()
