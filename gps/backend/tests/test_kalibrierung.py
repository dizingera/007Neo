"""Lenkwerkstatt über echte HTTP-/SQLite-Grenzen, ohne Motorstart."""
import tempfile
import time
import unittest
from test_tablet_lenkung import geraet
from agripilot.nmea import Fix

class WerkstattTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.context=geraet(self.tmp.name,'client','tablet-a');self.client=self.context.__enter__()
        self.addCleanup(self.context.__exit__,None,None,None)
        self.engine=self.client.app.state.app.engine
        self.engine.on_fix(Fix(lat=48,lon=11,fix_quality=4,speed_ms=0,course_deg=0,received_at=time.time()))
    def stand(self):
        r=self.client.get('/api/lenkung/kalibrierung');self.assertEqual(r.status_code,200,r.text)
        return r.json()['data']
    def senden(self,aktion,stand=None,**werte):
        s=stand or self.stand()
        return self.client.post('/api/lenkung/kalibrierung',json={
            'aktion':aktion,'traktor_id':s['traktor']['id'],'kontext':s['kontext'],**werte})
    def test_geometrie_speichern_und_vorherige_traktorwerte_wiederherstellen(self):
        s=self.stand();alt=s['geometrie']['wheelbase_m'];breite=self.engine.profile.width_m
        werte={**s['geometrie'],'wheelbase_m':3.1}
        r=self.senden('geometrie',s,vermessen=True,werte=werte);self.assertEqual(r.status_code,200,r.text)
        neu=self.stand();self.assertEqual(neu['geometrie']['wheelbase_m'],3.1)
        self.assertIsNotNone(neu['sicherung'])
        r=self.senden('wiederherstellen',neu,sicherung_id=neu['sicherung']['id'])
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.stand()['geometrie']['wheelbase_m'],alt)
        self.assertEqual(self.engine.profile.width_m,breite)
    def test_traktorwechsel_verhindert_speichern_eines_alten_entwurfs(self):
        s=self.stand();self.client.post('/api/traktoren',json={'name':'Zweiter Traktor'})
        r=self.senden('geometrie',s,vermessen=True,werte=s['geometrie'])
        self.assertEqual(r.status_code,409,r.text)
    def test_unplausible_masse_und_fahrende_maschine_aendern_nichts(self):
        s=self.stand();werte={**s['geometrie'],'wheelbase_m':float('nan')}
        r=self.senden('geometrie',s,vermessen=True,werte={**werte,'wheelbase_m':-1})
        self.assertEqual(r.status_code,400,r.text)
        self.engine.fix.speed_ms=2
        r=self.senden('geometrie',s,vermessen=True,werte=s['geometrie'])
        self.assertEqual(r.status_code,400,r.text)
        self.assertIsNone(self.stand()['sicherung'])
    def test_einrichtungsprotokoll_gehoert_zum_traktor(self):
        self.client.post('/api/lenkung/einrichten/erledigt',json={'schritt':'zaehlwerte','werte':{'zaehlwerte_je_grad':40}})
        def fertig():
            s=self.client.get('/api/lenkung/einrichten').json()['data']['schritte']
            return next(x['fertig'] for x in s if x['id']=='zaehlwerte')
        self.assertTrue(fertig())
        self.client.post('/api/traktoren',json={'name':'Anderer Traktor'})
        self.assertFalse(fertig())
    def test_bericht_ist_bei_geaenderten_einstellungen_nicht_mehr_aktuell(self):
        r=self.senden('protokoll');self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(self.stand()['protokoll']['aktuell'])
        self.client.post('/api/traktor',json={'steer_gain':1.1})
        self.assertFalse(self.stand()['protokoll']['aktuell'])
        self.assertIn('uebung',self.stand()['protokoll'])
    def test_motoreinstellungen_machen_alte_pruefmarke_ungueltig(self):
        self.client.post('/api/lenkung/einrichten/erledigt',json={'schritt':'zaehlwerte','werte':{'zaehlwerte_je_grad':40}})
        self.client.app.state.app.config.phidget.encoder_counts_per_deg=80
        s=self.client.get('/api/lenkung/einrichten').json()['data']['schritte']
        self.assertFalse(next(x['fertig'] for x in s if x['id']=='zaehlwerte'))