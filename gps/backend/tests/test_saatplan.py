"""Saatplan: Säspuren und Fahrgassen für den ganzen Schlag, mit Randvorgabe."""

import os
import tempfile
import time
import unittest

from agripilot import config as config_module
from agripilot import saatplan
from agripilot.engine import Engine
from agripilot.storage import Storage


def rechteck(breite, laenge=300.0):
    return [(0.0, 0.0), (laenge, 0.0), (laenge, breite), (0.0, breite)]


def sued(grenze):
    """Start in der Südwestecke, entlang der Südkante nach Osten."""
    return next(o for o in saatplan.startkanten(grenze) if o["von"] == [0.0, 0.0] and o["nach"][1] == 0.0)


class PlanTests(unittest.TestCase):
    def plan(self, breite, saebreite=3.0, weite=21.0, rand=3):
        grenze = rechteck(breite)
        o = sued(grenze)
        return saatplan.planen(grenze, tuple(o["von"]), tuple(o["nach"]), saebreite, weite, rand)

    def test_three_passes_then_a_tramline_at_every_edge(self):
        p = self.plan(168.0)                       # 8 Fahrgassenweiten
        self.assertEqual(p["konflikte"], [])
        self.assertTrue(p["randvorgabe_passt"])
        # Vorgewende: 7 Runden, die vierte (10,5 m) ist die Fahrgasse.
        self.assertEqual([r["einzug_m"] for r in p["ringe"] if r["fahrgasse"]], [10.5])
        self.assertEqual(len(p["ringe"]), 7)
        # Hauptfeld: Fahrgassen alle 21 m, an 10,5 m ausgerichtet.
        gassen = [s["versatz_m"] for s in p["spuren"] if s["fahrgasse"]]
        self.assertEqual(gassen, [31.5, 52.5, 73.5, 94.5, 115.5, 136.5])
        self.assertEqual(set(p["fahrgassen_abstaende"]), {21.0})
        # Vom Gegenrand aus: 168 - 10,5 = 157,5 ist die Vorgewende-Fahrgasse, 21 m nach 136,5.
        self.assertEqual(p["spuren"][0]["versatz_m"], 22.5)
        self.assertEqual(p["spuren"][-1]["versatz_m"], 145.5)
        # Die Saisonspur liegt auf der ersten Fahrgasse (10,5 m von der Startkante).
        self.assertEqual(p["saisonspur"], [[0.0, 10.5], [300.0, 10.5]])

    def test_every_planned_pass_lies_inside_the_main_field(self):
        p = self.plan(168.0)
        for spur in p["spuren"]:
            for a, b in spur["stuecke"]:
                self.assertAlmostEqual(a[0], 21.0, delta=0.05)
                self.assertAlmostEqual(b[0], 279.0, delta=0.05)

    def test_a_width_that_is_no_multiple_of_the_tramline_spacing_is_a_conflict(self):
        p = self.plan(157.0)
        konflikt = next(k for k in p["konflikte"] if k["art"] == "abstand")
        self.assertIn("Rand Nord", konflikt["text"])
        self.assertAlmostEqual(konflikt["abweichung_m"], 10.0, delta=0.01)   # 31 m statt 21 m
        self.assertTrue(any("Zusätzliche Fahrgasse" in a for a in konflikt["anpassung"]))
        self.assertTrue(any("147 m oder 168 m" in a for a in konflikt["anpassung"]))
        self.assertTrue(konflikt["punkte"])

    def test_too_little_room_is_reported_as_overlap(self):
        p = self.plan(163.0)                       # letzte Fahrgasse 136,5, Vorgewende 152,5: 16 m
        konflikt = next(k for k in p["konflikte"] if k["art"] == "abstand")
        self.assertAlmostEqual(konflikt["abweichung_m"], -5.0, delta=0.01)
        self.assertIn("doppelt", konflikt["text"])
        self.assertTrue(any("Teilbreite" in a for a in konflikt["anpassung"]))

    def test_a_drill_width_that_does_not_fit_the_rule_is_reported(self):
        p = self.plan(168.0, saebreite=4.0)
        konflikt = next(k for k in p["konflikte"] if k["art"] == "randvorgabe")
        self.assertIn("28 m", konflikt["text"])
        self.assertFalse(p["randvorgabe_passt"])

    def test_a_narrow_field_has_no_main_field(self):
        p = self.plan(35.0)
        self.assertEqual(p["spuren"], [])
        self.assertTrue(any(k["art"] == "zu_schmal" for k in p["konflikte"]))

    def test_a_slanted_edge_is_a_wedge(self):
        grenze = [(0.0, 0.0), (300.0, 0.0), (300.0, 150.0), (0.0, 168.0)]
        o = sued(grenze)
        p = saatplan.planen(grenze, tuple(o["von"]), tuple(o["nach"]), 3.0, 21.0, 3)
        self.assertTrue(any(k["art"] == "keil" for k in p["konflikte"]))

    def test_start_options_offer_both_corners_of_each_edge(self):
        optionen = saatplan.startkanten(rechteck(168.0))
        self.assertEqual(len(optionen), 8)
        self.assertTrue(any(o["text"] == "Ecke Südwest → entlang Süd 300 m nach Osten" for o in optionen))


