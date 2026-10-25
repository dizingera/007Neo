"""Saatplan mit allen Spuren: Hauptfeld A, Gegenseite B und Vorgewende - alle sichtbar
und alle mit der Lenkung fahrbar, ohne umzuschalten."""

import os
import tempfile
import unittest

from agripilot import config as config_module
from agripilot import saatplan
from agripilot.engine import Engine
from agripilot.guidance import GuidanceLine
from agripilot.storage import Storage
from test_tablet_lenkung import geraet


def rechteck(breite, laenge=300.0):
    return [(0.0, 0.0), (laenge, 0.0), (laenge, breite), (0.0, breite)]


def planen(motor, feld_id):
    start = next(o["id"] for o in motor.saatplan(feld_id)["optionen"]
                 if o["text"].startswith("Ecke Südwest") and "Süd" in o["kante"])
    return motor.saatplan_planen(feld_id, {"start": start, "breite_m": 3, "fahrgasse_m": 21,
                                           "randspuren": 3})


class AlleSpuren(unittest.TestCase):
    """151 m tief: Fahrgassen von A; von B drei Säspuren, die Fahrgasse und vier Spuren bis A."""

    def setUp(self):
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        self.store = Storage(os.path.join(ordner.name, "s.db"))
        self.addCleanup(self.store.close)
        self.motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), self.store)
        self.feld = self.store.save_field({"name": "Acker", "datum_lat": 48.0, "datum_lon": 9.6,
                                           "boundary": rechteck(151.0)})
        self.plan = planen(self.motor, self.feld["id"])
        self.spur = self.motor.saatplan_uebernehmen(self.feld["id"])
        self.linie = self.motor.line
        self.linie.spacing = 3.0

    def lenken(self, x, y, kurs):
        zustand = self.linie.solve((x, y), kurs, 2.0, self.motor.profile)
        return zustand, self.linie.spuren[zustand.pass_number]

    def test_every_pass_of_the_plan_is_in_the_line(self):
        spuren = saatplan.alle_spuren(self.plan)
        self.assertEqual({s["art"] for s in spuren}, {"A", "B", "V"})
        self.assertEqual(sum(1 for s in spuren if s["art"] == "V"), self.plan["vorgewende_runden"])
        self.assertTrue(all(s["geschlossen"] for s in spuren if s["art"] == "V"))
        # Die Vorgewende-Fahrgasse ist die vierte Runde (drei Säspuren, dann die Gasse).
        self.assertEqual([s["nummer"] for s in spuren if s["art"] == "V" and s["fahrgasse"]], [4])
        self.assertEqual(self.linie.mode, "plan")
        self.assertEqual(len(self.linie.spuren), len(spuren))
        self.assertEqual(self.spur["name"], "Saatplan Süd 300 m · alle Spuren")

    def test_the_live_state_stays_small(self):
        line = self.motor.state()["line"]
        self.assertEqual(line["mode"], "plan")
        self.assertEqual(line["spuren_anzahl"], len(self.linie.spuren))
        self.assertTrue(line["spuren_schluessel"])
        self.assertNotIn("spuren", line)

    def test_a_pass_from_b_is_steered_on_itself(self):
        b = next(s for s in self.plan["spuren"] if s.get("von_b"))
        zustand, spur = self.lenken(150.0, b["versatz_m"] + 0.2, 90.0)
        self.assertEqual((spur["art"], spur["nummer"]), ("B", b["nummer"]))
        self.assertAlmostEqual(abs(zustand.cross_track_m), 0.2, places=6)
        # Auch in der Gegenrichtung.
        zustand, spur = self.lenken(150.0, b["versatz_m"] - 0.3, 270.0)
        self.assertEqual(spur["art"], "B")
        self.assertAlmostEqual(abs(zustand.cross_track_m), 0.3, places=6)

    def test_the_headland_rounds_are_steered_too(self):
        # Westseite, erste Runde 1,5 m von der Grenze, nach Norden gefahren.
        zustand, spur = self.lenken(1.7, 80.0, 0.0)
        self.assertEqual((spur["art"], spur["nummer"]), ("V", 1))
        self.assertAlmostEqual(abs(zustand.cross_track_m), 0.2, places=6)
        # Ostseite, die Vorgewende-Fahrgasse (Runde 4, 10,5 m vom Rand), nach Süden.
        zustand, spur = self.lenken(300.0 - 10.5, 80.0, 180.0)
        self.assertEqual((spur["art"], spur["nummer"], spur["fahrgasse"]), ("V", 4, True))
        self.assertTrue(self.linie.ist_fahrgasse(zustand.pass_number))

    def test_a_crossing_round_does_not_steal_the_pass_being_finished(self):
        # Am Ende einer A-Spur, schon im Vorgewende: die quer liegende Runde zählt nicht.
        a = next(s for s in self.plan["spuren"] if not s.get("von_b") and s["versatz_m"] > 40)
        zustand, spur = self.lenken(285.0, a["versatz_m"], 90.0)
        self.assertEqual((spur["art"], spur["nummer"]), ("A", a["nummer"]))

    def test_the_current_pass_holds_until_well_beside_it(self):
        a = next(s for s in self.plan["spuren"] if not s.get("von_b") and s["versatz_m"] > 40)
        _, spur = self.lenken(150.0, a["versatz_m"], 90.0)
        self.assertEqual(spur["nummer"], a["nummer"])
        # 1,6 m daneben ist die Nachbarspur näher - die aktuelle bleibt trotzdem.
        zustand, spur = self.lenken(150.0, a["versatz_m"] + 1.6, 90.0)
        self.assertEqual(spur["nummer"], a["nummer"])
        self.assertAlmostEqual(abs(zustand.cross_track_m), 1.6, places=6)
        # Erst deutlich daneben (2,4 m) gilt die Nachbarspur.
        _, spur = self.lenken(150.0, a["versatz_m"] + 2.4, 90.0)
        self.assertEqual(spur["nummer"], a["nummer"] + 1)

    def test_a_roller_narrower_than_the_tramline_width_takes_any_pass(self):
        self.linie.spacing = 7.0
        a = next(s for s in self.plan["spuren"] if not s.get("von_b") and not s["fahrgasse"]
                 and s["versatz_m"] > 40)
        zustand, spur = self.lenken(150.0, a["versatz_m"] + 0.1, 90.0)
        self.assertEqual((spur["art"], spur["nummer"], spur["fahrgasse"]), ("A", a["nummer"], False))

    def test_a_sprayer_follows_only_the_tramlines(self):
        self.linie.spacing = 21.0
        a = next(s for s in self.plan["spuren"] if not s.get("von_b") and not s["fahrgasse"]
                 and s["versatz_m"] > 40)
        zustand, spur = self.lenken(150.0, a["versatz_m"], 90.0)
        self.assertTrue(spur["fahrgasse"])
        self.assertLessEqual(abs(zustand.cross_track_m), 10.5 + 1e-6)

    def test_a_field_load_brings_the_full_plan_back(self):
        self.motor.clear_line()
        self.motor.load_field(self.feld["id"])
        self.assertEqual(self.motor.line.mode, "plan")
        self.assertEqual(self.motor.line.name, "Saatplan Süd 300 m · alle Spuren")


