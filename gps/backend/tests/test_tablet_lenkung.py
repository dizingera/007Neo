"""Lokale Lenkabstimmung auf dem Traktor-Tablet vor fremden Ständen schützen."""

import asyncio
import base64
from contextlib import contextmanager
import math
import tempfile
import unittest
from unittest import mock
import warnings

import httpx

from agripilot import config as config_module
from agripilot.server import create_app
from agripilot.sync import SyncClient
from fastapi.testclient import TestClient


@contextmanager
def geraet(ordner, rolle, kennung):
    warnings.filterwarnings("ignore")
    config = config_module.load("/kein-solcher-pfad.yaml")
    config.server.data_dir = ordner
    config.network.role = rolle
    config.network.device_id = kennung
    config.network.device_name = kennung
    config.network.master_url = "http://buero.test"
    app = create_app(config)
    # Ohne Lifespan: ausschließlich HTTP/SQLite, keine Quellen, Motoren oder Hintergrundnetze.
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        app.state.app.store.close()


def abgleichen(tablet, master):
    anwendung = tablet.app.state.app
    abgleich = SyncClient(anwendung.config, anwendung.store, anwendung.engine)
    echter_client = httpx.AsyncClient

    def lokaler_transport(**kwargs):
        return echter_client(transport=httpx.ASGITransport(app=master.app), **kwargs)

    with mock.patch("httpx.AsyncClient", side_effect=lokaler_transport):
        ergebnis = asyncio.run(abgleich.sync_once())
    if not ergebnis.get("ok"):
        raise AssertionError(ergebnis)
    return ergebnis


def profil(client):
    return client.get("/api/state").json()["profile"]


