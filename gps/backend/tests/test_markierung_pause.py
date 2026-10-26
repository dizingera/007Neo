"""Markierungsschalter und Lenkung-Aus erhalten dieselbe Arbeit."""
import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from agripilot.config import Config
from agripilot.server import create_app
from agripilot.nmea import Fix

class MarkierungPauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        cfg=Config();cfg.network.device_id='pruefgeraet';cfg.server.data_dir=self.tmp.name;cfg.path=Path(self.tmp.name)/'config.yaml'
        cfg.gnss.source=cfg.imu.source=cfg.corrections.source='aus';cfg.steering.output='none'
        self.api=create_app(cfg);self.app=self.api.state.app;self.e=self.app.engine;self.s=self.app.store;self.c=TestClient(self.api)
        f=self.s.save_field(dict(name='F',datum_lat=48.,datum_lon=9.,boundary=[[0,0],[100,0],[100,100],[0,100]]));self.e.load_field(f['id'])
        self.e.fix=Fix(lat=48.,lon=9.,speed_ms=2.,fix_quality=4,received_at=time.time())
    def tearDown(self):
        self.c.close();self.app.diagnose16.schliessen();self.e.close();self.s.close();self.tmp.cleanup()
    def markieren(self,an):
        r=self.c.post('/api/job/markierung',json={'an':an,'operation':'Säen'});self.assertEqual(r.status_code,200,r.text);return r.json()['data']
    def test_aus_an_erhaelt_job_karte_kennzahlen_auch_waehrend_fahrt(self):
        j=self.markieren(True);self.e.coverage.cells={(1,2),(3,4)};self.e.coverage.applied_area_m2=2.;self.e.distance_m=120.;self.e.working_time_s=60.
        self.markieren(False);self.assertIsNone(self.e.job);self.assertEqual(self.e.coverage.cells,{(1,2),(3,4)})
        self.assertIsNone(self.s.get_job(j['id'])['ended_at'])
        j2=self.markieren(True);self.assertEqual(j2['id'],j['id']);self.assertEqual(self.e.coverage.cells,{(1,2),(3,4)})
        self.assertEqual(self.e.distance_m,120.);self.assertEqual(self.e.working_time_s,60.);self.assertAlmostEqual(self.e.coverage.applied_area_m2,2.)
        self.assertIsNone(self.e._last_update_at);self.assertEqual(len(self.s.list_jobs()),1)
        self.assertEqual(self.markieren(True)['id'],j['id']);self.markieren(False);self.markieren(False)
        self.assertEqual(len(self.s.list_jobs()),1)
    def test_lenkung_aus_pausiert_markierung_sofort_ohne_flaechenverlust(self):
        j=self.e.start_job('Säen');self.e.coverage.cells={(1,2)}
        self.app.steering.armed=True
        r=self.c.post('/api/steering/disarm');self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(self.app.steering.armed);self.assertIsNone(self.e.job)
        self.assertEqual(self.e.coverage.cells,{(1,2)});self.assertIsNone(self.s.get_job(j['id'])['ended_at'])
        self.assertEqual(self.markieren(True)['id'],j['id'])
    def test_neue_arbeit_bleibt_ausdruecklich_neu(self):
        old=self.markieren(True);self.e.coverage.cells={(1,2)};self.markieren(False)
        new=self.e.start_job('Andere Tätigkeit');self.assertNotEqual(old['id'],new['id']);self.assertEqual(self.e.coverage.cells,set());self.assertIsNone(self.e.paused_job)
    def test_falsches_geraet_setzt_pausierte_arbeit_nicht_fort(self):
        j=self.markieren(True);self.e.coverage.cells={(1,2)};self.markieren(False);blob=self.s.get_job_coverage(j['id'])
        self.e.profile.name='Fremdes Gerät'
        r=self.c.post('/api/job/markierung',json={'an':True});self.assertEqual(r.status_code,400,r.text)
        self.assertIsNone(self.e.job);self.assertEqual(self.s.get_job_coverage(j['id']),blob)
    def test_defekte_karte_wird_beim_wiedereinschalten_nicht_geleert(self):
        j=self.markieren(True);self.e.coverage.cells={(1,2)};self.markieren(False)
        self.s.db.execute('UPDATE jobs SET coverage=? WHERE id=?',(b'kap',j['id']));self.s.db.commit()
        r=self.c.post('/api/job/markierung',json={'an':True});self.assertEqual(r.status_code,400,r.text)
        self.assertIsNone(self.e.job);self.assertEqual(self.s.get_job_coverage(j['id']),b'kap')
