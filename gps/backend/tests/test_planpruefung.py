"""Gegenfälle und echte reguläre Saatpläne für die lesende Planprüfung."""
import copy
import json
import math
import unittest
from agripilot import planpruefung, saatplan


class PlanpruefungTests(unittest.TestCase):
    def setUp(self):
        self.feld = {'boundary': [[0, 0], [100, 0], [100, 100], [0, 100]]}

    def plan(self, haupt=None, ringe=None):
        return {'spuren': [{'stuecke': [haupt]}] if haupt else [],
                'ringe': [{'punkte': p} for p in (ringe or [])]}

    def arten(self, p, breite=3):
        return [b['art'] for b in planpruefung.pruefen(p, self.feld, breite)['befunde']]

    def test_regulaerer_echter_plan_ohne_falsche_nachbarwarnung(self):
        ring = [(0,0), (300,0), (300,168), (0,168)]
        p = saatplan.planen(ring, (0,0), (300,0), 3,21,3)
        ergebnis = planpruefung.pruefen(p, {'boundary':ring}, 3)
        self.assertTrue(ergebnis['ok'], ergebnis)
        self.assertEqual(ergebnis['befunde'], [])

    def test_kreuzung_mit_marker_und_keine_mutation(self):
        p = self.plan([[0,50],[100,50]], [[[10,10],[90,10],[90,90],[10,90],[10,10]]])
        vorher = copy.deepcopy((p, self.feld))
        result = planpruefung.pruefen(p,self.feld,3)
        kreuz = [b for b in result['befunde'] if b['art']=='kreuzung']
        self.assertEqual(len(kreuz),2)
        self.assertEqual({tuple(b['punkte'][0]) for b in kreuz}, {(10.,50.),(90.,50.)})
        self.assertTrue(all(b['severity']=='fehler' for b in kreuz))
        self.assertEqual((p,self.feld),vorher)

    def test_kreuzung_an_ringecke_aber_keine_tangente(self):
        ring = [[10,10],[90,10],[90,90],[10,90],[10,10]]
        p = self.plan([[5,5],[95,95]], [ring])
        r = planpruefung.pruefen(p,self.feld,3)
        self.assertEqual(len([b for b in r['befunde'] if b['art']=='kreuzung']),2)
        p = self.plan([[5,15],[15,5]], [ring])
        self.assertNotIn('kreuzung',self.arten(p))

    def test_extreme_koordinaten_werden_abgewiesen(self):
        r = planpruefung.pruefen(self.plan([[1e308,0],[-1e308,0]]),self.feld,3)
        self.assertFalse(r['ok'])
        json.dumps(r,allow_nan=False)

    def test_beruehrung_am_spurende_ist_keine_kreuzung(self):
        p = self.plan([[10,50],[90,50]], [[[10,10],[90,10],[90,90],[10,90]]])
        self.assertNotIn('kreuzung', self.arten(p))

    def test_normale_parallele_runden_sind_erlaubt(self):
        a = [[10,10],[90,10],[90,90],[10,90]]
        b = [[13,13],[87,13],[87,87],[13,87]]
        self.assertNotIn('vorgewende_abstand', self.arten(self.plan(ringe=[a,b])))

    def test_deutlich_zu_nahe_runden_auch_gegenlaeufig(self):
        a = [[10,10],[90,10],[90,90],[10,90]]
        b = [[10.4,10.4],[89.6,10.4],[89.6,89.6],[10.4,89.6]]
        self.assertIn('vorgewende_abstand', self.arten(self.plan(ringe=[a,list(reversed(b))])))

    def test_verlaengerte_geraden_ohne_ueberlappung_sind_kein_konflikt(self):
        a = [[10,10],[30,10],[30,30],[10,30]]
        b = [[60,10.4],[80,10.4],[80,30.4],[60,30.4]]
        self.assertNotIn('vorgewende_abstand', self.arten(self.plan(ringe=[a,b])))

    def test_geschlossene_grenze_duplikate_und_randpunkte(self):
        self.feld['boundary'] += [[0,100],[0,0]]
        r = planpruefung.pruefen(self.plan([[0,0],[100,0]]),self.feld,3)
        self.assertTrue(r['ok'],r)

    def test_ausserhalb_und_konkave_mitte(self):
        self.assertIn('ausserhalb',self.arten(self.plan([[-1,40],[60,40]])))
        feld = {'boundary': [[0,0],[100,0],[100,100],[60,100],[60,30],[40,30],[40,100],[0,100]]}
        result = planpruefung.pruefen(self.plan([[20,80],[80,80]]),feld,3)
        self.assertIn('ausserhalb',[b['art'] for b in result['befunde']])

    def test_kurzes_stueck_nur_hinweis_mit_radius(self):
        p = self.plan([[20,20],[28,20]])
        self.assertNotIn('kurzes_stueck',self.arten(p))
        p['radius_m']=6
        self.assertIn('kurzes_stueck',self.arten(p))
        self.assertIn('nicht auf Fahrbarkeit',planpruefung.pruefen(p,self.feld,3)['hinweis'])

    def test_fehlende_masse_und_defekte_daten_kein_gruen(self):
        for breite in (0,None,float('nan'),float('inf')):
            r=planpruefung.pruefen(self.plan([[20,20],[80,20]]),self.feld,breite)
            self.assertFalse(r['ok'])
            self.assertIn('Arbeitsbreite',r['hinweis'])
        for feld in ({},{'boundary': [[0,0],[1,1],[2,2]]}):
            self.assertFalse(planpruefung.pruefen(self.plan([[20,20],[80,20]]),feld,3)['ok'])
        p=self.plan([[20,20],[math.nan,20]])
        self.assertIn('daten',self.arten(p))
        json.dumps(planpruefung.pruefen(p,self.feld,3),allow_nan=False)

    def test_keine_erfundene_verbindung_zwischen_hauptfeldstuecken(self):
        p={'spuren':[{'stuecke': [[[20,20],[30,20]], [[70,80],[80,80]]]}], 'ringe':[]}
        self.assertTrue(planpruefung.pruefen(p,self.feld,3)['ok'])

    def test_aufwand_und_befundzahl_begrenzt(self):
        p={'spuren':[{'stuecke': [[[-1,i/10],[-2,i/10]] for i in range(2200)]}], 'ringe':[]}
        r=planpruefung.pruefen(p,self.feld,3)
        self.assertLessEqual(len(r['befunde']),100)
        self.assertFalse(r['vollstaendig'])
        self.assertFalse(r['ok'])
        self.assertIn('limit',r['hinweis'].lower())

    def test_ungueltige_container_und_leerplan(self):
        for p in (None, {}, {'spuren':None}, {'ringe':[None]}, {'ringe':[{'punkte':[[0,0],[1,0],None]}]}):
            self.assertFalse(planpruefung.pruefen(p,self.feld,3)['ok'])


if __name__ == '__main__':
    unittest.main()