class TabletLenkungTests(unittest.TestCase):
    def test_zwei_tablets_behalten_ihre_abstimmung_fuer_dieselbe_traktorkennung(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as c:
            with geraet(a, "master", "buero") as master, geraet(b, "client", "tablet-a") as links, \
                    geraet(c, "client", "tablet-b") as rechts:
                links.post("/api/traktor", json={"steer_gain": 0.55, "actuator_latency_ms": 120})
                rechts.post("/api/traktor", json={"steer_gain": 1.1, "actuator_latency_ms": 200})
                for _ in range(3):
                    abgleichen(links, master)
                    abgleichen(rechts, master)
                self.assertEqual(profil(links)["steer_gain"], 0.55)
                self.assertEqual(profil(rechts)["steer_gain"], 1.1)
                self.assertEqual(profil(links)["actuator_latency_ms"], 120)
                self.assertEqual(profil(rechts)["actuator_latency_ms"], 200)
            with geraet(b, "client", "tablet-a") as neu:
                self.assertEqual(profil(neu)["steer_gain"], 0.55)

    def test_datenpaket_aendert_geraetedaten_aber_keine_vorhandene_tabletlenkung(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                geraet(a, "master", "buero") as master, geraet(b, "client", "tablet-a") as tablet:
            tablet.post("/api/traktor", json={"steer_gain": 0.55, "antenna_right_m": 0.12})
            abgleichen(tablet, master)
            geraet_id = next(p["id"] for p in tablet.get("/api/profiles").json() if p["aktiv"])
            master.post(f"/api/profiles/{geraet_id}/select")
            master.post("/api/profile", json={"name": "Geteiltes Gerät", "width_m": 7.0})
            master.post("/api/traktor", json={"steer_gain": 1.8, "antenna_right_m": 0.0})
            master.post("/api/traktoren", json={"name": "Neuer Katalogtraktor"})
            paket = master.get("/api/daten/export")
            self.assertEqual(paket.status_code, 200)
            antwort = tablet.post("/api/daten/import", json={"zip": base64.b64encode(paket.content).decode()})
            self.assertEqual(antwort.status_code, 200, antwort.text)
            self.assertEqual(profil(tablet)["steer_gain"], 0.55)
            self.assertEqual(profil(tablet)["antenna_right_m"], 0.12)
            self.assertEqual(profil(tablet)["width_m"], 7.0)
            self.assertEqual(profil(tablet)["name"], "Geteiltes Gerät")
            self.assertIn("Neuer Katalogtraktor", [t["name"] for t in tablet.get("/api/traktoren").json()])

    def test_master_offline_aendert_keine_tabletlenkung(self):
        with tempfile.TemporaryDirectory() as a, geraet(a, "client", "tablet-a") as tablet:
            tablet.post("/api/traktor", json={"steer_gain": 0.55})
            tablet.post("/api/abstimmung", json={"modus": "automatisch"})
            anwendung = tablet.app.state.app
            abgleich = SyncClient(anwendung.config, anwendung.store, anwendung.engine)
            with mock.patch("httpx.AsyncClient", side_effect=httpx.ConnectError("Master offline")):
                self.assertFalse(asyncio.run(abgleich.sync_once())["ok"])
            self.assertEqual(profil(tablet)["steer_gain"], 0.55)
            self.assertEqual(tablet.get("/api/abstimmung").json()["data"]["modus"], "automatisch")

    def test_buero_loeschung_schaltet_den_aktiven_tablettraktor_nicht_um(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                geraet(a, "master", "buero") as master, geraet(b, "client", "tablet-a") as tablet:
            tablet.post("/api/traktor", json={"steer_gain": 0.55})
            aktiv = next(t for t in tablet.get("/api/traktoren").json() if t["aktiv"])
            master.post("/api/traktoren", json={"name": "Zweiter Traktor"})
            self.assertEqual(master.delete(f'/api/traktoren/{aktiv["id"]}').status_code, 200)
            abgleichen(tablet, master)
            nachher = next(t for t in tablet.get("/api/traktoren").json() if t["aktiv"])
            self.assertEqual(nachher["id"], aktiv["id"])
            self.assertEqual(profil(tablet)["steer_gain"], 0.55)
            self.assertNotIn(aktiv["id"], tablet.app.state.app.engine.profile_export()["traktoren_geloescht"])
            zweiter = next(t for t in tablet.get("/api/traktoren").json() if t["name"] == "Zweiter Traktor")
            self.assertEqual(tablet.post(f'/api/traktoren/{zweiter["id"]}/select').status_code, 200)

    def test_automatisch_gelernte_tabletwerte_erreichen_den_master_und_bleiben_nach_neustart(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with geraet(a, "master", "buero") as master, geraet(b, "client", "tablet-a") as tablet:
                tablet.post("/api/traktor", json={"steer_gain": 1.0})
                abgleichen(tablet, master)
                vorher = next(t for t in tablet.get("/api/traktoren").json() if t["aktiv"])
                maschinenstand = tablet.app.state.app.engine.profile_export()["maschinen"][0].get("stand", 0.0)
                tablet.post("/api/abstimmung", json={"modus": "automatisch"})
                for i in range(220):
                    t = 1000 + i / 10
                    tablet.app.state.app.engine.abstimmung.beobachten(
                        t, 0.08 * math.sin(2 * math.pi * t / 5), 2.0, True, True)
                nachher = next(t for t in tablet.get("/api/traktoren").json() if t["aktiv"])
                self.assertAlmostEqual(profil(tablet)["steer_gain"], 0.85)
                self.assertGreater(nachher["stand"], vorher["stand"])
                self.assertEqual(tablet.app.state.app.engine.profile_export()["maschinen"][0].get("stand", 0.0), maschinenstand)
                abgleichen(tablet, master)
                self.assertAlmostEqual(profil(master)["steer_gain"], 0.85)
            with geraet(b, "client", "tablet-a") as neu:
                self.assertAlmostEqual(profil(neu)["steer_gain"], 0.85)
                self.assertEqual(neu.get("/api/abstimmung").json()["data"]["modus"], "automatisch")

    def test_neuerer_buerostand_ersetzt_keine_lokalen_traktorwerte(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                geraet(a, "master", "buero") as master, geraet(b, "client", "tablet-a") as tablet:
            werte = {"steer_gain": 0.55, "actuator_latency_ms": 120,
                     "antenna_forward_m": 1.85, "max_steer_left_deg": 27.0,
                     "regelart": "voraus", "lenk_traegheit_s": 0.6}
            self.assertEqual(tablet.post("/api/traktor", json=werte).status_code, 200)
            master.post("/api/traktor", json={"name": "Traktor vom Büro", "steer_gain": 1.8,
                                               "actuator_latency_ms": 0, "antenna_forward_m": 0.5})
            abgleichen(tablet, master)
            stand = profil(tablet)
            for schluessel, wert in werte.items():
                self.assertEqual(stand[schluessel], wert, schluessel)
            self.assertEqual(tablet.get("/api/state").json()["traktor"], "Traktor vom Büro")


if __name__ == "__main__":
    unittest.main()


class LenkreglerAusDemBuero(unittest.TestCase):
    """Ab 1.5.83: den Lenkregler im Büro einstellen, er kommt in der Kabine an -
    aber nur, was im Büro bewusst eingestellt wurde, nicht die Werte anderer Tablets."""

    def test_buero_setzt_den_lenkregler_fuer_alle_kabinen_ohne_den_einbau_anzufassen(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as c:
            with geraet(a, "master", "buero") as master, geraet(b, "client", "tablet-a") as links, \
                    geraet(c, "client", "tablet-b") as rechts:
                links.post("/api/traktor", json={"antenna_right_m": 0.12, "cerea_lenk_aggr": 70})
                for _ in range(2):
                    abgleichen(links, master)
                    abgleichen(rechts, master)
                # Was Tablet A für sich eingestellt hat, bleibt bei A.
                self.assertEqual(profil(links)["cerea_lenk_aggr"], 70)
                self.assertEqual(profil(rechts)["cerea_lenk_aggr"], 100)
                # Jetzt stellt das Büro den Lenkregler ein.
                master.post("/api/traktor", json={"regelart": "cerea", "cerea_lenk_aggr": 80,
                                                  "cerea_d": 0.3})
                for _ in range(2):
                    abgleichen(links, master)
                    abgleichen(rechts, master)
                for tablet in (links, rechts):
                    self.assertEqual(profil(tablet)["regelart"], "cerea")
                    self.assertEqual(profil(tablet)["cerea_lenk_aggr"], 80)
                    self.assertEqual(profil(tablet)["cerea_d"], 0.3)
                self.assertEqual(profil(links)["antenna_right_m"], 0.12)   # Einbau bleibt
                # Danach stellt Kabine B selbst nach: das bleibt dort und wandert nicht zu A.
                rechts.post("/api/traktor", json={"cerea_lenk_aggr": 60})
                for _ in range(2):
                    abgleichen(rechts, master)
                    abgleichen(links, master)
                self.assertEqual(profil(rechts)["cerea_lenk_aggr"], 60)
                self.assertEqual(profil(links)["cerea_lenk_aggr"], 80)
