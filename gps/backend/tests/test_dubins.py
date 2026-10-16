"""Dubins-Wenden: Endpose, Radius und tatsächliche Wendeintegration."""
import math
import unittest
from agripilot import dubins
from agripilot import headland
from agripilot.geo import distance, heading_deg, angle_difference

class DubinsTests(unittest.TestCase):
    def test_gerade_verbindung_hat_bekannte_kuerzeste_laenge(self):
        pfade = dubins.kandidaten((0, 0, 90), (20, 0, 90), 4)
        self.assertAlmostEqual(pfade[0].laenge_m, 20)
        self.assertLess(distance(pfade[0].punkte[-1], (20, 0)), 1e-7)

    def test_wende_trifft_zielpose_und_unterschreitet_radius_nicht(self):
        for kurs in (0, 37, 90, 215, 359):
            for abstand in (3, 8, 21):
                start = (3, 4, kurs)
                v = math.radians(kurs)
                ziel = (3 - math.cos(v)*abstand, 4 + math.sin(v)*abstand, (kurs+180)%360)
                pfade = dubins.kandidaten(start, ziel, 4)
                self.assertTrue(pfade)
                self.assertEqual([p.laenge_m for p in pfade], sorted(p.laenge_m for p in pfade))
                for pfad in pfade:
                    self.assertLess(distance(pfad.punkte[0], start[:2]), 1e-8)
                    self.assertLess(distance(pfad.punkte[-1], ziel[:2]), 1e-7)
                    self.assertLess(abs(angle_difference(pfad.kurse[-1], ziel[2])), 1e-7)
                    for a,b,ka,kb in zip(pfad.punkte,pfad.punkte[1:],pfad.kurse,pfad.kurse[1:]):
                        ds=distance(a,b); winkel=abs(math.radians(angle_difference(kb,ka)))
                        if winkel>1e-8:
                            self.assertGreaterEqual(ds/(2*math.sin(winkel/2)), 4-1e-6)

    def test_wendeplanung_kennt_dubins_und_wendet_auf_die_gewaehlte_seite(self):
        for richtung, x in (("links",-6),("rechts",6)):
            s=headland.HeadlandSettings(muster="dubins",richtung=richtung,radius_m=4,ueberspringen=2)
            self.assertEqual(s.normalisiert().muster,"dubins")
            pfad=headland.plan((0,0),0,s,3,15)
            self.assertLess(distance(pfad[-1],(x,0)),1e-7)

    def test_ungueltige_geometrie_wird_abgewiesen(self):
        for radius in (0,-1,float("nan"),float("inf")):
            with self.assertRaises(ValueError):dubins.kandidaten((0,0,0),(1,1,180),radius)

if __name__ == "__main__":unittest.main()
