"""Vorgewende-Runden: die Feldgrenze sauber nach innen versetzt - auch an Einbuchtungen,
spitzen Zipfeln und engen Hälsen. Auf diesen Runden wird gelenkt (Saatplan)."""

import unittest

from agripilot import headland


def pruefen(testfall, grenze, abstand, toleranz=0.1):
    """Jeder Punkt jeder Runde (auch zwischen den Ecken) liegt im Feld und mindestens
    ``abstand`` von der Grenze; die Runde kreuzt sich nirgends selbst."""
    ringe = headland.ringe(grenze, abstand)
    for ring in ringe:
        n = len(ring)
        for k in range(n):
            a, b = ring[k], ring[(k + 1) % n]
            for s in (0.0, 0.25, 0.5, 0.75):
                p = (a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s)
                testfall.assertTrue(headland.point_in_polygon(p, grenze), p)
                testfall.assertGreaterEqual(headland.distance_to_boundary(p, grenze), abstand - toleranz, p)
        for i in range(n):
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue
                testfall.assertIsNone(headland._schnitt(ring[i], ring[(i + 1) % n], ring[j], ring[(j + 1) % n]),
                                      f"Runde kreuzt sich selbst: Kante {i} und {j}")
    return ringe


class VorgewendeRinge(unittest.TestCase):
    def test_a_rectangle_gets_sharp_corners_at_exactly_the_distance(self):
        ringe = pruefen(self, [(0, 0), (100, 0), (100, 60), (0, 60)], 10.5)
        self.assertEqual(len(ringe), 1)
        self.assertEqual(sorted((round(x, 6), round(y, 6)) for x, y in ringe[0]),
                         [(10.5, 10.5), (10.5, 49.5), (89.5, 10.5), (89.5, 49.5)])

    def test_a_notch_leaves_no_spikes_across_the_field(self):
        # Rechteck mit einer tiefen Einbuchtung von oben (wie Stelle 2): vorher liefen
        # die Runden an den einspringenden Ecken als Zacken quer durchs Feld.
        grenze = [(0, 0), (300, 0), (300, 200), (170, 200), (170, 80), (130, 80), (130, 200), (0, 200)]
        for runde in range(7):
            ringe = pruefen(self, grenze, (runde + 0.5) * 3.0)
            self.assertEqual(len(ringe), 1, f"Runde {runde + 1}")

    def test_a_sharp_tip_with_a_tiny_edge_keeps_its_rounds(self):
        # Spitzer Zipfel, dessen Spitze eine 1 m kurze Kante ist (Langer Bauer): die
        # klappt beim Versetzen um; die Runde muss trotzdem ganz herum laufen.
        grenze = [(-35.6, 52.3), (-5.3, 23.4), (67.8, -45.7), (66.9, -45.1), (26.2, -21.7),
                  (-47.0, -3.6), (-69.5, 1.9)]
        for runde in range(4):
            ringe = pruefen(self, grenze, (runde + 0.5) * 3.0)
            self.assertEqual(len(ringe), 1, f"Runde {runde + 1}")

    def test_a_narrow_neck_splits_a_round_into_two(self):
        # Zwei Felder, verbunden durch einen 12 m breiten Hals: ab 6 m Abstand zwei Runden.
        grenze = [(0, 0), (100, 0), (100, 44), (140, 44), (140, 0), (240, 0), (240, 100),
                  (140, 100), (140, 56), (100, 56), (100, 100), (0, 100)]
        self.assertEqual(len(pruefen(self, grenze, 4.5)), 1)
        zwei = pruefen(self, grenze, 7.5)
        self.assertEqual(len(zwei), 2)
        self.assertEqual(len(headland.ring(grenze, 7.5)), len(max(zwei, key=lambda r: abs(headland._flaeche(r)))))

    def test_too_narrow_for_a_round_gives_none(self):
        self.assertEqual(headland.ringe([(0, 0), (100, 0), (100, 10), (0, 10)], 6.0), [])
        self.assertEqual(headland.ring([(0, 0), (100, 0), (100, 10), (0, 10)], 6.0), [])

    def test_either_direction_of_the_boundary_gives_the_same_round(self):
        grenze = [(0, 0), (300, 0), (300, 200), (170, 200), (170, 80), (130, 80), (130, 200), (0, 200)]
        hin = headland.ringe(grenze, 7.5)[0]
        her = headland.ringe(list(reversed(grenze)), 7.5)[0]
        self.assertAlmostEqual(abs(headland._flaeche(hin)), abs(headland._flaeche(her)), places=3)


if __name__ == "__main__":
    unittest.main()
