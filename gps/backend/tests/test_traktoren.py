"""Traktoren getrennt von den Geräten: Antenne und Lenkung je Traktor, der Rest je Maschine."""

import json
import os
import tempfile
import unittest
import warnings

from agripilot import config as config_module
from agripilot import myterra
from agripilot.guidance import TRAKTOR_FELDER, VehicleProfile


def _client(ordner):
    from fastapi.testclient import TestClient
    from agripilot.server import create_app
    warnings.filterwarnings("ignore")
    config = config_module.load("/kein-solcher-pfad.yaml")
    config.server.data_dir = ordner
    return TestClient(create_app(config))


def _aktiv(liste):
    return next(e for e in liste if e["aktiv"])


class AufteilungTests(unittest.TestCase):
    def test_tractor_fields_are_antenna_and_steering_only(self):
        self.assertIn("antenna_forward_m", TRAKTOR_FELDER)
        self.assertIn("steer_gain", TRAKTOR_FELDER)
        self.assertIn("speed_gain_rest", TRAKTOR_FELDER)
        for geraet in ("width_m", "sections", "tool_trailing_m", "trailed", "section_pcc", "name"):
            self.assertNotIn(geraet, TRAKTOR_FELDER)
        self.assertTrue(set(TRAKTOR_FELDER) <= set(VehicleProfile.__dataclass_fields__))


class TraktorTests(unittest.TestCase):
    def test_a_fresh_install_has_one_tractor_with_a_stable_id(self):
        with tempfile.TemporaryDirectory() as ordner, _client(ordner) as c:
            traktoren = c.get("/api/traktoren").json()
            self.assertEqual([t["name"] for t in traktoren], ["Traktor"])
            self.assertEqual(traktoren[0]["id"], "traktor-traktor")
            self.assertEqual(traktoren[0]["stand"], 0.0)   # jede eingestellte Fassung gewinnt

    def test_antenna_belongs_to_the_tractor_width_to_the_machine(self):
        with tempfile.TemporaryDirectory() as ordner, _client(ordner) as c:
            grubber = c.post("/api/profiles", json={"name": "Grubber", "werte": {"width_m": 2.5}}).json()["data"]
            c.post("/api/profiles", json={"name": "Spritze", "werte": {"width_m": 24.0}})
            # Antenne am gewählten Traktor einmessen - bei gewählter Spritze.
            c.post("/api/profile", json={"antenna_forward_m": 1.85, "antenna_height_m": 3.4})
            c.post(f"/api/profiles/{grubber['id']}/select")
            gespann = c.get("/api/state").json()["profile"]
            self.assertEqual(gespann["width_m"], 2.5)                  # vom Grubber
            self.assertEqual(gespann["antenna_forward_m"], 1.85)       # vom Traktor
            self.assertEqual(gespann["antenna_height_m"], 3.4)

    def test_choosing_another_tractor_keeps_the_implement(self):
        with tempfile.TemporaryDirectory() as ordner, _client(ordner) as c:
            c.post("/api/profiles", json={"name": "Grubber", "werte": {"width_m": 2.5}})
            erster = _aktiv(c.get("/api/traktoren").json())
            c.post("/api/traktor", json={"name": "John Deere 7530 Premium", "antenna_forward_m": 1.9})
            zweiter = c.post("/api/traktoren", json={"name": "IHC 956 XL",
                                                    "werte": {"antenna_forward_m": 0.8}}).json()["data"]
            stand = c.get("/api/state").json()
            self.assertEqual(stand["traktor"], "IHC 956 XL")
            self.assertEqual(stand["profile"]["antenna_forward_m"], 0.8)
            self.assertEqual(stand["profile"]["width_m"], 2.5)
            c.post(f"/api/traktoren/{erster['id']}/select")
            stand = c.get("/api/state").json()
            self.assertEqual(stand["traktor"], "John Deere 7530 Premium")
            self.assertEqual(stand["profile"]["antenna_forward_m"], 1.9)
            self.assertEqual(stand["profile"]["width_m"], 2.5)
            self.assertNotEqual(erster["id"], zweiter["id"])

    def test_duplicate_names_empty_names_and_the_last_tractor_are_refused(self):
        with tempfile.TemporaryDirectory() as ordner, _client(ordner) as c:
            self.assertEqual(c.post("/api/traktoren", json={"name": ""}).status_code, 400)
            self.assertEqual(c.post("/api/traktoren", json={"name": "traktor"}).status_code, 400)
            einziger = _aktiv(c.get("/api/traktoren").json())
            self.assertEqual(c.delete(f"/api/traktoren/{einziger['id']}").status_code, 400)
            self.assertEqual(c.post("/api/traktor", json={"name": "  "}).status_code, 400)

    def test_deleting_the_chosen_tractor_chooses_another(self):
        with tempfile.TemporaryDirectory() as ordner, _client(ordner) as c:
            neu = c.post("/api/traktoren", json={"name": "IHC 956 XL"}).json()["data"]
            self.assertEqual(c.delete(f"/api/traktoren/{neu['id']}").status_code, 200)
            self.assertEqual([t["name"] for t in c.get("/api/traktoren").json()], ["Traktor"])
            self.assertEqual(c.get("/api/state").json()["traktor"], "Traktor")


