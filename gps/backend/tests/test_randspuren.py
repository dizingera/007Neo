"""Spuren aus der Feldgrenze: gerade Kanten ab 20 m als AB, Bögen als Kurve."""

import math
import os
import tempfile
import unittest

from agripilot import config as config_module
from agripilot import randspuren
from agripilot.engine import Engine
from agripilot.storage import Storage

RECHTECK = [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)]


def mit_bogen():
    """Rechteck, dessen Nordrand ein Halbkreis ist (Radius 50 m)."""
    bogen = [(50.0 + 50.0 * math.cos(a), 60.0 + 50.0 * math.sin(a))
             for a in [i * math.pi / 30 for i in range(1, 30)]]
    return [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0)] + bogen + [(0.0, 60.0)]


class KantenTests(unittest.TestCase):
    def test_a_rectangle_gives_four_ab_lines_on_its_edges(self):
        kanten = randspuren.kanten(RECHTECK)
        self.assertEqual([k["mode"] for k in kanten], ["ab"] * 4)
        self.assertEqual(sorted(round(k["laenge_m"]) for k in kanten), [60, 60, 100, 100])
        namen = sorted(randspuren.benennen(k, RECHTECK) for k in kanten)
        self.assertEqual(namen, ["Rand Nord 100 m", "Rand Ost 60 m", "Rand Süd 100 m", "Rand West 60 m"])

    def test_edges_shorter_than_20_m_are_left_out(self):
        klein = [(0.0, 0.0), (100.0, 0.0), (100.0, 15.0), (0.0, 15.0)]
        self.assertEqual(sorted(round(k["laenge_m"]) for k in randspuren.kanten(klein)), [100, 100])

    def test_a_round_edge_becomes_one_curve(self):
        kanten = randspuren.kanten(mit_bogen())
        kurven = [k for k in kanten if k["mode"] == "curve"]
        self.assertEqual(len(kurven), 1)
        self.assertAlmostEqual(kurven[0]["laenge_m"], math.pi * 50.0, delta=6.0)
        # Das erste, fast tangentiale Bogenstück zählt noch zur Geraden (Toleranz).
        gerade = sorted(k["laenge_m"] for k in kanten if k["mode"] == "ab")
        self.assertEqual(len(gerade), 3)
        for ist, soll in zip(gerade, [60, 60, 100]):
            self.assertAlmostEqual(ist, soll, delta=6.0)

    def test_a_gently_bent_edge_is_a_curve_not_many_short_ab_lines(self):
        # Fünf 34-m-Stücke, je 6 Grad geknickt - so lagen manche MyTerra-Ränder vor.
        punkte, x, y, kurs = [(0.0, 0.0)], 0.0, 0.0, 90.0
        for _ in range(5):
            x += 34.0 * math.sin(math.radians(kurs)); y += 34.0 * math.cos(math.radians(kurs))
            punkte.append((x, y)); kurs -= 6.0
        ring = punkte + [(x, y - 120.0), (0.0, -120.0)]
        kurven = [k for k in randspuren.kanten(ring) if k["mode"] == "curve"]
        self.assertEqual(len(kurven), 1)
        self.assertAlmostEqual(kurven[0]["laenge_m"], 170.0, delta=1.0)

    @staticmethod
    def _rand(stuecke, schluss):
        """Ring aus (Länge, Knick in Grad) und einer Rückkehr über `schluss`."""
        punkte, x, y, kurs = [(0.0, 0.0)], 0.0, 0.0, 90.0
        for laenge, knick in stuecke:
            kurs -= knick
            x += laenge * math.sin(math.radians(kurs)); y += laenge * math.cos(math.radians(kurs))
            punkte.append((x, y))
        return punkte + schluss(x, y)

    def test_a_straight_edge_running_into_a_gentle_bend_is_one_curve(self):
        # Wie Brünnele Süd: 244 m gerade, dann 15 m mit 6 Grad, 59 m mit 9 Grad, dann die Ecke.
        ring = self._rand([(244.0, 0.0), (15.0, 6.0), (59.0, 9.0)],
                          lambda x, y: [(x, y - 150.0), (0.0, -150.0)])
        kanten = randspuren.kanten(ring)
        kurven = [k for k in kanten if k["mode"] == "curve"]
        self.assertEqual(len(kurven), 1)
        self.assertAlmostEqual(kurven[0]["laenge_m"], 318.0, delta=1.0)
        self.assertEqual([round(p, 1) for p in kurven[0]["points"][0]], [0.0, 0.0])

    def test_a_rounded_corner_still_separates_two_edges(self):
        # 100 m, abgerundete 90-Grad-Ecke aus sechs 4-m-Sehnen, 80 m: zwei
        # Kanten, keine L-förmige Kurvenspur um die Ecke.
        ring = self._rand([(100.0, 0.0)] + [(4.0, 15.0)] * 6 + [(80.0, 0.0)],
                          lambda x, y: [(-30.0, y), (-30.0, 0.0)])
        kanten = randspuren.kanten(ring)
        self.assertEqual([k["mode"] for k in kanten], ["ab"] * 4)
        self.assertEqual(sorted(round(k["laenge_m"]) for k in kanten), [84, 97, 130, 143])


