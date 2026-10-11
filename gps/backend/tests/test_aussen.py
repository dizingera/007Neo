"""Abgleich von unterwegs: Arbeitercode, und von außen nur der Abgleich."""

import tempfile
import unittest
import warnings

from agripilot import config as config_module

TUNNEL = {"cf-connecting-ip": "203.0.113.7", "cf-ray": "8a1b2c3d4e5f-FRA"}
ABGLEICH = {"device": {"id": "t1", "name": "Tablet", "role": "client"}, "since": 0, "changes": {}}


class AussenTests(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore")
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _client(self, token="", rolle="master"):
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.server.data_dir = self.dir.name
        config.network.device_id = "buero"
        config.network.role = rolle
        config.network.sync_token = token
        return TestClient(create_app(config))

    # -- ohne gesetzten Code: Heimnetz wie bisher, von außen nichts -------------

    def test_the_home_network_syncs_without_a_code_as_before(self):
        with self._client() as c:
            self.assertEqual(c.post("/api/sync", json=ABGLEICH).status_code, 200)

    def test_from_outside_nothing_works_without_a_code_on_the_master(self):
        with self._client() as c:
            antwort = c.post("/api/sync", json=ABGLEICH, headers=TUNNEL)
        self.assertEqual(antwort.status_code, 401)

    # -- mit Code ----------------------------------------------------------------

    def test_with_a_code_the_right_one_is_needed_even_at_home(self):
        with self._client("K7M2-9PQX") as c:
            self.assertEqual(c.post("/api/sync", json=ABGLEICH).status_code, 401)
            self.assertEqual(c.post("/api/sync", json=ABGLEICH,
                                    headers={"X-AgriPilot-Token": "falsch"}).status_code, 401)
            self.assertEqual(c.post("/api/sync", json=ABGLEICH,
                                    headers={"X-AgriPilot-Token": "K7M2-9PQX"}).status_code, 200)

    def test_the_code_is_forgiving_about_case_and_spaces(self):
        with self._client("K7M2-9PQX") as c:
            for getippt in ("k7m2-9pqx", " K7M2-9PQX ", "K7M2 -9PQX", "k7m29pqx"):
                self.assertEqual(c.post("/api/sync", json=ABGLEICH,
                                        headers={"X-AgriPilot-Token": getippt}).status_code, 200, getippt)

    def test_from_outside_with_the_right_code_the_sync_works(self):
        with self._client("K7M2-9PQX") as c:
            antwort = c.post("/api/sync", json=ABGLEICH,
                             headers={**TUNNEL, "X-AgriPilot-Token": "K7M2-9PQX"})
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("changes", antwort.json())

    def test_from_outside_with_a_wrong_code_it_is_refused(self):
        with self._client("K7M2-9PQX") as c:
            antwort = c.post("/api/sync", json=ABGLEICH, headers={**TUNNEL, "X-AgriPilot-Token": "raten"})
        self.assertEqual(antwort.status_code, 401)

    # -- von außen nur der Abgleich ----------------------------------------------

    def test_from_outside_the_controls_are_not_reachable(self):
        with self._client("K7M2-9PQX") as c:
            kopf = {**TUNNEL, "X-AgriPilot-Token": "K7M2-9PQX"}      # selbst mit dem richtigen Code
            for methode, pfad in (("get", "/"), ("get", "/api/system"), ("get", "/api/state"),
                                  ("post", "/api/steering/arm"), ("get", "/api/settings"),
                                  ("post", "/api/settings"), ("get", "/api/daten/export"),
                                  ("post", "/api/update/online"), ("get", "/api/fields")):
                antwort = getattr(c, methode)(pfad, headers=kopf)
                self.assertEqual(antwort.status_code, 403, f"{methode} {pfad}")

    def test_at_home_everything_still_works(self):
        with self._client("K7M2-9PQX") as c:
            self.assertEqual(c.get("/api/system").status_code, 200)
            self.assertEqual(c.get("/").status_code, 200)

    def test_other_proxy_headers_count_as_outside_too(self):
        with self._client("K7M2-9PQX") as c:
            self.assertEqual(c.get("/api/system", headers={"x-forwarded-for": "1.2.3.4"}).status_code, 403)

    def test_the_master_shows_the_code_only_at_home(self):
        with self._client("K7M2-9PQX") as c:
            self.assertEqual(c.get("/api/system").json()["arbeitercode"], "K7M2-9PQX")
            self.assertEqual(c.get("/api/system", headers=TUNNEL).status_code, 403)

    def test_a_worker_does_not_show_a_code(self):
        with self._client("K7M2-9PQX", rolle="client") as c:
            self.assertEqual(c.get("/api/system").json()["arbeitercode"], "")


class WorkerClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_worker_sends_the_code_and_names_a_wrong_one(self):
        from unittest import mock
        from agripilot import sync
        from agripilot.storage import Storage
        import httpx

        gesehen = {}

        class Antwort:
            def __init__(self, code):
                self.status_code = code

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError("x", request=mock.Mock(), response=self)

            def json(self):
                return {"server_time": 1.0, "changes": {}}

        class Verbindung:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None):
                gesehen["headers"] = headers
                return Antwort(gesehen["code"])

        with tempfile.TemporaryDirectory() as ordner:
            store = Storage(ordner + "/c.db")
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.network.role = "client"
            config.network.master_url = "https://agripilot.example"
            config.network.sync_token = "K7M2-9PQX"
            klient = sync.SyncClient(config, store)
            with mock.patch.object(httpx, "AsyncClient", Verbindung):
                gesehen["code"] = 200
                self.assertTrue((await klient.sync_once())["ok"])
                self.assertEqual(gesehen["headers"], {"X-AgriPilot-Token": "K7M2-9PQX"})
                gesehen["code"] = 401
                self.assertFalse((await klient.sync_once())["ok"])
                self.assertEqual(klient.status, "Arbeitercode falsch oder fehlt")
            store.close()


if __name__ == "__main__":
    unittest.main()
