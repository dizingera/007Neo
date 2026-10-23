import unittest
from agripilot import gespann, headland
from agripilot.guidance import VehicleProfile

class GespannTest(unittest.TestCase):
    def profil(self,**kw):
        return VehicleProfile(**dict(dict(width_m=3,traktor_breite_m=2,traktor_vorn_m=4,traktor_hinten_m=1,geraet_breite_m=3,geraet_vorn_m=1,geraet_hinten_m=1),**kw))
    def test_fehlende_masse_keine_freigabe(self):
        r=gespann.pruefen([(0,0),(0,10)],[[-20,-20],[20,-20],[20,20],[-20,20]],VehicleProfile())
        self.assertFalse(r['vollstaendig']);self.assertFalse(r['im_feld'])
    def test_aussenbreite_und_grenzabstand(self):
        ring=[[-2,-20],[2,-20],[2,40],[-2,40]]
        self.assertTrue(gespann.pruefen([(0,0),(0,10)],ring,self.profil(),.1)['im_feld'])
        self.assertFalse(gespann.pruefen([(0,0),(0,10)],ring,self.profil(geraet_breite_m=4.2),0)['im_feld'])
        self.assertFalse(gespann.pruefen([(0,0),(0,10)],ring,self.profil(),.6)['im_feld'])
    def test_deichsel_darf_nicht_durch_bucht(self):
        ring=[[-10,0],[10,0],[10,14],[-2,14],[-2,16],[10,16],[10,40],[-10,40]]
        r=gespann.pruefen([(0,20),(0,21)],ring,self.profil(trailed=True,hitch_length_m=10),0,0,0)
        self.assertFalse(r['im_feld'])
    def test_endtangente_mit_langem_anbau(self):
        pfad=headland.plan_u_turn((0,0),0,12,6,True)
        ring=[[-13.4,-100],[100,-100],[100,100],[-13.4,100]]
        r=gespann.pruefen(pfad,ring,self.profil(tool_trailing_m=8,traktor_vorn_m=1),0,0,0,180)
        self.assertFalse(r['im_feld'])
        self.assertLessEqual(min(p[0] for h in r['huellen'] for p in h),-13.5)
    def test_gezogene_huelle_weicht_von_starrem_anbau_ab(self):
        pfad=headland.plan_u_turn((0,0),0,12,6,True);ring=[[-100,-100],[100,-100],[100,100],[-100,100]]
        a=gespann.pruefen(pfad,ring,self.profil(tool_trailing_m=8),.5,0,0,180)
        b=gespann.pruefen(pfad,ring,self.profil(trailed=True,hitch_length_m=8),.5,0,0,180)
        self.assertTrue(a['im_feld']);self.assertTrue(b['im_feld']);self.assertNotEqual(a['huellen'],b['huellen'])