class EngineTests(unittest.TestCase):
    def setUp(self):
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        self.store = Storage(os.path.join(ordner.name, "r.db"))
        self.addCleanup(self.store.close)
        self.motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), self.store)
        self.feld = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 9.6,
                                           "boundary": mit_bogen()})

    def test_lines_are_created_and_a_second_run_updates_instead_of_duplicating(self):
        erst = self.motor.randspuren_anlegen(self.feld["id"])
        self.assertEqual(len(erst["angelegt"]), 4)
        linien = self.store.list_lines(self.feld["id"])
        self.assertEqual(sorted(l["mode"] for l in linien), ["ab", "ab", "ab", "curve"])
        # Versatz an einer Randspur bleibt beim zweiten Durchlauf erhalten
        sued = next(l for l in linien if l["name"].startswith("Rand Süd"))
        self.store.save_line({**sued, "nudge_m": 0.3})
        self.motor.randspuren_anlegen(self.feld["id"])
        danach = self.store.list_lines(self.feld["id"])
        self.assertEqual(len(danach), 4)
        self.assertEqual(next(l for l in danach if l["id"] == sued["id"])["nudge_m"], 0.3)

    def test_a_season_edge_keeps_geometry_and_a_changed_edge_gets_a_new_id(self):
        alt = self.store.save_line({"field_id": self.feld["id"], "name": "Rand Süd 90 m", "mode": "ab",
                                    "points": [[5, 0], [95, 0]], "spacing_m": 6.0, "nudge_m": 1.0,
                                    "saison": 2026, "fahrgasse_m": 21.0})
        self.motor.randspuren_anlegen(self.feld["id"])
        linien = self.store.list_lines(self.feld["id"])
        nachfolger = next(l for l in linien if l["id"] == alt["id"])
        self.assertEqual(nachfolger, alt)
        self.assertTrue(any(l["name"] == "Rand Süd 100 m" and l["id"] != alt["id"] for l in linien))
        self.assertEqual((nachfolger["nudge_m"], nachfolger["saison"], nachfolger["fahrgasse_m"]),
                         (1.0, 2026, 21.0))
        self.assertEqual(len([l for l in linien if l["name"].startswith("Rand ")]), 5)

    def test_own_lines_are_never_touched(self):
        eigene = self.store.save_line({"field_id": self.feld["id"], "name": "AB 07:30", "mode": "ab",
                                       "points": [[10, 10], [10, 50]], "spacing_m": 6.0})
        self.motor.randspuren_anlegen(self.feld["id"])
        self.store.save_field({**self.feld, "boundary": RECHTECK})   # Grenze geändert
        self.motor.randspuren_anlegen(self.feld["id"])
        namen = sorted(l["name"] for l in self.store.list_lines(self.feld["id"]))
        self.assertIn("AB 07:30", namen)
        self.assertEqual(len([n for n in namen if n.startswith("Rand ")]), 4)
        self.assertNotIn("Rand Kurve Nord", " ".join(namen))
        self.assertIsNotNone(self.store.get_line(eigene["id"]))

    def test_loading_the_field_prefers_own_lines_then_the_longest_edge(self):
        self.motor.randspuren_anlegen(self.feld["id"])
        self.motor.load_field(self.feld["id"])
        self.assertEqual(self.motor.line.name, "Rand Süd 100 m")
        eigene = self.store.save_line({"field_id": self.feld["id"], "name": "AB 07:30", "mode": "ab",
                                       "points": [[10, 10], [10, 50]], "spacing_m": 6.0})
        self.motor.randspuren_anlegen(self.feld["id"])          # danach geändert: trotzdem eigene zuerst
        self.motor.load_field(self.feld["id"])
        self.assertEqual(self.motor.line.id, eigene["id"])

    def test_a_curve_line_guides(self):
        self.motor.randspuren_anlegen(self.feld["id"])
        kurve = next(l for l in self.store.list_lines(self.feld["id"]) if l["mode"] == "curve")
        self.motor.load_field(self.feld["id"])
        self.motor.load_line(kurve["id"])
        zustand = self.motor.line.solve((50.0, 105.0), 270.0, 2.0, self.motor.profile)
        self.assertTrue(zustand.active)
        self.assertLess(abs(zustand.cross_track_m), 3.5)


