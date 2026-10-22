"""Diagnosepaket, Genauigkeit je Arbeit, Luftbild."""

import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from agripilot import diagnose, luftbild
from agripilot.genauigkeit import Genauigkeit
from agripilot.nmea import Fix
from test_tablet_lenkung import geraet

FELD = {"id": "f1", "name": "Am Bach", "datum_lat": 48.4, "datum_lon": 9.6,
        "boundary": [[0, 0], [300, 0], [300, 200], [0, 200]]}
JPEG = b"\xff\xd8\xff\xe0" + b"bild" * 100


class Schwaerzen(unittest.TestCase):
    def test_secrets_are_blacked_out_at_any_depth(self):
        roh = {"corrections.password": "geheim1", "network.sync_token": "2213-1610",
               "corrections.username": "andy", "corrections.host": "caster.example",
               "liste": [{"api_token": "x"}], "leer": {"password": ""}}
        aus = diagnose.schwaerzen(roh)
        text = json.dumps(aus)
        for geheim in ("geheim1", "2213-1610", "andy"):
            self.assertNotIn(geheim, text)
        self.assertEqual(aus["corrections.host"], "caster.example")
        self.assertEqual(aus["leer"]["password"], "")

    def test_package_contains_parts_and_redacts_json(self):
        with tempfile.TemporaryDirectory() as ordner:
            datei = Path(ordner) / "roh.txt"
            datei.write_text("$GPGGA,...", encoding="utf-8")
            paket = diagnose.packen({"a.json": {"network.sync_token": "2213-1610"}, "b.txt": "hallo", "c": None},
                                    {"rohdaten/roh.txt": datei})
            with zipfile.ZipFile(io.BytesIO(paket)) as z:
                self.assertEqual(sorted(z.namelist()), ["a.json", "b.txt", "rohdaten/roh.txt"])
                self.assertNotIn("2213", z.read("a.json").decode())

    def test_log_tail(self):
        with tempfile.TemporaryDirectory() as ordner:
            p = Path(ordner) / "agripilot.log"
            p.write_text("\n".join(f"Zeile {i}" for i in range(10000)), encoding="utf-8")
            ende = diagnose.protokoll_ende(p, 5)
            self.assertEqual(ende.splitlines(), [f"Zeile {i}" for i in range(9995, 10000)])
            self.assertEqual(diagnose.protokoll_ende(Path(ordner) / "fehlt.log"), "")


class GenauigkeitMessen(unittest.TestCase):
    def test_only_steered_driving_on_a_line_counts(self):
        g = Genauigkeit()
        for i in range(100):
            g.messen(0.02 if i % 2 else -0.02, True, True, 0.1)
        for _ in range(100):
            g.messen(0.5, False, True, 0.1)       # von Hand gefahren: zählt zur Fahrt, nicht zur Abweichung
        for _ in range(50):
            g.messen(3.0, True, False, 0.1)       # Wende / Stand: zählt gar nicht
        e = g.ergebnis(abgaben_jetzt=3)
        self.assertEqual(e["effektiv_cm"], 2.0)
        self.assertEqual(e["max_cm"], 2.0)
        self.assertAlmostEqual(e["lenkanteil"], 0.5, places=2)
        self.assertEqual(e["abgaben"], 3)

    def test_p95_and_nothing_without_steering(self):
        self.assertIsNone(Genauigkeit().ergebnis())
        g = Genauigkeit()
        g.zuruecksetzen(abgaben_start=2)
        for i in range(100):
            g.messen(0.10 if i < 3 else 0.01, True, True, 0.1)
        e = g.ergebnis(abgaben_jetzt=2)
        self.assertLessEqual(e["p95_cm"], 1.5)
        self.assertEqual(e["abgaben"], 0)


class LuftbildHolen(unittest.TestCase):
    def test_cut_out_and_request(self):
        box = luftbild.ausschnitt(FELD)
        self.assertLess(box[0], 48.4)
        self.assertGreater(box[3], 9.6)
        b, h = luftbild.bildgroesse(FELD, box)
        self.assertLessEqual(max(b, h), luftbild.MAX_PIXEL)
        url = luftbild.getmap_url("https://dienst.example/wms?", "EBENE", box, b, h)
        self.assertIn("SRS=EPSG%3A4326", url)
        self.assertIn("LAYERS=EBENE", url)
        self.assertIsNone(luftbild.ausschnitt({**FELD, "boundary": []}))

    def test_fetch_once_then_from_cache_and_master_first(self):
        aufrufe = []

        def lesen(url, kopf, timeout):
            aufrufe.append((url, kopf))
            return JPEG

        with tempfile.TemporaryDirectory() as ordner:
            pfad, info = luftbild.holen(FELD, Path(ordner), master=("http://buero:8080", {"X-AgriPilot-Token": "c"}),
                                        lesen=lesen)
            self.assertTrue(aufrufe[0][0].endswith("/api/sync/luftbild/f1"))
            self.assertEqual(len(aufrufe), 1)
            self.assertEqual(pfad.read_bytes(), JPEG)
            x0, y0, x1, y1 = info["rechteck"]
            self.assertAlmostEqual(x0, -150, delta=0.5)
            self.assertAlmostEqual(y1, 350, delta=0.5)
            luftbild.holen(FELD, Path(ordner), lesen=lesen)
            self.assertEqual(len(aufrufe), 1)          # aus dem Zwischenspeicher

    def test_master_fails_then_service_error_page_is_rejected(self):
        def lesen(url, kopf, timeout):
            if "/api/sync/" in url:
                raise OSError("Master weg")
            return b"<ServiceExceptionReport>"

        with tempfile.TemporaryDirectory() as ordner:
            with self.assertRaises(luftbild.LuftbildFehler) as fehler:
                luftbild.holen(FELD, Path(ordner), master=("http://buero", {}), lesen=lesen)
            self.assertIn("Master", str(fehler.exception))


