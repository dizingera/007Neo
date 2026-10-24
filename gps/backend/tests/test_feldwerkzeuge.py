"""Verhalten der neuen Feldwerkzeuge, einschließlich beschädigter Entwürfe."""
import math
import unittest
from agripilot import feldwerkzeuge as f
from agripilot.geo import LocalPlane

class FeldwerkzeugeTest(unittest.TestCase):
    def test_arbeitsgrenze_erhaelt_original_und_prueft_kanten(self):
        original=[[0,0],[100,0],[100,100],[0,100]]
        self.assertEqual(f.grenze_pruefen([[2,2],[98,2],[98,98],[2,98]],original)[0],[2.,2.])
        # Ungültig bleibt: Selbstschnitt, unter 100 m², kaputte Koordinaten.
        for punkte in ([[0,0],[100,100],[0,100],[100,0]], [[0,0],[1,0],[0,1]], [[0,0],[math.nan,1],[30,30]]):
            with self.assertRaises(ValueError): f.grenze_pruefen(punkte,original)
        # Kein Limit gegenüber der Feldgrenze mehr (24.10.2026): außerhalb und über eine Bucht ist erlaubt.
        self.assertEqual(len(f.grenze_pruefen([[-1,0],[10,0],[10,20]],original)),3)
        bucht=[[0,0],[100,0],[100,100],[60,100],[60,40],[40,40],[40,100],[0,100]]
        self.assertEqual(len(f.grenze_pruefen([[10,10],[90,10],[90,90],[10,90]],bucht)),4)

    def test_spuren_trennen_luecken_wenden_und_unbearbeitet(self):
        plane=LocalPlane(50,10)
        def punkt(i,x,y,working=1):
            lat,lon=plane.to_wgs(x,y)
            return dict(t=i,lat=lat,lon=lon,tool_lat=lat,tool_lon=lon,working=working,turning=0,fix_quality=4,speed_ms=2)
        punkte=[punkt(i,10,i*2) for i in range(30)]
        punkte += [punkt(80+i,30,100-i*2) for i in range(30)]
        punkte += [punkt(120+i,50,i*2,0) for i in range(30)]
        segmente=f.arbeitslinien(punkte,plane)
        self.assertEqual(len(segmente),2)
        self.assertFalse(segmente[0]['altaufzeichnung'])
        self.assertGreater(segmente[0]['laenge_m'],50)
        alt=[{k:v for k,v in p.items() if k not in ('tool_lat','tool_lon','working','turning')} for p in punkte[:30]]
        self.assertTrue(f.arbeitslinien(alt,plane)[0]['altaufzeichnung'])

    def test_polygon_enthaelt_auch_kanten_und_abstand(self):
        ring=[[0,0],[100,0],[100,100],[0,100]]
        self.assertTrue(f.flaeche_enthalten([[1,1],[9,1],[9,9],[1,9]],ring,0.5))
        self.assertFalse(f.flaeche_enthalten([[1,1],[9,1],[9,9],[1,9]],ring,2))

if __name__=='__main__': unittest.main()


class WendenAusArbeitenTest(unittest.TestCase):
    def test_weite_manuelle_u_wende_entfernt(self):
        ebene=LocalPlane(50,10)
        pts=[(0,i*2) for i in range(31)]
        pts += [(25-25*math.cos(a),60+25*math.sin(a)) for a in [i*math.pi/40 for i in range(1,41)]]
        pts += [(50,60-i*2) for i in range(1,31)]
        rows=[]
        for i,(x,y) in enumerate(pts):
            lat,lon=ebene.to_wgs(x,y)
            rows.append(dict(t=i,lat=lat,lon=lon,tool_lat=lat,tool_lon=lon,working=1,turning=0,fix_quality=4,speed_ms=2))
        aus=f.arbeitslinien(rows,ebene)
        self.assertEqual(len(aus),2)
        self.assertTrue(all(s['laenge_m']<65 for s in aus))
        self.assertTrue(all(y<62 for s in aus for x,y in s['punkte']))

    def test_sanfte_kurve_und_kuerzen(self):
        pts=[(200*(1-math.cos(i*.003)),200*math.sin(i*.003)) for i in range(100)]
        self.assertEqual(len(f._ohne_wendeboegen(pts)),1)
        self.assertEqual(f.abschnitt_kuerzen([[0,0],[0,100]],10,20),[[0.,10.],[0.,80.]])
        with self.assertRaises(ValueError):f.abschnitt_kuerzen([[0,0],[0,100]],50,40)
