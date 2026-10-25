"""Zusatzdaten und Konfliktauflösung gegen echte temporäre SQLite-Datenbanken."""
import asyncio
import copy
import time
import unittest
from unittest.mock import patch

from agripilot.storage import Storage
from agripilot.sync import apply_changes, SyncClient
from agripilot import zusatzsync as z
from agripilot.config import Config


def version(t):
    return {"zeit": t, "boundary": [[0, 0], [t, 0], [0, 1]], "area_ha": 1}


def bericht(t=10):
    messung = {"zeit": t, "werte": {"rms_cm": 3, "proben": 100},
               "einstellungen": {"traktor": "t", "geraet": "g", "profil": {"width_m": 3},
                                  "motor": {"max_duty": .55}, "imu": {"aktiv": True}}}
    return {"vorher": messung, "nachher": copy.deepcopy(messung), "besser": True, "uebung": True}


class ZusatzsyncTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Storage(":memory:"), Storage(":memory:")
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)

    def item(self, key, value, stamp=10, device="a"):
        return dict(key=key, value=value, updated_at=stamp, device_id=device)

    def test_unknown_invalid_and_secret_keys_rejected(self):
        items = [self.item("network.sync_token", "do-not-copy"), self.item("feld_einfahrt:../x", {}),
                 self.item("feld_einfahrt:f", {"lat": 100, "lon": 0}),
                 self.item("grenze_verlauf:f", [{"zeit": 1, "boundary": [[float("nan"), 0]]}]),
                 None, self.item("probefahrt_bericht", {})]
        self.assertEqual(z.uebernehmen(self.a, items, "a"), {"angewendet": 0, "abgelehnt": 6})
        self.a.set_setting("network.sync_token", "do-not-copy")
        self.assertEqual(z.exportieren(self.a, device_id="a"), [])

    def test_history_union_bounded_deterministic_and_idempotent(self):
        self.a.set_setting("grenze_verlauf:f", [version(i) for i in range(8)])
        self.b.set_setting("grenze_verlauf:f", [version(i) for i in range(4, 12)])
        aa, bb = z.exportieren(self.a, device_id="a"), z.exportieren(self.b, device_id="b")
        z.uebernehmen(self.a, bb, "a")
        z.uebernehmen(self.b, aa, "b")
        self.assertEqual(self.a.get_setting("grenze_verlauf:f"), self.b.get_setting("grenze_verlauf:f"))
        self.assertEqual([v["zeit"] for v in self.a.get_setting("grenze_verlauf:f")], list(range(11, 1, -1)))
        self.assertEqual(z.uebernehmen(self.a, bb, "a")["angewendet"], 0)

    def test_reports_preserve_own_and_relay_origin(self):
        self.a.set_setting("probefahrt_bericht", bericht(1))
        self.b.set_setting("probefahrt_bericht", bericht(2))
        z.uebernehmen(self.b, z.exportieren(self.a, device_id="a"), "b")
        self.assertEqual(self.b.get_setting("probefahrt_bericht"), bericht(2))
        self.assertEqual(self.b.get_setting("probefahrt_fremd:a")["bericht"]["vorher"]["zeit"], 1)
        z.uebernehmen(self.a, z.exportieren(self.b, device_id="b"), "a")
        self.assertEqual(self.a.get_setting("probefahrt_bericht"), bericht(1))
        self.assertIsNotNone(self.a.get_setting("probefahrt_fremd:b"))
        self.assertIsNone(self.a.get_setting("probefahrt_fremd:a"))

    def test_report_unknown_nested_settings_not_exported(self):
        b = bericht()
        b["network"] = {"sync_token": "do-not-copy"}
        b["vorher"]["einstellungen"]["network"] = b["network"]
        b["vorher"]["einstellungen"]["imu"]["host"] = "do-not-copy"
        b["vorher"]["werte"]["sync_token"] = 123456
        self.a.set_setting("probefahrt_bericht", b)
        exported = z.exportieren(self.a, device_id="a")
        self.assertNotIn("do-not-copy", str(exported))
        self.assertNotIn("sync_token", str(exported))
        self.assertEqual(exported[0]["value"]["vorher"]["einstellungen"]["motor"]["max_duty"], .55)

    def test_entry_origin_timestamp_survives_relay_and_local_edit_wins(self):
        z.uebernehmen(self.b, [self.item("feld_einfahrt:f", {"lat": 48, "lon": 9})], "b")
        self.assertEqual(z.exportieren(self.b, device_id="b")[0]["updated_at"], 10)
        self.b.set_setting("feld_einfahrt:f", {"x": 3, "y": 4})
        z.uebernehmen(self.b, [self.item("feld_einfahrt:f", {"lat": 49, "lon": 10}, 11)], "b")
        self.assertEqual(self.b.get_setting("feld_einfahrt:f"), {"x": 3, "y": 4})

    def field(self):
        f = self.a.save_field(dict(id="f", name="Lokal", datum_lat=48, datum_lon=9))
        return self.a.changes_since("fields", 0)[0]

    def test_conflict_preserves_both_and_resolution_becomes_local_change(self):
        local = self.field()
        incoming = {**local, "name": "Fremd", "updated_at": local["updated_at"] + 1}
        apply_changes(self.a, {"fields": [incoming]})
        c = self.a.get_setting("sync_konflikte")[0]
        self.assertEqual(c["lokal"]["name"], "Lokal")
        self.assertEqual(c["eingehend"]["name"], "Fremd")
        self.assertTrue(c["neuerer_uebernommen"])
        self.assertEqual(self.a.get_field("f")["name"], "Fremd")
        z.loesen(self.a, c["id"], "lokal")
        self.assertEqual(self.a.get_field("f")["name"], "Lokal")
        self.assertGreater(self.a.get_field("f")["updated_at"], incoming["updated_at"])
        self.assertEqual(self.a.get_setting("sync_konflikte")[0]["status"], "geloest")
        with self.assertRaises(ValueError):
            z.loesen(self.a, c["id"], "lokal")

    def test_acknowledged_equal_older_and_received_records_no_conflict(self):
        local = self.field()
        self.a.set_setting("sync_push_cursor", local["updated_at"] + .1)
        apply_changes(self.a, {"fields": [{**local, "name": "Neu", "updated_at": local["updated_at"] + 1}]})
        self.assertEqual(self.a.get_setting("sync_konflikte", []), [])
        self.a.set_setting("sync_push_cursor", 0)
        apply_changes(self.a, {"fields": [{**local, "updated_at": local["updated_at"] - 1}]})
        self.assertEqual(self.a.get_setting("sync_konflikte", []), [])
        self.b.save_field(dict(id="f", name="Lokal", datum_lat=48, datum_lon=9))
        same = self.b.changes_since("fields", 0)[0]
        apply_changes(self.b, {"fields": [{**same, "updated_at": same["updated_at"] + 1}]})
        self.assertEqual(self.b.get_setting("sync_konflikte", []), [])

    def test_resolution_rejects_invalid_selection_and_stale_record(self):
        local = self.field()
        apply_changes(self.a, {"fields": [{**local, "name": "Fremd", "updated_at": local["updated_at"] + 1}]})
        c = self.a.get_setting("sync_konflikte")[0]
        with self.assertRaises(ValueError):
            z.loesen(self.a, c["id"], "anything")
        self.a.save_field({**self.a.get_field("f"), "name": "Später"})
        with self.assertRaises(ValueError):
            z.loesen(self.a, c["id"], "eingehend")
        self.assertEqual(self.a.get_field("f")["name"], "Später")

    def test_exchange_and_readonly_status(self):
        response = z.exchange(self.b, [self.item("grenze_verlauf:f", [version(1)])], device_id="b")
        self.assertIn("zusatzdaten", response)
        cfg = Config()
        client = SyncClient(cfg, self.b)
        before = self.b.db.total_changes
        state = client.status_dict()
        self.assertGreater(state["pending_count"], 0)
        self.assertEqual(state["conflicts_count"], 0)
        self.assertEqual(self.b.db.total_changes, before)
        self.assertNotIn("sync_token", str(state))

    def test_client_posts_extras_and_accepts_reply_only_after_success(self):
        import httpx
        self.a.set_setting("grenze_verlauf:f", [version(1)])
        cfg = Config()
        cfg.network.device_id = "a"
        cfg.network.master_url = "http://master"
        payloads = []
        def handler(request):
            import json
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"server_time": time.time(), "changes": {}, "zusatzdaten": [self.item("probefahrt_bericht", bericht(), device="b")]})
        real = httpx.AsyncClient
        def make_client(**kw):
            return real(transport=httpx.MockTransport(handler), **kw)
        with patch.object(httpx, "AsyncClient", side_effect=make_client):
            result = asyncio.run(SyncClient(cfg, self.a).sync_once())
        self.assertTrue(result["ok"])
        self.assertEqual(payloads[0]["zusatzdaten"][0]["key"], "grenze_verlauf:f")
        self.assertIsNotNone(self.a.get_setting("probefahrt_fremd:b"))


    def test_remote_future_clock_does_not_create_false_conflict(self):
        local = self.field()
        self.a.set_setting("sync_push_cursor", time.time() + 1)
        future = time.time() + 100
        apply_changes(self.a, {"fields": [{**local, "updated_at": future, "name": "Remote1"}]})
        self.a.set_setting("sync_push_cursor", 0)
        apply_changes(self.a, {"fields": [{**local, "updated_at": future + 1, "name": "Remote2"}]})
        self.assertEqual(self.a.get_setting("sync_konflikte", []), [])

    def test_conflict_archive_bounded_and_incoming_resolution(self):
        local = self.field()
        for i in range(55):
            self.a.db.execute("UPDATE fields SET updated_at=?,name=? WHERE id='f'", (time.time() + i * 2, str(i)))
            self.a.db.execute("DELETE FROM sync_received")
            self.a.db.commit()
            row = dict(self.a.db.execute("SELECT * FROM fields WHERE id='f'").fetchone())
            apply_changes(self.a, {"fields": [{**row, "updated_at": row["updated_at"] + 1, "name": "Remote" + str(i)}]})
        archive = self.a.get_setting("sync_konflikte")
        self.assertEqual(len(archive), 50)
        z.loesen(self.a, archive[-1]["id"], "eingehend")
        self.assertEqual(self.a.get_field("f")["name"], "Remote54")

    def test_job_conflict_resolution_keeps_union_coverage(self):
        from agripilot.coverage import CoverageMap
        import base64
        self.field()
        job = self.a.start_job("f", "a")
        a, b = CoverageMap(), CoverageMap()
        a.cells.add((1, 1))
        b.cells.add((2, 2))
        self.a.update_job(job["id"], coverage=a.pack(), operation="Lokal")
        record = self.a.changes_since("jobs", 0)[0]
        incoming = {**record, "operation": "Remote", "updated_at": record["updated_at"] + 1,
                    "coverage": base64.b64encode(b.pack()).decode()}
        apply_changes(self.a, {"jobs": [incoming]})
        archive = self.a.get_setting("sync_konflikte")
        self.assertEqual(archive[-1]["lokal"]["coverage"], record["coverage"])
        self.assertEqual(archive[-1]["eingehend"]["coverage"], incoming["coverage"])
        z.loesen(self.a, archive[-1]["id"], "lokal")
        self.assertEqual(self.a.get_job(job["id"])["operation"], "Lokal")
        self.assertEqual(CoverageMap.unpack(self.a.get_job_coverage(job["id"])).cells, {(1, 1), (2, 2)})

    def test_failed_handshake_keeps_cursors_and_local_extras(self):
        import httpx
        self.a.set_setting("grenze_verlauf:f", [version(1)])
        cfg = Config()
        cfg.network.master_url = "http://master"
        self.a.set_setting("sync_push_cursor", 5)
        real = httpx.AsyncClient
        def handler(request):
            return httpx.Response(503, json={"zusatzdaten": [self.item("feld_einfahrt:f", {"x": 1, "y": 2})]})
        with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: real(transport=httpx.MockTransport(handler), **kw)):
            result = asyncio.run(SyncClient(cfg, self.a).sync_once())
        self.assertFalse(result["ok"])
        self.assertEqual(self.a.get_setting("sync_push_cursor"), 5)
        self.assertIsNone(self.a.get_setting("feld_einfahrt:f"))

    def test_line_conflict_geometry_and_restore(self):
        self.field()
        self.a.save_line(dict(id="l", field_id="f", name="AB", mode="ab", points=[[0, 0], [0, 100]], spacing_m=3))
        local = self.a.changes_since("lines", 0)[0]
        incoming = {**local, "points": "[[1,0],[1,100]]", "updated_at": local["updated_at"] + 1}
        apply_changes(self.a, {"lines": [incoming]})
        conflict = self.a.get_setting("sync_konflikte")[0]
        self.assertEqual(conflict["tabelle"], "lines")
        z.loesen(self.a, conflict["id"], "lokal")
        self.assertEqual(self.a.get_line("l")["points"], [[0, 0], [0, 100]])

    def test_master_worker_followups_without_push_cursor(self):
        # Master hat keinen Push-Cursor: Empfangsherkunft verhindert Scheinkonflikte.
        self.b.save_field(dict(id="f", name="Worker1", datum_lat=48, datum_lon=9))
        first = self.b.changes_since("fields", 0)[0]
        first["updated_at"] = time.time() - 10
        apply_changes(self.a, {"fields": [first]})
        second = {**first, "name": "Worker2", "updated_at": first["updated_at"] + 1}
        apply_changes(self.a, {"fields": [second]})
        self.assertEqual(self.a.get_setting("sync_konflikte", []), [])
        self.assertEqual(self.a.get_field("f")["name"], "Worker2")
        # Echte neue lokale Bearbeitung nach Empfang muss trotzdem archiviert werden.
        stamp = time.time() + 1
        self.a.db.execute("UPDATE fields SET name='Masterlokal',updated_at=? WHERE id='f'", (stamp,))
        self.a.db.commit()
        apply_changes(self.a, {"fields": [{**second, "name": "Worker3", "updated_at": stamp + 1}]})
        conflict = self.a.get_setting("sync_konflikte")[0]
        self.assertEqual(conflict["lokal"]["name"], "Masterlokal")
        self.assertEqual(conflict["eingehend"]["name"], "Worker3")

    def test_exercise_client_never_connects_or_writes(self):
        import httpx
        cfg = Config()
        cfg.network.device_id = "uebung-lokal"
        client = SyncClient(cfg, self.a, engine=object())
        before = self.a.db.total_changes
        with patch.object(httpx, "AsyncClient", side_effect=AssertionError("Übung darf nicht verbinden")):
            self.assertFalse(asyncio.run(client.sync_once())["ok"])
            self.assertFalse(asyncio.run(client.live_once()))
            asyncio.run(client.run())
        self.assertFalse(client.running)
        self.assertEqual(self.a.db.total_changes, before)
        self.assertIn("Übungsmodus", client.status)

    def test_entry_delete_propagates_and_old_points_never_resurrect(self):
        key = "feld_einfahrt:f"
        self.a.set_setting(key, {"lat": 48, "lon": 9, "zeit": 10})
        z.uebernehmen(self.b, z.exportieren(self.a, device_id="a"), "b")
        self.assertEqual(self.b.get_setting(key)["zeit"], 10)
        self.a.set_setting(key, {"geloescht": True, "zeit": 20})
        z.uebernehmen(self.b, z.exportieren(self.a, device_id="a"), "b")
        self.assertEqual(self.b.get_setting(key), {"geloescht": True, "zeit": 20})
        # Empfangs-/Transportzeit darf die alte Version nicht wieder neu machen.
        old = self.item(key, {"lat": 48, "lon": 9, "zeit": 10}, stamp=time.time() + 1000)
        self.assertEqual(z.uebernehmen(self.b, [old], "b")["angewendet"], 0)
        relay = z.exportieren(self.b, device_id="b")
        self.assertEqual(relay[0]["updated_at"], 20)
        z.uebernehmen(self.a, relay, "a")
        self.assertTrue(self.a.get_setting(key)["geloescht"])
        # Eine bewusst neuere Setzung bleibt erlaubt.
        z.uebernehmen(self.b, [self.item(key, {"localx": 3, "localy": 4, "zeit": 30}, stamp=30)], "b")
        self.assertEqual(self.b.get_setting(key), {"localx": 3, "localy": 4, "zeit": 30})

    def test_entry_delete_wins_equal_version_in_both_directions(self):
        key = "feld_einfahrt:f"
        point = self.item(key, {"lat": 48, "lon": 9, "zeit": 10}, stamp=10)
        deleted = self.item(key, {"geloescht": True, "zeit": 10}, stamp=10)
        z.uebernehmen(self.a, [point, deleted], "a")
        z.uebernehmen(self.b, [deleted, point], "b")
        self.assertEqual(self.a.get_setting(key), self.b.get_setting(key))
        self.assertEqual(self.a.get_setting(key), {"geloescht": True, "zeit": 10})

    def test_entry_tombstone_validation_and_whitelist(self):
        key = "feld_einfahrt:f"
        invalid = [{"geloescht": True}, {"geloescht": True, "zeit": float("inf")},
                   {"geloescht": True, "zeit": -1}, {"geloescht": "true", "zeit": 1},
                   {"lat": 48, "lon": 9, "zeit": True}]
        self.assertEqual(z.uebernehmen(self.a, [self.item(key, v) for v in invalid], "a")["abgelehnt"], 5)
        z.uebernehmen(self.a, [self.item(key, {"geloescht": True, "zeit": 10, "network": "do-not-copy", "lat": 48})], "a")
        self.assertEqual(self.a.get_setting(key), {"geloescht": True, "zeit": 10})

if __name__ == "__main__":
    unittest.main()
