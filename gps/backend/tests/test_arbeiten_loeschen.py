"""Arbeiten löschen: einzeln, je Feld, alle - mit Löschvermerk für den Abgleich."""

import tempfile
import unittest

from agripilot.coverage import CoverageMap, build_sections
from test_tablet_lenkung import abgleichen, geraet


def feld_mit_arbeiten(client, name, anzahl):
    app = client.app.state.app
    feld = app.store.save_field(dict(name=name, datum_lat=50, datum_lon=10,
                                     boundary=[[0, 0], [100, 0], [100, 100], [0, 100]]))
    arbeiten = []
    for i in range(anzahl):
        job = app.store.start_job(feld["id"], "tablet", "Spritze", f"Arbeit {i}", None)
        karte = CoverageMap(0.5)
        karte.add_swath((10.0 + i * 10, 0.0), (10.0 + i * 10, 50.0), 0.0, build_sections(6.0, 1))
        import time
        app.store.update_job(job["id"], ended_at=time.time(), area_ha=karte.area_ha, distance_m=50.0,
                             working_time_s=60.0, coverage=karte.pack())
        arbeiten.append(job["id"])
    return feld, arbeiten


class ArbeitenLoeschen(unittest.TestCase):
    def test_one_job_goes_and_its_area_leaves_the_map(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            feld, ids = feld_mit_arbeiten(c, "A", 2)
            c.post(f"/api/fields/{feld['id']}/load")
            engine = c.app.state.app.engine
            vorher = engine.coverage.area_ha
            r = c.delete(f"/api/jobs/{ids[0]}")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["data"]["geloescht"], 1)
            self.assertTrue(r.json()["data"]["karte_neu"])
            self.assertEqual([j["id"] for j in c.get("/api/jobs").json()], [ids[1]])
            self.assertAlmostEqual(engine.coverage.area_ha, vorher / 2, places=3)

    def test_a_running_job_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            feld, ids = feld_mit_arbeiten(c, "A", 1)
            c.post(f"/api/fields/{feld['id']}/load")
            laufend = c.post("/api/job/start", json={"operation": "Säen"}).json()["data"]["id"]
            self.assertEqual(c.delete(f"/api/jobs/{laufend}").status_code, 409)
            r = c.delete("/api/jobs", params={"alle": "true"}).json()["data"]
            self.assertEqual(r["geloescht"], 1)
            self.assertTrue(r["laufend_uebersprungen"])
            self.assertFalse(r["karte_neu"])            # die laufende Arbeit malt weiter
            self.assertEqual([j["id"] for j in c.get("/api/jobs").json()], [laufend])

    def test_per_field_or_all(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            feld_a, _ = feld_mit_arbeiten(c, "A", 2)
            feld_b, ids_b = feld_mit_arbeiten(c, "B", 3)
            self.assertEqual(c.delete("/api/jobs").status_code, 400)       # weder Feld noch alle
            r = c.delete("/api/jobs", params={"field_id": feld_a["id"]}).json()["data"]
            self.assertEqual(r["geloescht"], 2)
            self.assertEqual(sorted(j["id"] for j in c.get("/api/jobs").json()), sorted(ids_b))
            r = c.delete("/api/jobs", params={"alle": "true"}).json()["data"]
            self.assertEqual(r["geloescht"], 3)
            self.assertEqual(c.get("/api/jobs").json(), [])

    def test_the_deletion_reaches_the_master_and_does_not_come_back(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with geraet(a, "master", "pi") as master, geraet(b, "client", "tablet") as tablet:
                _, ids = feld_mit_arbeiten(tablet, "A", 2)
                abgleichen(tablet, master)
                self.assertEqual(len(master.get("/api/jobs").json()), 2)
                tablet.delete(f"/api/jobs/{ids[0]}")
                abgleichen(tablet, master)
                abgleichen(tablet, master)                                  # und kommt nicht zurück
                self.assertEqual([j["id"] for j in master.get("/api/jobs").json()], [ids[1]])
                self.assertEqual([j["id"] for j in tablet.get("/api/jobs").json()], [ids[1]])


if __name__ == "__main__":
    unittest.main()
