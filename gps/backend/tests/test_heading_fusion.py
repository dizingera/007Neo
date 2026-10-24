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

    def test_a_gyro_counting_the_other_way_is_turned_round_and_used(self):
        # Tinkerforge zählt gegen den Uhrzeigersinn positiv: ab 1.5.80 wird das
        # Vorzeichen gelernt statt die Kopplung abzuschalten.
        filt = HeadingFilter()
        fehler = fahre(filt, 12.0, 400, gyro_vorzeichen=-1.0)
        self.assertTrue(filt.fusion_aktiv)
        self.assertEqual(filt.gier_vorzeichen, -1.0)
        self.assertTrue(filt.gier_bestaetigt)
        self.assertIn("umgedreht", filt.warnung)
        self.assertLess(abs(fehler), 0.5)

    def test_a_gyro_that_contradicts_both_ways_is_switched_off(self):
        filt = HeadingFilter()
        fahre(filt, 12.0, 400, gyro_vorzeichen=-1.0)      # lernt -1
        fehler = fahre(filt, 12.0, 400, gyro_vorzeichen=1.0)  # und dann wieder andersherum
        self.assertFalse(filt.fusion_aktiv)
        self.assertIn("prüfen", filt.warnung)
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


class ReviewFixTests(unittest.TestCase):
    def test_reset_clears_reverse_and_learned_state(self):
        filt = HeadingFilter()
        for _ in range(10):
            filt.update(90.0, None, 2.0, yaw_rate_deg_s=0.0, dt=DT)
        for _ in range(4):
            filt.update(270.0, None, 2.0, yaw_rate_deg_s=0.0, dt=DT)
        self.assertTrue(filt.rueckwaerts)
        filt.reset()
        self.assertFalse(filt.rueckwaerts)
        self.assertIsNone(filt.value)
        self.assertTrue(filt.fusion_aktiv)

    def test_true_heading_clears_a_stale_reverse_flag(self):
        filt = HeadingFilter()
        filt.rueckwaerts = True
        filt.update(None, 45.0, 2.0)
        self.assertFalse(filt.rueckwaerts)

    def test_no_reverse_flip_below_walking_pace(self):
        filt = HeadingFilter()
        filt.update(90.0, None, 2.0)
        for _ in range(6):
            filt.update(270.0, None, 0.6)
        self.assertFalse(filt.rueckwaerts)

    def test_slow_branch_ignores_a_gyro_that_failed_the_sign_check(self):
        filt = HeadingFilter()
        fahre(filt, 12.0, 400, gyro_vorzeichen=-1.0)
        fahre(filt, 12.0, 400, gyro_vorzeichen=1.0)
        self.assertFalse(filt.fusion_aktiv)
        vorher = filt.value
        filt.update(None, None, 0.1, yaw_rate_deg_s=30.0, dt=0.5)
        self.assertEqual(filt.value, vorher)

    def test_bias_is_not_learned_in_a_turn(self):
        filt = HeadingFilter()
        # Kurve mit 20 Grad/s; der Bewegungskurs hinkt 2 Grad hinterher.
        wahr = 90.0
        filt.update(wahr, None, 3.0)
        for _ in range(100):
            wahr = normalize_heading(wahr + 2.0)
            filt.update(normalize_heading(wahr - 2.0), None, 3.0, yaw_rate_deg_s=20.0, dt=DT)
        self.assertEqual(filt.bias_deg_s, 0.0)


class ConfirmForwardTests(unittest.TestCase):
    def test_arming_clears_a_false_reverse_after_turning_around(self):
        filt = HeadingFilter()
        for _ in range(10):
            filt.update(90.0, None, 2.0)
        for _ in range(5):                      # umgedreht und losgefahren: Kurs 270
            filt.update(270.0, None, 2.0)
        self.assertTrue(filt.rueckwaerts)
        filt.bestaetige_vorwaerts()
        filt.update(270.0, None, 2.0)
        self.assertFalse(filt.rueckwaerts)
        self.assertAlmostEqual(angle_difference(filt.value, 270.0), 0.0, delta=1.0)
        for _ in range(5):
            filt.update(270.0, None, 2.0)
        self.assertFalse(filt.rueckwaerts)

    def test_a_stale_reverse_course_does_not_count_as_the_confirmation(self):
        """Der Fahrer drückt, der Traktor rollt schon vorwärts, aber die letzte
        Position stammt noch aus der Rückwärtsfahrt: gilt erst der nächste Kurs."""
        filt = HeadingFilter()
        for _ in range(10):
            filt.update(0.0, None, 2.8)
        for _ in range(6):
            filt.update(180.0, None, 1.2)
        self.assertTrue(filt.rueckwaerts)
        filt.bestaetige_vorwaerts()                 # letzter Kurs war 180
        self.assertTrue(filt.bestaetigung_offen)
        filt.update(0.0, None, 2.8)                 # erste Position vorwärts
        self.assertFalse(filt.rueckwaerts)
        self.assertAlmostEqual(angle_difference(filt.value, 0.0), 0.0, delta=1.0)
        for _ in range(6):
            filt.update(0.0, None, 2.8)
        self.assertFalse(filt.rueckwaerts)
        self.assertAlmostEqual(angle_difference(filt.value, 0.0), 0.0, delta=1.0)

    def test_confirming_while_not_reversing_changes_nothing(self):
        filt = HeadingFilter()
        for _ in range(10):
            filt.update(90.0, None, 2.0)
        vorher = filt.value
        filt.bestaetige_vorwaerts()
        self.assertFalse(filt.bestaetigung_offen)
        filt.update(90.0, None, 2.0)
        self.assertAlmostEqual(filt.value, vorher, places=3)


if __name__ == "__main__":
    unittest.main()


class LangsamRueckwaerts(unittest.TestCase):
    def test_slow_reversing_does_not_spin_the_heading_round(self):
        # Vorher wurde ein um 180° verdrehter Kurs zwischen 0,5 und 1 m/s eingeglättet:
        # nach wenigen Zehntelsekunden zeigte der Traktor in die Gegenrichtung.
        filt = HeadingFilter()
        filt.update(90.0, None, 2.0)
        for _ in range(10):
            filt.update(270.0, None, 0.7)
        self.assertAlmostEqual(filt.value, 90.0)
        self.assertTrue(filt.gegenlaeufig)
        self.assertFalse(filt.rueckwaerts)
        filt.update(91.0, None, 0.7)                 # wieder vorwärts
        self.assertFalse(filt.gegenlaeufig)

    def test_the_driver_confirming_forward_takes_the_new_course(self):
        filt = HeadingFilter()
        filt.update(90.0, None, 2.0)
        filt.update(270.0, None, 0.7)                # von Hand gewendet, langsam los
        self.assertTrue(filt.gegenlaeufig)
        filt.rueckwaerts = True                      # wie nach erkannter Umkehr
        filt.bestaetige_vorwaerts()
        filt.update(270.0, None, 0.7)
        self.assertAlmostEqual(filt.value, 270.0)
        self.assertFalse(filt.gegenlaeufig)