class EngineTests(unittest.TestCase):
    def setUp(self):
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        self.store = Storage(os.path.join(ordner.name, "s.db"))
        self.addCleanup(self.store.close)
        self.motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), self.store)
        self.feld = self.store.save_field({"name": "Acker", "datum_lat": 48.0, "datum_lon": 9.6,
                                           "boundary": rechteck(168.0)})

    def test_plan_is_stored_and_taken_over_as_season_line(self):
        stand = self.motor.saatplan(self.feld["id"])
        self.assertIsNone(stand["plan"])
        self.assertEqual(stand["vorgabe"]["fahrgasse_m"], 21.0)
        start = next(o["id"] for o in stand["optionen"] if o["text"].startswith("Ecke Südwest")
                     and "Süd" in o["kante"])
        plan = self.motor.saatplan_planen(self.feld["id"], {"start": start, "breite_m": 3,
                                                            "fahrgasse_m": 21, "randspuren": 3})
        self.assertEqual(plan["anzahl_fahrgassen"], 6)
        self.assertEqual(self.motor.saatplan(self.feld["id"])["plan"]["anzahl_spuren"], plan["anzahl_spuren"])

        spur = self.motor.saatplan_uebernehmen(self.feld["id"])
        self.assertTrue(spur["name"].startswith("Saatplan "))
        self.assertEqual((spur["saison"], spur["fahrgasse_m"]), (time.localtime().tm_year, 21.0))
        self.assertEqual(self.motor.line.id, spur["id"])
        # Die Führung trifft dieselben Fahrgassen: 10,5 m und 31,5 m von der Südkante.
        self.motor.line.spacing = 3.0
        self.assertTrue(self.motor.line.ist_fahrgasse(0))
        self.assertTrue(self.motor.line.ist_fahrgasse(7))
        self.assertFalse(self.motor.line.ist_fahrgasse(3))
        # Nochmal übernehmen ersetzt, statt eine zweite Saatplan-Spur anzulegen.
        self.motor.saatplan_uebernehmen(self.feld["id"])
        namen = [l["name"] for l in self.store.list_lines(self.feld["id"])]
        self.assertEqual(len([n for n in namen if n.startswith("Saatplan ")]), 1)

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            self.motor.saatplan_planen(self.feld["id"], {"start": "gibt-es-nicht"})
        with self.assertRaises(ValueError):
            self.motor.saatplan_uebernehmen(self.feld["id"])
        leer = self.store.save_field({"name": "Ohne", "datum_lat": 48.0, "datum_lon": 9.6, "boundary": []})
        with self.assertRaises(ValueError):
            self.motor.saatplan(leer["id"])


if __name__ == "__main__":
    unittest.main()
