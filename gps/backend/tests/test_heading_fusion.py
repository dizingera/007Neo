"""Kurs aus Gyro und Bewegung: ruhiger und ohne Nachlauf als der Kurs allein."""

import random
import unittest

from agripilot.guidance import HeadingFilter, angle_difference, normalize_heading

DT = 0.1


def fahre(filt, rate_deg_s, schritte, start=90.0, bias=0.0, rauschen=0.0,
          gyro_vorzeichen=1.0, seed=7):
    """Wahre Kurve mit konstanter Drehrate; liefert den Fehler am Ende."""
    zufall = random.Random(seed)
    wahr = start
    filt.update(wahr, None, 3.0)
    for _ in range(schritte):
        wahr = normalize_heading(wahr + rate_deg_s * DT)
        kurs = normalize_heading(wahr + zufall.uniform(-rauschen, rauschen))
        gyro = gyro_vorzeichen * rate_deg_s + bias
        filt.update(kurs, None, 3.0, yaw_rate_deg_s=gyro, dt=DT)
    return angle_difference(filt.value, wahr)


class FusionTests(unittest.TestCase):
    def test_follows_a_turn_without_the_lag_of_plain_smoothing(self):
        # Ohne Gyro-Wert bleibt der Nachlauf der Glättung ...
        schlicht = HeadingFilter()
        wahr = 90.0
        schlicht.update(wahr, None, 3.0)
        for _ in range(100):
            wahr = normalize_heading(wahr + 1.0)
            schlicht.update(wahr, None, 3.0)
        self.assertGreater(abs(angle_difference(schlicht.value, wahr)), 1.0)
        # ... mit Gyro folgt der Kurs der Kurve.
        self.assertLess(abs(fahre(HeadingFilter(), 10.0, 100)), 0.5)

    def test_noise_is_rejected_better_with_gyro(self):
        mit = [abs(fahre(HeadingFilter(), 0.0, 300, rauschen=4.0, seed=s)) for s in range(5)]
        schlicht = HeadingFilter()
        zufall = random.Random(3)
        schlicht.update(90.0, None, 3.0)
        fehler = []
        for _ in range(300):
            schlicht.update(90.0 + zufall.uniform(-4.0, 4.0), None, 3.0)
            fehler.append(abs(angle_difference(schlicht.value, 90.0)))
        self.assertLess(sum(mit) / len(mit), sum(fehler) / len(fehler))

    def test_gyro_bias_is_learned_on_the_straight(self):
        fehler = fahre(HeadingFilter(), 0.0, 600, bias=1.0)
        self.assertLess(abs(fehler), 0.3)

    def test_wrong_gyro_sign_switches_fusion_off_and_warns(self):
        filt = HeadingFilter()
        fehler = fahre(filt, 12.0, 400, gyro_vorzeichen=-1.0)
        self.assertFalse(filt.fusion_aktiv)
        self.assertIn("Vorzeichen", filt.warnung)
        # Danach gilt wieder der Kurs allein: nur noch der Nachlauf der Glättung.
        self.assertLess(abs(fehler), 4.0)

    def test_correct_sign_keeps_fusion_on(self):
        filt = HeadingFilter()
        fahre(filt, 12.0, 400)
        self.assertTrue(filt.fusion_aktiv)
        self.assertEqual(filt.warnung, "")

    def test_without_yaw_rate_nothing_changes(self):
        a, b = HeadingFilter(), HeadingFilter()
        for kurs in (90.0, 92.0, 95.0, 99.0):
            a.update(kurs, None, 3.0)
            b.update(kurs, None, 3.0, yaw_rate_deg_s=None, dt=DT)
        self.assertAlmostEqual(a.value, b.value)

    def test_slow_driving_does_not_fuse(self):
        filt = HeadingFilter()
        filt.update(90.0, None, 0.8)
        filt.update(100.0, None, 0.8, yaw_rate_deg_s=0.0, dt=DT)
        # 0,8 m/s liegt unter der Fusionsgrenze: die alte Glättung gilt.
        self.assertAlmostEqual(filt.value, 90.0 + 10.0 * filt.smoothing)


class ReverseTests(unittest.TestCase):
    """Rückwärts liefert der Empfänger den Kurs der Bewegung - 180 Grad vom Traktor."""

    def _vorwaerts(self):
        filt = HeadingFilter()
        for _ in range(10):
            filt.update(90.0, None, 2.0, yaw_rate_deg_s=0.0, dt=DT)
        return filt

    def test_reverse_is_detected_and_body_heading_is_kept(self):
        filt = self._vorwaerts()
        for _ in range(4):
            filt.update(270.0, None, 2.0, yaw_rate_deg_s=0.0, dt=DT)
        self.assertTrue(filt.rueckwaerts)
        self.assertAlmostEqual(angle_difference(filt.value, 90.0), 0.0, delta=1.0)

    def test_reverse_works_without_a_gyro_too(self):
        filt = HeadingFilter()
        for _ in range(10):
            filt.update(90.0, None, 2.0)
        for _ in range(4):
            filt.update(270.0, None, 2.0)
        self.assertTrue(filt.rueckwaerts)
        self.assertAlmostEqual(angle_difference(filt.value, 90.0), 0.0, delta=1.0)

    def test_a_single_glitch_is_not_reverse(self):
        filt = self._vorwaerts()
        filt.update(270.0, None, 2.0, yaw_rate_deg_s=0.0, dt=DT)
        filt.update(90.0, None, 2.0, yaw_rate_deg_s=0.0, dt=DT)
        self.assertFalse(filt.rueckwaerts)

    def test_forward_again_clears_it(self):
        filt = self._vorwaerts()
        for _ in range(4):
            filt.update(270.0, None, 2.0, yaw_rate_deg_s=0.0, dt=DT)
        for _ in range(4):
            filt.update(90.0, None, 2.0, yaw_rate_deg_s=0.0, dt=DT)
        self.assertFalse(filt.rueckwaerts)
        self.assertAlmostEqual(angle_difference(filt.value, 90.0), 0.0, delta=1.0)

    def test_a_normal_turn_is_not_reverse(self):
        filt = self._vorwaerts()
        kurs = 90.0
        for _ in range(60):
            kurs += 2.0                     # 20 Grad/s: eine zügige Wende
            filt.update(kurs, None, 2.0, yaw_rate_deg_s=20.0, dt=DT)
        self.assertFalse(filt.rueckwaerts)


if __name__ == "__main__":
    unittest.main()
