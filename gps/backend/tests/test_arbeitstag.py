"""Integrationsfälle der Tagesbedienung; keine Hardware und keine produktiven Daten."""
import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from agripilot.config import Config
from agripilot.server import create_app
from agripilot.nmea import Fix
from agripilot import arbeitstag
from agripilot.uebung import konfigurieren

class ArbeitstagTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.cfg=Config();self.cfg.server.data_dir=self.tmp.name
        self.cfg.path=Path(self.tmp.name)/"config.yaml"
        self.cfg.gnss.source="aus";self.cfg.imu.source="aus"
        self.cfg.steering.output="none";self.cfg.corrections.source="aus"
        self.api=create_app(self.cfg);self.app=self.api.state.app;self.e=self.app.engine
        self.c=TestClient(self.api)
        self.f=self.app.store.save_field(dict(name="Testfeld",datum_lat=48.,datum_lon=11.,boundary=[[0,0],[100,0],[100,100],[0,100]]))
        self.e.load_field(self.f['id'])
        self.e.fix=Fix(lat=48.,lon=11.,fix_quality=4,speed_ms=0,received_at=time.time())
    def tearDown(self):
        self.c.close();self.app.store.close();self.tmp.cleanup()
    def test_tagesstart_mit_echter_spur(self):
        l=self.e.store.save_line(dict(field_id=self.f["id"],name="Spur",mode="ab",points=[[10,10],[10,90]],spacing_m=3.))
        self.e.load_line(l["id"])
        d=self.c.get("/api/arbeitstag").json()["data"]
        self.assertEqual(len(d["einstieg"]["start"]),2)
        self.assertEqual(len(d["einstieg"]["start_xy"]),2)
        self.assertTrue(d["offline"]["spur"])

    def test_tagesstart_ohne_spur_ist_nicht_bereit(self):
        r=self.c.get('/api/arbeitstag');self.assertEqual(r.status_code,200,r.text)
        d=r.json()['data'];self.assertFalse(d['offline']['spur']);self.assertIn('gespann',d)
        self.assertNotIn('sync_token',str(d));self.assertNotIn('arbeitercode',str(d))
    def test_einfahrt_setzen_loeschen(self):
        r=self.c.post('/api/arbeitstag/einfahrt',json={'aktion':'setzen'});self.assertEqual(r.status_code,200,r.text)
        d=self.c.get('/api/arbeitstag').json()['data'];self.assertEqual(d['einstieg']['einfahrt']['lat'],48.)
        self.assertEqual(d['einstieg']['einfahrt_xy'],[0.,0.])
        self.assertEqual(self.c.post('/api/arbeitstag/einfahrt',json={'aktion':'loeschen'}).status_code,200)
        self.assertTrue(self.e.store.get_setting('feld_einfahrt:'+self.f['id'])['geloescht'])
    def test_einfahrt_nicht_waehrend_fahrt(self):
        self.e.fix.speed_ms=1
        self.assertEqual(self.c.post('/api/arbeitstag/einfahrt',json={'aktion':'setzen'}).status_code,400)
    def test_stillstand_ohne_frisches_gps_unbekannt(self):
        self.e.fix.received_at=time.time()-5
        self.assertEqual(self.c.post('/api/backup/pruefen').status_code,400)
    def test_sicherung_fehlt_lesbarer_fehler(self):
        r=self.c.post('/api/backup/pruefen');self.assertEqual(r.status_code,400)
    def test_plan_fehlt_keine_freigabe(self):
        r=self.c.get('/api/arbeitstag/planpruefung');self.assertEqual(r.status_code,400)
    def test_profilverlauf_echt_wiederherstellen(self):
        alt=self.e.profile.steer_gain
        self.e.update_profile({'steer_gain':alt+.1})
        buch=self.c.get('/api/betriebsbuch').json()['data'];ein=buch['einstellungen'][0]
        self.assertEqual(ein['quelle'],'fahrer')
        r=self.c.post('/api/betriebsbuch/wiederherstellen',json={'id':ein['id']})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.e.profile.steer_gain,alt)
        self.assertFalse(self.e.steering.armed)
        self.assertEqual(self.e.betriebsbuch.verlauf()['einstellungen'][0]['quelle'],'wiederherstellung')
    def test_automatik_aenderung_kennt_quelle(self):
        self.e._automatik_setzen({'cerea_d':.25})
        self.assertEqual(self.e.betriebsbuch.verlauf()['einstellungen'][0]['quelle'],'automatik')
    def test_motorverlauf_ohne_zugangsdaten(self):
        r=self.c.post('/api/settings',json={'aenderungen':{'phidget.max_duty':.6}})
        self.assertEqual(r.status_code,200,r.text)
        ein=self.e.betriebsbuch.verlauf()['einstellungen'][0]
        self.assertIn('phidget.max_duty',ein['aenderungen'])
        self.assertNotIn('network',str(ein))
    def test_archiv_und_csv_formelgeschuetzt(self):
        j=self.app.store.start_job(self.f['id'],self.cfg.network.device_id,'Gerät','=1+1')
        self.app.store.update_job(j['id'],area_ha=1.5,distance_m=123)
        a=self.c.get('/api/archiv').json()['data'];self.assertEqual(len(a['jobs']),1)
        self.assertEqual(a['flaeche_ha'],1.5)
        csv=self.c.get('/api/archiv/bericht.csv');self.assertIn("'=1+1",csv.text)
        self.assertIn('attachment',csv.headers['content-disposition'])
    def test_fremde_arbeit_nicht_fortsetzen(self):
        j=self.app.store.start_job(self.f['id'],'anderer','Gerät','Arbeit')
        r=self.c.post('/api/arbeitstag/fortsetzen',json={'job_id':j['id']})
        self.assertEqual(r.status_code,400);self.assertIsNone(self.e.job)
    def test_abgeschlossene_arbeit_nicht_fortsetzen(self):
        j=self.app.store.start_job(self.f['id'],self.cfg.network.device_id,'Gerät','Arbeit')
        self.app.store.update_job(j['id'],ended_at=time.time())
        self.assertEqual(self.c.post('/api/arbeitstag/fortsetzen',json={'job_id':j['id']}).status_code,400)
    def test_tunnel_darf_neue_bedienwege_nicht_aufrufen(self):
        self.assertEqual(self.c.get('/api/arbeitstag',headers={'cf-ray':'test'}).status_code,403)
    def test_uebung_konfig_bleibt_getrennt_und_hardwarefrei(self):
        cfg=konfigurieren(Path(self.tmp.name)/'uebung')
        self.assertNotEqual(cfg.db_path,self.cfg.db_path)
        self.assertEqual(cfg.path.parent,Path(self.tmp.name)/'uebung')
        self.assertEqual(cfg.steering.output,'none');self.assertEqual(cfg.teilbreiten.output,'aus')
        self.assertEqual(cfg.network.master_url,'');self.assertEqual(cfg.network.device_id,'uebung-lokal')
    def test_uebung_verweigert_sync_und_hardwareeinstellungen(self):
        self.cfg.network.device_id='uebung-lokal'
        self.assertEqual(self.c.post('/api/sync',json={}).status_code,403)
        self.assertEqual(self.c.post('/api/settings',json={'aenderungen':{'steering.output':'phidget'}}).status_code,403)

    def test_uebung_darf_programm_nicht_zurueckrollen(self):
        from unittest.mock import patch
        self.cfg.network.device_id='uebung-lokal'
        with patch('agripilot.update.zurueckholen') as rueck:
            self.assertEqual(self.c.post('/api/update/zurueck').status_code,403)
            rueck.assert_not_called()
    def test_alte_rtk_position_ist_nicht_bereit(self):
        self.e.fix.received_at=time.time()-10
        d=self.c.get('/api/arbeitstag').json()['data']
        self.assertEqual(next(x for x in d['checks'] if x['titel']=='RTK')['status'],'offen')
    def test_anzeigeausgang_ist_kein_motor(self):
        self.cfg.steering.enabled=True
        self.e.steering.output.ready=True
        d=self.c.get('/api/arbeitstag').json()['data']
        self.assertEqual(next(x for x in d['checks'] if x['titel']=='Lenkmotor')['status'],'offen')
    def test_ungueltige_alte_geometrie_lesbar_ablehnen(self):
        ein=self.e.betriebsbuch.einstellung_aendern({'traktor_vorn_m':31.},{'traktor_vorn_m':2.},'fahrer')
        r=self.c.post('/api/betriebsbuch/wiederherstellen',json={'id':ein['id']})
        self.assertEqual(r.status_code,400,r.text)