class UmstiegTests(unittest.TestCase):
    def test_from_156_the_tractor_names_and_measured_antenna_carry_over(self):
        from agripilot.engine import Engine
        from agripilot.storage import Storage
        with tempfile.TemporaryDirectory() as ordner:
            store = Storage(os.path.join(ordner, "a.db"))
            # Stand 1.5.6: das Gespann mit gemessener Antenne, Traktor als Name an der Maschine.
            gespann = {**VehicleProfile().__dict__, "name": "Grubber 2,50 m", "width_m": 2.5,
                       "antenna_forward_m": 1.77, "traktor": "John Deere 7530 Premium"}
            store.set_setting("vehicle_profile", gespann)
            store.set_setting("vehicle_profiles", [{"id": "g", **gespann}])
            store.set_setting("vehicle_profile_active", "g")
            store.set_setting(myterra.KATALOG, {"traktoren": ["IHC 956 XL", "John Deere 7530 Premium"]})
            engine = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
            traktoren = engine.list_traktoren()
            self.assertEqual([t["name"] for t in traktoren], ["John Deere 7530 Premium", "IHC 956 XL"])
            self.assertEqual(_aktiv(traktoren)["name"], "John Deere 7530 Premium")
            self.assertTrue(all(t["antenna_forward_m"] == 1.77 for t in traktoren))
            self.assertEqual(engine.profile.width_m, 2.5)
            self.assertEqual(engine.profile.antenna_forward_m, 1.77)
            store.close()


class AbgleichTests(unittest.TestCase):
    def test_tractors_travel_with_the_machines_and_the_newer_wins(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                _client(a) as master, _client(b) as tablet:
            master.post("/api/traktor", json={"name": "John Deere 7530 Premium", "antenna_forward_m": 1.9})
            paket = master.app.state.app.engine.profile_export()
            self.assertIn("traktoren", paket)
            tablet.app.state.app.engine.profile_merge(json.loads(json.dumps(paket)))
            traktoren = tablet.get("/api/traktoren").json()
            jd = next(t for t in traktoren if t["name"] == "John Deere 7530 Premium")
            self.assertEqual(jd["antenna_forward_m"], 1.9)

    def test_an_old_device_sending_machines_with_antenna_does_not_move_the_antenna(self):
        with tempfile.TemporaryDirectory() as ordner, _client(ordner) as c:
            engine = c.app.state.app.engine
            c.post("/api/profile", json={"antenna_forward_m": 1.9})
            aktiv = _aktiv(engine.list_profiles())
            # Ein Tablet mit 1.5.6 schickt dieselbe Maschine neuer, mit alter Antenne darin.
            alt = {k: v for k, v in aktiv.items() if k != "aktiv"}
            alt.update({"width_m": 3.3, "antenna_forward_m": 0.5,
                        "stand": float(aktiv.get("stand") or 0.0) + 10})
            engine.profile_merge({"maschinen": [alt], "geloescht": {}})
            self.assertEqual(engine.profile.width_m, 3.3)
            self.assertEqual(engine.profile.antenna_forward_m, 1.9)


class ArbeitTests(unittest.TestCase):
    def test_a_job_remembers_its_tractor_and_myterra_gets_it(self):
        with tempfile.TemporaryDirectory() as ordner, _client(ordner) as c:
            c.post("/api/traktor", json={"name": "John Deere 7530 Premium"})
            c.post("/api/profile", json={"name": "Grubber 2,50 m", "anbaugeraet": "Grubber 2,50 m"})
            c.post("/api/fields", json={"name": "Hinterm Hof"})
            engine = c.app.state.app.engine
            feld = engine.store.list_fields()[0]
            engine.load_field(feld["id"])
            job = engine.start_job("Grubbern")
            self.assertEqual(job["tractor"], "John Deere 7530 Premium")
            engine.store.update_job(job["id"], ended_at=job["started_at"] + 600, area_ha=1.0)
            daten, _ = myterra.paket_bauen(engine.store, "x", [job["id"]], markieren=False)
            bericht = json.loads(daten)["berichte"][0]
            self.assertEqual(bericht["maschine"], "John Deere 7530 Premium")
            self.assertEqual(bericht["anbaugeraet"], "Grubber 2,50 m")


if __name__ == "__main__":
    unittest.main()
