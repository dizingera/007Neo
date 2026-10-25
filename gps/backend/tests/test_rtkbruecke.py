"""RTK-Ausfall überbrücken."""

import unittest

from agripilot.rtkbruecke import RtkBruecke


def fahrt(bruecke, schritte, rank_bei, ersatz_versatz=(0.0, 0.0), sprung_bei=None, tempo=2.0, max_s=60):
    """Geradeaus nach Norden, 10 Bilder je Sekunde. Liefert (wahr, gerechnet, aktiv) je Schritt."""
    aus = []
    for i in range(schritte):
        t = i / 10
        wahr = (0.0, tempo * t)
        rank = rank_bei(t)
        gemessen = wahr if rank >= 4 else (wahr[0] + ersatz_versatz[0], wahr[1] + ersatz_versatz[1])
        if sprung_bei is not None and t >= sprung_bei and rank < 4:
            gemessen = (gemessen[0] + 0.8, gemessen[1])
        gerechnet = bruecke.update(gemessen, rank, tempo, 0.0, t, max_s)
        aus.append((wahr, gerechnet, bruecke.aktiv))
    return aus


class RtkBrueckeTest(unittest.TestCase):
    def test_with_rtk_nothing_changes(self):
        b = RtkBruecke()
        for wahr, gerechnet, aktiv in fahrt(b, 50, lambda t: 4):
            self.assertEqual(wahr, gerechnet)
            self.assertFalse(aktiv)

    def test_the_offset_of_the_fallback_solution_is_removed(self):
        b = RtkBruecke()
        verlauf = fahrt(b, 300, lambda t: 4 if t < 5 else 5 if t < 5 else 2, ersatz_versatz=(0.6, -0.4))
        nach = [(w, g) for (w, g, a), i in zip(verlauf, range(300)) if i / 10 >= 6]
        for wahr, gerechnet in nach:
            self.assertLess(abs(gerechnet[0] - wahr[0]), 0.02)
            self.assertLess(abs(gerechnet[1] - wahr[1]), 0.05)
        self.assertTrue(verlauf[-1][2])

    def test_it_ends_after_the_limit(self):
        b = RtkBruecke()
        verlauf = fahrt(b, 200, lambda t: 4 if t < 2 else 2, max_s=10)
        self.assertTrue(verlauf[50][2])          # 5 s ohne RTK: überbrückt
        self.assertFalse(verlauf[150][2])        # 13 s: vorbei
        self.assertIn("10 s", b.grund)

    def test_a_jump_of_the_fallback_ends_it(self):
        b = RtkBruecke()
        verlauf = fahrt(b, 100, lambda t: 4 if t < 2 else 2, sprung_bei=5.0)
        self.assertTrue(verlauf[40][2])
        self.assertFalse(verlauf[60][2])
        self.assertIn("gesprungen", b.grund)

    def test_no_new_bridge_until_rtk_returns(self):
        b = RtkBruecke()
        fahrt(b, 200, lambda t: 4 if t < 2 else 2, max_s=5)
        self.assertFalse(b.aktiv)
        b.update((0.0, 100.0), 2, 2.0, 0.0, 30.0, 5)
        self.assertFalse(b.aktiv)
        b.update((0.0, 100.0), 4, 2.0, 0.0, 30.1, 5)
        b.update((0.3, 100.2), 2, 2.0, 0.0, 30.2, 5)
        self.assertTrue(b.aktiv)

    def test_switched_off_with_zero(self):
        b = RtkBruecke()
        verlauf = fahrt(b, 50, lambda t: 4 if t < 2 else 2, max_s=0)
        self.assertFalse(any(a for _, _, a in verlauf))

    def test_status_counts_down(self):
        b = RtkBruecke()
        fahrt(b, 50, lambda t: 4 if t < 2 else 2, max_s=60)
        s = b.status(4.9, 60)
        self.assertTrue(s["aktiv"])
        self.assertAlmostEqual(s["rest_s"], 57.0, delta=0.3)


if __name__ == "__main__":
    unittest.main()
