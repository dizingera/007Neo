"""Lenkanschlag links und rechts getrennt."""

import unittest

from agripilot.guidance import TRAKTOR_FELDER, GuidanceLine, VehicleProfile


class LenkanschlagTest(unittest.TestCase):
    def test_zero_means_the_common_limit(self):
        p = VehicleProfile(max_steer_deg=35.0)
        self.assertEqual(p.einschlag_grenzen(), (35.0, 35.0))
        self.assertEqual(p.einschlag_begrenzen(50.0), 35.0)
        self.assertEqual(p.einschlag_begrenzen(-50.0), -35.0)

    def test_each_side_has_its_own_stop(self):
        p = VehicleProfile(max_steer_deg=35.0, max_steer_left_deg=30.0, max_steer_right_deg=38.0)
        self.assertEqual(p.einschlag_begrenzen(-50.0), -30.0)      # links (negativ)
        self.assertEqual(p.einschlag_begrenzen(50.0), 38.0)        # rechts
        self.assertEqual(p.einschlag_begrenzen(10.0), 10.0)

    def test_the_guidance_respects_the_side(self):
        p = VehicleProfile(max_steer_deg=35.0, max_steer_left_deg=20.0, steer_gain=5.0)
        linie = GuidanceLine("ab", [(0, 0), (0, 100)], 3.0)
        # Weit rechts der Spur: es muss stark nach links gelenkt werden - nur bis 20 Grad.
        self.assertEqual(linie._steer_angle(5.0, 0.0, 2.0, p), -20.0)
        self.assertEqual(linie._steer_angle(-5.0, 0.0, 2.0, p), 35.0)

    def test_both_limits_belong_to_the_tractor(self):
        self.assertIn("max_steer_left_deg", TRAKTOR_FELDER)
        self.assertIn("max_steer_right_deg", TRAKTOR_FELDER)


if __name__ == "__main__":
    unittest.main()
