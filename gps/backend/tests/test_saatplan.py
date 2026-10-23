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
        self.assertEqual(max(s["versatz_m"] for s in p["spuren"]), 145.5)
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
        self.assertAlmostEqual(f["breite_m"], 4.0, delta=0.01)      # A-Säbreite bis Fahrgasse B
        self.assertAlmostEqual(f["ueberlappung_m"], 2.0, delta=0.01)
        # Nach der letzten Fahrgasse von A noch eine Säbreite (139,5 m), dann ist A fertig.
        self.assertEqual([s["versatz_m"] for s in p["spuren"] if not s.get("von_b")][-2:], [136.5, 139.5])
        self.assertTrue(any("11,0 m Teilbreite" in a for a in f["anpassung"]))
        self.assertTrue(p["reihenfolge"][0].startswith("Von der Startecke (A): so viele Fahrgassen wie passen"))
        self.assertTrue(p["reihenfolge"][1].startswith("An der Gegenseite (B"))
        self.assertTrue(p["reihenfolge"][2].startswith("Von B weiter Richtung A bis zur letzten Säspur von A: 2"))
        self.assertTrue(any("die Fahrgassen bleiben frei" in a for a in f["anpassung"]))
        self.assertEqual([k for k in p["konflikte"] if k["art"] == "abstand"], [])
        # Die Saisonspur kennt beide: 126 m (A) und 136 m (B) neben der Spur.
        self.assertEqual(p["saisonspur_fahrgassen"][:2], [-136.0, -126.0])

    def test_the_extra_pass_after_a_never_falls_into_the_tramline_of_b(self):
        # 148 m: eine Fahrgasse bei 136,5 m läge nur 1 m vor der von B (137,5 m) -
        # die Säbreite danach fiele in die Fahrgasse. Also endet A bei 115,5 m.
        p = self.plan(148.0)
        f = p["fuellstreifen"]
        self.assertAlmostEqual(f["abstand_m"], 22.0, delta=0.01)
        self.assertEqual([s["versatz_m"] for s in p["spuren"] if s["fahrgasse"]][-1], 115.5)
        self.assertTrue(any("je 0,5 m weiter" in a for a in f["anpassung"]))
        self.assertEqual(f["anzahl_von_b"], 6)
        self.assertAlmostEqual(f["ueberlappung_m"], 2.0, delta=0.01)
        # Die Spur für B liegt auf der Fahrgasse von B und kennt dieselben Fahrgassen.
        self.assertEqual(p["saisonspur_b"], [[0.0, 137.5], [300.0, 137.5]])
        self.assertEqual(p["saisonspur_b_fahrgassen"][-2:], [106.0, 127.0])
        self.assertIn(0.0, p["saisonspur_b_fahrgassen"])

    def test_a_whole_number_of_tramline_widths_needs_nothing(self):
        p = self.plan(147.0)
        self.assertEqual((p["fuellstreifen"]["abstand_m"], p["fuellstreifen"]["ueberlappung_m"]), (21.0, 0.0))
        self.assertEqual(p["fuellstreifen"]["anzahl_von_b"], 5)
        self.assertEqual([s["versatz_m"] for s in p["spuren"] if s["fahrgasse"]],
                         [31.5, 52.5, 73.5, 94.5, 115.5])

    def test_without_rest_there_is_no_filler_strip(self):
        p = self.plan(168.0)
        self.assertEqual((p["fuellstreifen"]["abstand_m"], p["fuellstreifen"]["ueberlappung_m"]), (21.0, 0.0))
        self.assertFalse(any("Teilbreite" in a for a in p["fuellstreifen"]["anpassung"]))
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

    def test_a_step_in_the_field_is_not_the_opposite_side(self):
        # Westen nur 120 m tief, Osten 168 m: die Stufe bei 120 m ist keine Gegenseite,
        # B liegt am echten Rand bei 168 m - und das Feld bis dorthin bekommt Säspuren.
        grenze = [(0.0, 0.0), (300.0, 0.0), (300.0, 168.0), (150.0, 168.0), (150.0, 120.0), (0.0, 120.0)]
        o = sued(grenze)
        p = saatplan.planen(grenze, tuple(o["von"]), tuple(o["nach"]), 3.0, 21.0, 3)
        self.assertIsNotNone(p["fuellstreifen"])
        self.assertAlmostEqual(p["fuellstreifen"]["bis_m"], 168.0 - 10.5 - 1.5, delta=0.01)
        self.assertEqual(p["saisonspur_b"][0][1], 157.5)
        tiefste = max(s["versatz_m"] for s in p["spuren"])
        self.assertGreaterEqual(tiefste, 145.5)                   # bis ans Vorgewende am echten Rand
        # Im Osten hinter der Stufe liegen Säspuren, im Westen nicht (dort ist Vorgewende).
        hinter_stufe = [s for s in p["spuren"] if s["versatz_m"] > 120.0]
        self.assertTrue(hinter_stufe)
        for s in hinter_stufe:
            for a, b in s["stuecke"]:
                self.assertGreaterEqual(min(a[0], b[0]), 150.0 + 21.0 - 0.05)

    def test_a_field_without_a_straight_far_side_is_sown_from_a_to_the_end(self):
        # Hinten schräg, davor eine gerade Stufe: keine Gegenseite, A läuft bis zum Ende durch.
        grenze = [(0.0, 0.0), (300.0, 0.0), (300.0, 200.0), (150.0, 120.0), (0.0, 120.0)]
        o = sued(grenze)
        p = saatplan.planen(grenze, tuple(o["von"]), tuple(o["nach"]), 3.0, 21.0, 3)
        self.assertIsNone(p["fuellstreifen"])
        self.assertGreaterEqual(max(s["versatz_m"] for s in p["spuren"]), 160.0)

    def test_start_options_offer_both_corners_of_each_edge(self):
        optionen = saatplan.startkanten(rechteck(168.0))
        self.assertEqual(len(optionen), 8)
        self.assertTrue(any(o["text"] == "Ecke Südwest → entlang Süd 300 m nach Osten · längste Kante" for o in optionen))
        # Die längste gerade Kante zuerst: dort fängt man beim Säen an, sie ist vorgewählt.
        self.assertEqual([o["laenge_m"] for o in optionen], sorted((o["laenge_m"] for o in optionen), reverse=True))
        self.assertTrue(optionen[0]["laengste"] and optionen[0]["laenge_m"] == 300.0)
        self.assertFalse(any(o.get("laengste") for o in optionen if o["laenge_m"] < 300.0))


