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

    def test_as_many_tramlines_from_a_as_fit_then_three_passes_and_a_tramline_from_b(self):
        # 157 m: von A Fahrgassen bis 136,5 m, von B nach 3 Säspuren bei 146,5 m - 10 m auseinander.
        p = self.plan(157.0)
        gassen = [s["versatz_m"] for s in p["spuren"] if s["fahrgasse"]]
        self.assertEqual(gassen, [31.5, 52.5, 73.5, 94.5, 115.5, 136.5])
        f = p["fuellstreifen"]
        self.assertAlmostEqual(f["abstand_m"], 10.0, delta=0.01)
        self.assertAlmostEqual(f["breite_m"], 7.0, delta=0.01)      # zwischen den Fahrgassen-Spuren
        self.assertTrue(any("11,0 m Teilbreite" in a for a in f["anpassung"]))
        self.assertTrue(p["reihenfolge"][0].startswith("Von der Startecke (A): so viele Fahrgassen wie passen"))
        self.assertTrue(p["reihenfolge"][1].startswith("An der Gegenseite (B"))
        self.assertTrue(p["reihenfolge"][2].startswith("Rest zwischen B und A auffüllen (7,0 m"))
        self.assertEqual([k for k in p["konflikte"] if k["art"] == "abstand"], [])
        # Die Saisonspur kennt beide: 126 m (A) und 136 m (B) neben der Spur.
        self.assertEqual(p["saisonspur_fahrgassen"][:2], [-136.0, -126.0])

    def test_the_two_last_tramlines_may_be_only_a_metre_apart(self):
        p = self.plan(148.0)
        f = p["fuellstreifen"]
        self.assertAlmostEqual(f["abstand_m"], 1.0, delta=0.01)
        self.assertTrue(any("20,0 m Teilbreite" in a for a in f["anpassung"]))
        self.assertTrue(any("überlappen 2,0 m" in a for a in f["anpassung"]))
        self.assertEqual(p["saisonspur_fahrgassen"][:2], [-127.0, -126.0])

    def test_a_whole_number_of_tramline_widths_needs_nothing(self):
        p = self.plan(147.0)
        self.assertIsNone(p["fuellstreifen"])
        self.assertEqual([s["versatz_m"] for s in p["spuren"] if s["fahrgasse"]],
                         [31.5, 52.5, 73.5, 94.5, 115.5])

    def test_without_rest_there_is_no_filler_strip(self):
        p = self.plan(168.0)
        self.assertIsNone(p["fuellstreifen"])
        self.assertEqual(p["saisonspur_fahrgassen"], [-147.0, -126.0, -105.0, -84.0, -63.0, -42.0, -21.0, 0.0])

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
        # Nach Osten gefahren liegt das Feld links: Spurnummern negativ.
        self.motor.line.spacing = 3.0
        self.assertTrue(self.motor.line.ist_fahrgasse(0))
        self.assertTrue(self.motor.line.ist_fahrgasse(-7))
        self.assertFalse(self.motor.line.ist_fahrgasse(-3))
        # Nochmal übernehmen ersetzt, statt eine zweite Saatplan-Spur anzulegen.
        self.motor.saatplan_uebernehmen(self.feld["id"])
        namen = [l["name"] for l in self.store.list_lines(self.feld["id"])]
        self.assertEqual(len([n for n in namen if n.startswith("Saatplan ")]), 1)

    def test_the_season_line_knows_the_tramlines_from_a_and_from_b(self):
        feld = self.store.save_field({"name": "Schmal", "datum_lat": 48.0, "datum_lon": 9.6,
                                      "boundary": rechteck(157.0)})
        start = next(o["id"] for o in self.motor.saatplan(feld["id"])["optionen"]
                     if o["text"].startswith("Ecke Südwest") and "Süd" in o["kante"])
        self.motor.saatplan_planen(feld["id"], {"start": start, "breite_m": 3, "fahrgasse_m": 21,
                                                "randspuren": 3})
        spur = self.motor.saatplan_uebernehmen(feld["id"])
        self.assertEqual(self.store.get_line(spur["id"])["fahrgassen"][:2], [-136.0, -126.0])
        linie = self.motor.line
        linie.spacing = 3.0
        self.assertTrue(linie.ist_fahrgasse(-35))       # 105 m neben der Spur: Fahrgasse von A
        self.assertTrue(linie.ist_fahrgasse(-42))       # 126 m: die letzte Fahrgasse von A
        self.assertFalse(linie.ist_fahrgasse(-43))      # dazwischen wird aufgefüllt
        self.assertFalse(linie.ist_fahrgasse(-44))
        self.assertTrue(linie.ist_fahrgasse(-45))       # 135 m: Fahrgasse von B (146,5 m ab A)
        # Mit der Spritze (21 m) liegt jede Spur auf einer Fahrgasse.
        linie.spacing = 21.0
        self.assertTrue(all(linie.ist_fahrgasse(-k) for k in range(0, 7)))
        self.assertEqual(self.motor.state()["line"]["fahrgassen"][-1], 0.0)

    def test_planning_from_where_the_tractor_stands(self):
        self.motor.load_field(self.feld["id"])
        with self.assertRaises(RuntimeError):
            self.motor.saatplan_hier()                     # noch keine Position
        # Südostecke, nach Norden ausgerichtet: entlang der Ostkante.
        self.motor.position, self.motor.heading = (296.0, 4.0), 2.0
        plan = self.motor.saatplan_hier({"breite_m": 3})
        self.assertEqual(plan["start"], [300.0, 0.0])
        self.assertEqual(plan["kurs"], 0.0)
        self.assertIn("nach Norden", plan["startwahl"])
        # Dieselbe Ecke, nach Westen ausgerichtet: entlang der Südkante.
        self.motor.heading = 268.0
        self.assertIn("nach Westen", self.motor.saatplan_hier()["startwahl"])
        self.assertEqual(self.motor.saatplan(self.feld["id"])["vorgabe"]["breite_m"], 3.0)
        # Mitten im Feld gibt es keine Startecke.
        self.motor.position = (150.0, 80.0)
        with self.assertRaises(ValueError):
            self.motor.saatplan_hier()

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