class RandRasterTests(unittest.TestCase):
    """Saisonspur am Rand: Geräte mit der Kante an der Grenze, Fahrgassen bei
    halber Fahrgassenweite - Drille und Spritze treffen dieselben Gassen."""

    @staticmethod
    def linie(breite, innen=1.0, fahrgasse=21.0):
        from agripilot.guidance import GuidanceLine
        l = GuidanceLine("ab", [(0, 0), (0, 100)], breite, "Rand West 100 m")
        l.rand_innen, l.fahrgasse_m = innen, fahrgasse
        return l

    def gassen_m(self, breite, innen=1.0):
        """Wo (Meter ins Feld) die Spuren mit Fahrgasse liegen."""
        l = self.linie(breite, innen)
        return sorted(round(innen * (l.raster_versatz() + p * breite), 2)
                for p in range(-40, 41) if l.ist_fahrgasse(p)
                and innen * (l.raster_versatz() + p * breite) > 0)

    def test_drill_and_sprayer_hit_the_same_tramlines(self):
        self.assertEqual(self.gassen_m(3.0)[:3], [10.5, 31.5, 52.5])
        self.assertEqual(self.gassen_m(21.0)[:3], [10.5, 31.5, 52.5])
        # 4 m geht nicht auf: die Spur, in deren Streifen die Gasse fällt.
        for soll, ist in zip([10.5, 31.5, 52.5], self.gassen_m(4.0)):
            self.assertLessEqual(abs(soll - ist), 2.0)

    def test_the_field_on_the_left_mirrors_the_grid(self):
        self.assertEqual(self.gassen_m(3.0, innen=-1.0)[:3], [10.5, 31.5, 52.5])
        self.assertEqual(self.gassen_m(21.0, innen=-1.0)[:2], [10.5, 31.5])

    def test_the_first_pass_has_the_tool_edge_on_the_boundary(self):
        from agripilot.guidance import VehicleProfile
        l = self.linie(3.0)
        zustand = l.solve((1.5, 50.0), 0.0, 2.0, VehicleProfile())
        self.assertEqual(zustand.pass_number, 0)
        self.assertAlmostEqual(zustand.cross_track_m, 0.0)
        self.assertTrue(l.solve((10.5, 50.0), 0.0, 2.0, VehicleProfile()).pass_number == 3)
        self.assertTrue(l.ist_fahrgasse(3))
        geo = {g["pass"]: g["points"][0][0] for g in l.pass_geometry(0, 1)}
        self.assertAlmostEqual(geo[0], 1.5)
        self.assertAlmostEqual(geo[1], 4.5)

    def test_without_tramlines_or_off_the_edge_nothing_moves(self):
        ohne = self.linie(3.0, fahrgasse=0.0)
        self.assertEqual(ohne.raster_versatz(), 0.0)
        eigene = self.linie(3.0, innen=0.0)
        self.assertEqual((eigene.raster_versatz(), eigene.fahrgassen_versatz()), (0.0, 0.0))
        self.assertTrue(eigene.ist_fahrgasse(0))     # eigene Spur: Gasse auf Spur 0 wie bisher


class RandSeiteTests(unittest.TestCase):
    def setUp(self):
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        self.store = Storage(os.path.join(ordner.name, "s.db"))
        self.addCleanup(self.store.close)
        self.motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), self.store)
        self.feld = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 9.6,
                                           "boundary": RECHTECK})
        self.motor.randspuren_anlegen(self.feld["id"])
        self.motor.load_field(self.feld["id"])

    def spur(self, anfang):
        return next(l for l in self.store.list_lines(self.feld["id"]) if l["name"].startswith(anfang))

    def test_every_edge_knows_where_the_field_is_and_the_tramline_lies_inside(self):
        for name, punkt in (("Rand Süd", (50.0, 10.5)), ("Rand Nord", (50.0, 49.5)),
                            ("Rand West", (10.5, 30.0)), ("Rand Ost", (89.5, 30.0))):
            with self.subTest(name):
                spur = self.spur(name)
                self.motor.update_line(spur["id"], {"saisonspur": True})
                self.motor.load_line(spur["id"])
                linie = self.motor.line
                self.assertNotEqual(linie.rand_innen, 0.0)
                linie.spacing = 3.0
                zustand = linie.solve(punkt, 0.0, 2.0, self.motor.profile)
                self.assertAlmostEqual(abs(zustand.cross_track_m), 0.0, places=6)
                self.assertTrue(linie.ist_fahrgasse(zustand.pass_number))
                self.assertNotEqual(self.motor.state()["line"]["raster_m"], 0.0)

    def test_switching_the_season_on_moves_the_grid_off_the_boundary(self):
        spur = self.spur("Rand Süd")
        self.motor.load_line(spur["id"])
        self.assertEqual(self.motor.line.raster_versatz(), 0.0)
        self.motor.update_line(spur["id"], {"saisonspur": True})
        self.assertAlmostEqual(abs(self.motor.line.raster_versatz()), self.motor.line.spacing / 2)


if __name__ == "__main__":
    unittest.main()
