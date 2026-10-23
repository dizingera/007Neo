"""Hindernisse im Feld und Fernansicht der Maschinen."""

import asyncio
import tempfile
import time
import unittest
from unittest import mock

import httpx

from agripilot import hindernisse
from agripilot.flotte import Flotte
from agripilot.nmea import Fix
from agripilot.sync import SyncClient
from test_tablet_lenkung import abgleichen, geraet


class HindernisGeometrie(unittest.TestCase):
    MAST = {"id": "m", "name": "Mast", "x": 0.0, "y": 20.0, "radius_m": 0.5}

    def test_ahead_in_the_swath_warns_with_distance(self):
        w = hindernisse.warnung((0.0, 0.0), 0.0, 3.0, 6.0, [self.MAST])
        self.assertEqual(w["name"], "Mast")
        self.assertAlmostEqual(w["abstand_m"], 19.5, places=1)

    def test_beside_the_swath_or_behind_is_quiet(self):
        seitlich = {**self.MAST, "x": 6.0}
        self.assertIsNone(hindernisse.warnung((0.0, 0.0), 0.0, 2.0, 6.0, [seitlich]))
        self.assertIsNone(hindernisse.warnung((0.0, 30.0), 0.0, 2.0, 6.0, [self.MAST]))

    def test_reach_grows_with_speed(self):
        weit = {**self.MAST, "y": 60.0}
        self.assertIsNone(hindernisse.warnung((0.0, 0.0), 0.0, 2.0, 6.0, [weit]))
        self.assertIsNotNone(hindernisse.warnung((0.0, 0.0), 0.0, 8.0, 6.0, [weit]))

    def test_the_nearest_is_reported(self):
        nah = {**self.MAST, "id": "n", "name": "Stein", "y": 8.0}
        self.assertEqual(hindernisse.warnung((0, 0), 0.0, 2.0, 6.0, [self.MAST, nah])["name"], "Stein")

    def test_on_a_turn_route(self):
        bogen = [(0.0, 0.0), (0.0, 10.0), (5.0, 15.0), (10.0, 10.0)]
        self.assertIsNotNone(hindernisse.auf_route(bogen, 3.0, [{**self.MAST, "x": 5.0, "y": 16.0}]))
        self.assertIsNone(hindernisse.auf_route(bogen, 3.0, [{**self.MAST, "x": 30.0, "y": 30.0}]))


class FlotteAblage(unittest.TestCase):
    def test_reports_age_and_forgets(self):
        f = Flotte()
        f.melden("a", {"name": "JD", "lat": 50.0, "lon": 10.0, "geheim": 1}, jetzt=100.0)
        liste = f.liste(jetzt=110.0)
        self.assertEqual(liste[0]["alter_s"], 10.0)
        self.assertNotIn("geheim", liste[0])
        self.assertEqual(f.liste(jetzt=100.0 + 16 * 60), [])

    def test_rejects_nonsense(self):
        f = Flotte()
        f.melden("a", {"lat": "x", "lon": 10})
        f.melden("b", {"lat": 95.0, "lon": 10})
        f.melden("", {"lat": 50.0, "lon": 10})
        self.assertEqual(f.liste(), [])

    def test_excludes_the_asker(self):
        f = Flotte()
        f.melden("a", {"lat": 50.0, "lon": 10.0})
        f.melden("b", {"lat": 50.0, "lon": 10.1})
        self.assertEqual([m["id"] for m in f.liste(ohne="a")], ["b"])


def live_abgleich(tablet, master):
    anwendung = tablet.app.state.app
    abgleich = SyncClient(anwendung.config, anwendung.store, anwendung.engine)
    echter_client = httpx.AsyncClient

    def lokaler_transport(**kwargs):
        return echter_client(transport=httpx.ASGITransport(app=master.app), **kwargs)

    with mock.patch("httpx.AsyncClient", side_effect=lokaler_transport):
        return asyncio.run(abgleich.live_once())


