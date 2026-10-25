import tempfile,time,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from agripilot.config import Config
from agripilot.server import create_app
from agripilot.nmea import Fix
from agripilot.coverage import CoverageMap

class AusbauApiTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();c=Config();c.server.data_dir=self.tmp.name;c.path=Path(self.tmp.name)/'config.yaml';c.gnss.source='aus';c.imu.source='aus';c.steering.output='none';c.corrections.source='aus';c.network.device_id='test-local'
  self.api=create_app(c);self.a=self.api.state.app;self.e=self.a.engine;self.s=self.a.store;self.c=TestClient(self.api)
  self.f=self.s.save_field(dict(name='Nord',datum_lat=48.,datum_lon=9.,boundary=[[0,0],[100,0],[100,100],[0,100]]));self.e.load_field(self.f['id']);self.e.fix=Fix(lat=48.,lon=9.,speed_ms=0,fix_quality=4,received_at=time.time())
 def tearDown(self):
  self.c.close();self.a.diagnose16.schliessen();self.e.close();self.s.close();self.tmp.cleanup()
 def payload(self):
  k=self.c.get('/api/ausbau/kontext').json()['data'];return dict(field_id=self.f['id'],feld_stand=self.f['updated_at'],traktor_id=next(t['id'] for t in k['traktoren'] if t['aktiv']),profile_id=next(g['id'] for g in k['profile'] if g['aktiv']),breite_m=self.e.profile.width_m,operation='Grubbern')
 def test_start_ohne_lenkfreigabe(self):
  r=self.c.post('/api/arbeitsstart',json=self.payload());self.assertEqual(r.status_code,200,r.text);self.assertFalse(self.e.steering.armed);self.assertEqual(self.e.job['operation'],'Grubbern')
 def test_start_bewegung_und_alter_feldstand_abgewiesen(self):
  p=self.payload();self.e.fix.speed_ms=1;self.assertEqual(self.c.post('/api/arbeitsstart',json=p).status_code,400);self.assertIsNone(self.e.job)
  self.e.fix.speed_ms=0;p['feld_stand']-=1;self.assertEqual(self.c.post('/api/arbeitsstart',json=p).status_code,400);self.assertIsNone(self.e.job)
 def test_vorlage_aendert_keine_breite(self):
  old=self.e.profile.width_m;p=self.payload();r=self.c.post('/api/komfort',json={'vorlagen':[{'id':'v','name':'Test','operation':'Grubbern','width_m':99}]});self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.e.profile.width_m,old);self.assertNotIn('width_m',r.json()['data']['vorlagen'][0])
 def test_vorschau_schreibt_nichts_und_veralteter_stand_speichert_nicht(self):
  alt=self.e.profile.steer_gain;werte={'steer_gain':alt+.1};r=self.c.post('/api/aenderungsvorschau',json={'ziel':'/api/profile','werte':werte});self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.e.profile.steer_gain,alt);stand=r.json()['data']['stand'];self.e.update_profile({'steer_gain':alt+.2});r=self.c.post('/api/profile',json={**werte,'_vorschau':stand});self.assertEqual(r.status_code,400,r.text);self.assertAlmostEqual(self.e.profile.steer_gain,alt+.2)
 def test_vorschau_geheimnis_wird_nie_ausgegeben(self):
  r=self.c.post('/api/aenderungsvorschau',json={'ziel':'/api/settings','werte':{'aenderungen':{'network.sync_token':'synthetic-never-real-secret'}}});self.assertEqual(r.status_code,200,r.text);self.assertNotIn('synthetic-never-real-secret',r.text);self.assertIn('Geschützt',r.text)
 def test_kosten_guard_restflaeche_pdf_echt(self):
  j=self.s.start_job(self.f['id'],'test-local',operation='Grubbern');m=CoverageMap(.5);m.cells={(0,0),(1,0)};self.s.update_job(j['id'],coverage=m.pack(),working_time_s=180,ended_at=time.time(),area_ha=.00005)
  r=self.c.get('/api/jobs/'+j['id']+'/restflaeche');self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['data']['bekannt'])
  self.e.fix.speed_ms=2;self.assertEqual(self.c.post('/api/jobs/'+j['id']+'/kosten',json={'diesel_liter':2}).status_code,400);self.e.fix.speed_ms=0
  r=self.c.post('/api/jobs/'+j['id']+'/kosten',json={'diesel_liter':2,'diesel_preis_eur_l':1.5,'betriebsmittel_eur':0,'stundensatz_eur':20});self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['data']['diesel_eur'],3)
  r=self.c.get('/api/fields/'+self.f['id']+'/bericht.pdf');self.assertEqual(r.status_code,200,r.text[:200]);self.assertTrue(r.content.startswith(b'%PDF-'))
 def test_diagnose_routes_keine_falsche_abnahme(self):
  self.assertEqual(self.c.get('/api/ausbau/diagnose').status_code,200)
  self.assertEqual(self.c.post('/api/ausbau/abnahme',json={'bestaetigt':True}).status_code,400)
  self.assertEqual(self.c.post('/api/ausbau/fahrserie',json={'aktion':'vorher','etappe':'gerade','bestaetigt':False}).status_code,400)
 def test_papierkorb_route(self):
  self.assertEqual(self.c.get('/api/papierkorb').status_code,200)

 def test_neues_raster_wird_vollstaendig_an_browser_ersetzt(self):
  self.e.coverage.cells={(0,0)}
  zuerst=self.a._live_message();self.assertEqual(zuerst['type'],'init');self.assertEqual(zuerst['cells'],[[0,0]])
  self.assertEqual(self.a._live_message()['type'],'state')
  self.e.coverage=CoverageMap(.5)
  neu=self.a._live_message();self.assertEqual(neu['type'],'init');self.assertEqual(neu['cells'],[])

 def test_feldladen_merkt_zuletzt_nur_bei_erfolg(self):
  r=self.c.post('/api/fields/'+self.f['id']+'/load');self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.s.get_setting('arbeitskomfort')['zuletzt'],[self.f['id']])
  r=self.c.post('/api/fields/nicht-vorhanden/load');self.assertEqual(r.status_code,400);self.assertEqual(self.s.get_setting('arbeitskomfort')['zuletzt'],[self.f['id']])