class SpeichernUndAbgleich(unittest.TestCase):
    def test_the_passes_travel_with_the_line(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            quelle, ziel = Storage(os.path.join(a, "s.db")), Storage(os.path.join(b, "s.db"))
            try:
                spuren = [{"art": "A", "nummer": 1, "punkte": [[0, 1.5], [300, 1.5]],
                           "geschlossen": False, "fahrgasse": False}]
                linie = quelle.save_line({"field_id": "f", "name": "Saatplan X · alle Spuren", "mode": "plan",
                                          "points": [[0, 1.5], [300, 1.5]], "spacing_m": 3.0, "spuren": spuren})
                self.assertEqual(quelle.get_line(linie["id"])["spuren"], spuren)
                # Versatz speichern ohne Spuren mitzugeben: sie bleiben.
                quelle.save_line({**{k: v for k, v in linie.items() if k != "spuren"}, "nudge_m": 0.1})
                self.assertEqual(quelle.get_line(linie["id"])["spuren"], spuren)
                ziel.apply_remote("lines", quelle.changes_since("lines", 0))
                self.assertEqual(ziel.get_line(linie["id"])["spuren"], spuren)
            finally:
                quelle.close()
                ziel.close()

    def test_a_plan_without_passes_falls_back_to_its_reference_line(self):
        linie = GuidanceLine("plan", [(0.0, 0.0), (0.0, 100.0)], 3.0, spuren=[])
        self.assertEqual(linie.mode, "ab")
        linie = GuidanceLine("plan", [(0.0, 0.0), (0.0, 100.0)], 3.0, spuren=[{"punkte": [[1, 1]]}])
        self.assertEqual(linie.mode, "ab")

    def test_the_display_fetches_the_passes_once(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            motor = c.app.state.app.engine
            feld = c.app.state.app.store.save_field({"name": "Acker", "datum_lat": 48.0, "datum_lon": 9.6,
                                                     "boundary": rechteck(157.0)})
            self.assertEqual(c.get("/api/line/spuren").json()["spuren"], [])
            planen(motor, feld["id"])
            motor.saatplan_uebernehmen(feld["id"])
            antwort = c.get("/api/line/spuren").json()
            self.assertEqual(len(antwort["spuren"]), len(motor.line.spuren))
            self.assertEqual(antwort["schluessel"], c.get("/api/state").json()["line"]["spuren_schluessel"])


if __name__ == "__main__":
    unittest.main()