class LuftbildErsatz(unittest.TestCase):
    def test_coarse_fallback_when_the_service_is_down_and_kept_for_a_while(self):
        aufrufe = []

        def lesen(url, kopf, timeout):
            aufrufe.append(url)
            if "ersatz.example" in url:
                return JPEG
            raise TimeoutError("timed out")

        with tempfile.TemporaryDirectory() as ordner:
            ersatz = ("https://ersatz.example/wms", "rgb", "Ersatz")
            pfad, info = luftbild.holen(FELD, Path(ordner), lesen=lesen, ersatz=ersatz)
            self.assertTrue(info["grob"])
            self.assertEqual(info["quelle"], "Ersatz")
            n = len(aufrufe)
            luftbild.holen(FELD, Path(ordner), lesen=lesen, ersatz=ersatz)
            self.assertEqual(len(aufrufe), n)             # Ersatz frisch: Dienst nicht erneut abwarten

    def test_after_an_outage_the_next_field_does_not_wait_for_the_service(self):
        aufrufe = []

        def lesen(url, kopf, timeout):
            aufrufe.append(url)
            if "ersatz.example" in url:
                return JPEG
            raise TimeoutError("timed out")

        ersatz = ("https://ersatz.example/wms", "rgb", "Ersatz")
        luftbild._gestoert_bis.clear()
        with tempfile.TemporaryDirectory() as ordner:
            luftbild.holen(FELD, Path(ordner), wms="https://dienst.example/wms", lesen=lesen, ersatz=ersatz)
            zweites = {**FELD, "id": "f2"}
            aufrufe.clear()
            luftbild.holen(zweites, Path(ordner), wms="https://dienst.example/wms", lesen=lesen, ersatz=ersatz)
            self.assertEqual([u for u in aufrufe if "dienst.example" in u], [])
        luftbild._gestoert_bis.clear()

    def test_timeout_reads_like_german(self):
        def lesen(url, kopf, timeout):
            raise TimeoutError("timed out")

        with tempfile.TemporaryDirectory() as ordner:
            with self.assertRaises(luftbild.LuftbildFehler) as f:
                luftbild.holen(FELD, Path(ordner), lesen=lesen, ersatz=None)
            self.assertIn("Zeitüberschreitung", str(f.exception))


class ApiTests(unittest.TestCase):
    def test_diagnose_download_and_job_accuracy(self):
        with tempfile.TemporaryDirectory() as ordner:
            with geraet(ordner, "client", "tablet-a") as tablet:
                app = tablet.app.state.app
                app.config.network.sync_token = "2213-1610"
                r = tablet.post("/api/diagnose", json={"notiz": "Pendelt am Hang", "lenklive": [{"t": 1, "quer": 2}]})
                self.assertEqual(r.status_code, 200, r.text)
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    namen = z.namelist()
                    self.assertIn("zustand.json", namen)
                    self.assertIn("lenkung_live.json", namen)
                    self.assertIn("Pendelt am Hang", z.read("LIESMICH.txt").decode())
                    for name in namen:
                        self.assertNotIn(b"2213-1610", z.read(name))
                e = app.engine
                feld = app.store.save_field(dict(name="T", datum_lat=48.4, datum_lon=9.6,
                                                 boundary=[[0, 0], [100, 0], [100, 100], [0, 100]]))
                e.load_field(feld["id"])
                e.on_fix(Fix(lat=48.4002, lon=9.6002, fix_quality=4, speed_ms=2.0, course_deg=0.0,
                             received_at=time.time()))
                e.start_job("Grubbern")
                for i in range(20):
                    e.genauigkeit.messen(0.03, True, True, 0.1)
                e.distance_m = 50.0
                job = e.stop_job()
                gespeichert = app.store.get_job(job["id"])
                self.assertEqual(gespeichert["genauigkeit"]["effektiv_cm"], 3.0)
                csv = tablet.get("/api/jobs.csv").text
                self.assertIn("lenkung_effektiv_cm", csv.splitlines()[0])
                self.assertIn("3.0", csv.splitlines()[1])


if __name__ == "__main__":
    unittest.main()