class ApiUndAbgleich(unittest.TestCase):
    def _feld(self, client):
        app = client.app.state.app
        feld = app.store.save_field(dict(name="Testfeld", datum_lat=50, datum_lon=10,
                                         boundary=[[0, 0], [100, 0], [100, 100], [0, 100]]))
        app.engine.load_field(feld["id"])
        app.engine.on_fix(Fix(lat=50.0002, lon=10.0002, fix_quality=4, speed_ms=2.0,
                              course_deg=0.0, received_at=time.time()))
        app.engine.on_fix(Fix(lat=50.00022, lon=10.0002, fix_quality=4, speed_ms=2.0,
                              course_deg=0.0, received_at=time.time()))
        return feld

    def test_mark_list_delete_and_sync_to_another_tablet(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as c:
            with geraet(a, "master", "buero") as master, geraet(b, "client", "tablet-a") as links, \
                    geraet(c, "client", "tablet-b") as rechts:
                feld = self._feld(links)
                r = links.post("/api/hindernisse", json={"name": "Mast", "seite": "rechts", "radius_m": 1.5})
                self.assertEqual(r.status_code, 200, r.text)
                hid = r.json()["data"]["id"]
                self.assertEqual(len(links.get(f"/api/hindernisse?field_id={feld['id']}").json()), 1)
                self.assertEqual(links.get("/api/state").json()["hindernisse"][0]["name"], "Mast")
                abgleichen(links, master)
                abgleichen(rechts, master)
                self.assertEqual([h["id"] for h in rechts.get("/api/hindernisse").json()], [hid])
                self.assertEqual(links.delete(f"/api/hindernisse/{hid}").status_code, 200)
                self.assertEqual(links.get("/api/hindernisse").json(), [])

    def test_without_a_field_there_is_no_obstacle(self):
        with tempfile.TemporaryDirectory() as a:
            with geraet(a, "client", "tablet-a") as tablet:
                self.assertEqual(tablet.post("/api/hindernisse", json={}).status_code, 400)

    def test_tablets_see_each_other_through_the_master(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as c:
            with geraet(a, "master", "buero") as master, geraet(b, "client", "tablet-a") as links, \
                    geraet(c, "client", "tablet-b") as rechts:
                for tablet in (links, rechts):     # echte Empfänger, kein Simulator
                    tablet.app.state.app.config.gnss.source = "serial"
                self._feld(links)
                self._feld(rechts)
                abgleichen(links, master)          # der Master kennt das Feld erst nach dem Abgleich
                self.assertTrue(live_abgleich(links, master))
                self.assertTrue(live_abgleich(rechts, master))
                self.assertTrue(live_abgleich(links, master))
                flotte = links.get("/api/state").json()["flotte"]
                self.assertEqual([m["id"] for m in flotte], ["tablet-b"])
                self.assertIn("x", flotte[0])
                uebersicht = master.get("/api/flotte").json()
                namen = sorted(m["id"] for m in uebersicht["maschinen"])
                self.assertEqual(namen, ["tablet-a", "tablet-b"])
                self.assertEqual(len(next(iter(uebersicht["felder"].values()))["grenze"]), 4)

    def test_the_office_in_the_simulator_sees_the_tablets_but_is_not_seen(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as c:
            with geraet(a, "master", "pi") as master, geraet(b, "client", "tablet-a") as tablet, \
                    geraet(c, "client", "buero") as buero:
                tablet.app.state.app.config.gnss.source = "serial"
                self.assertEqual(buero.app.state.app.config.gnss.source, "simulator")
                self._feld(tablet)
                self._feld(buero)
                self.assertTrue(live_abgleich(tablet, master))
                self.assertTrue(live_abgleich(buero, master))
                self.assertTrue(live_abgleich(tablet, master))
                self.assertEqual([m["id"] for m in buero.get("/api/state").json()["flotte"]],
                                 ["tablet-a"])
                self.assertEqual(tablet.get("/api/state").json()["flotte"], [])
                self.assertEqual([m["id"] for m in master.get("/api/flotte").json()["maschinen"]],
                                 ["tablet-a"])


if __name__ == "__main__":
    unittest.main()