class FastGeradeKanten(unittest.TestCase):
    """Höllochweg: die längste Kante (406 m) war ein Bogen mit 2,9 m Stich - nicht wählbar."""

    def grenze(self, stich):
        # Südkante 400 m mit einem Knick in der Mitte, die Nordkante 380 m.
        return [(0.0, 0.0), (200.0, -stich), (400.0, 0.0), (390.0, 120.0), (10.0, 120.0)]

    def test_a_nearly_straight_bend_is_a_start_edge_and_the_longest_comes_first(self):
        optionen = saatplan.startkanten(self.grenze(2.9))
        self.assertTrue(optionen[0]['laengste'])
        self.assertIn('(fast gerade)', optionen[0]['text'])
        self.assertTrue(optionen[0]['id'].startswith('k'))
        self.assertAlmostEqual(optionen[0]['laenge_m'], 400.0)

    def test_a_real_bend_stays_out(self):
        self.assertFalse(any('Süd' in o['kante'] and '400' in o['kante'] for o in saatplan.startkanten(self.grenze(12.0))))

    def test_ids_of_straight_edges_do_not_move(self):
        # Mit und ohne den fast geraden Bogen: die geraden Kanten behalten ihre Kennung.
        mit = {o['id']: o['kante'] for o in saatplan.startkanten(self.grenze(2.9)) if not o['id'].startswith('k')}
        ohne = {o['id']: o['kante'] for o in saatplan.startkanten(self.grenze(12.0))}
        self.assertEqual(mit, {k: v for k, v in ohne.items() if k in mit})


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
        self.assertEqual(stand["vorgabe"]["start"], stand["optionen"][0]["id"])   # die längste Kante
        self.assertEqual(stand["optionen"][0]["laenge_m"], 300.0)
        start = next(o["id"] for o in stand["optionen"] if o["text"].startswith("Ecke Südwest")
                     and "Süd" in o["kante"])
        plan = self.motor.saatplan_planen(self.feld["id"], {"start": start, "breite_m": 3,
                                                            "fahrgasse_m": 21, "randspuren": 3})
        self.assertEqual(plan["anzahl_fahrgassen"], 6)
        self.assertEqual(self.motor.saatplan(self.feld["id"])["plan"]["anzahl_spuren"], plan["anzahl_spuren"])

        alle = self.motor.saatplan_uebernehmen(self.feld["id"])
        self.assertEqual(alle["name"], "Saatplan Süd 300 m · alle Spuren")
        self.assertEqual(self.motor.line.id, alle["id"])
        self.assertEqual(self.motor.line.mode, "plan")
        # A bleibt daneben bestehen - für Spritze und Streuer im Raster der Fahrgassen.
        spur = next(l for l in self.store.list_lines(self.feld["id"]) if l["name"].endswith("· A"))
        self.assertEqual((spur["saison"], spur["fahrgasse_m"]), (time.localtime().tm_year, 21.0))
        self.motor.load_line(spur["id"])
        # Die Führung trifft dieselben Fahrgassen: 10,5 m und 31,5 m von der Südkante.
        # Nach Osten gefahren liegt das Feld links: Spurnummern negativ.
        self.motor.line.spacing = 3.0
        self.assertTrue(self.motor.line.ist_fahrgasse(0))
        self.assertTrue(self.motor.line.ist_fahrgasse(-7))
        self.assertFalse(self.motor.line.ist_fahrgasse(-3))
        # Nochmal übernehmen ersetzt, statt eine zweite Saatplan-Spur anzulegen.
        self.motor.saatplan_uebernehmen(self.feld["id"])
        namen = [l["name"] for l in self.store.list_lines(self.feld["id"])]
        self.assertEqual(sorted(n for n in namen if n.startswith("Saatplan ")),
                         ["Saatplan Süd 300 m · A", "Saatplan Süd 300 m · B",
                          "Saatplan Süd 300 m · alle Spuren"])
        # Die Spur mit allen Spuren ist die Saisonspur, die beim Laden des Feldes kommt.
        self.assertEqual(self.store.season_line(self.feld["id"], time.localtime().tm_year)["name"],
                         "Saatplan Süd 300 m · alle Spuren")

    def test_the_season_line_knows_the_tramlines_from_a_and_from_b(self):
        feld = self.store.save_field({"name": "Schmal", "datum_lat": 48.0, "datum_lon": 9.6,
                                      "boundary": rechteck(157.0)})
        start = next(o["id"] for o in self.motor.saatplan(feld["id"])["optionen"]
                     if o["text"].startswith("Ecke Südwest") and "Süd" in o["kante"])
        self.motor.saatplan_planen(feld["id"], {"start": start, "breite_m": 3, "fahrgasse_m": 21,
                                                "randspuren": 3})
        self.motor.saatplan_uebernehmen(feld["id"])
        spur = next(l for l in self.store.list_lines(feld["id"]) if l["name"].endswith("· A"))
        self.assertEqual(spur["fahrgassen"][:2], [-136.0, -126.0])
        self.motor.load_line(spur["id"])
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
