"""Integrationsgegenfälle: keine Kalibrierung aus alten Werten, keine Kartenverluste."""
import math
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from agripilot.actuators import OutputFeedback, UdpOutput
from agripilot.config import Config
from agripilot.engine import Engine
from agripilot.guidance import GuidanceLine, GuidanceState
from agripilot.nmea import Fix
from agripilot.steering import SteeringController
from agripilot.storage import Storage


class EnginePruefung(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.cfg=Config();self.cfg.network.device_id='nur-test';self.cfg.steering.enabled=True
        self.s=Storage(Path(self.tmp.name)/'test.db');self.e=Engine(self.cfg,self.s)
    def tearDown(self):
        self.e.close();self.s.close();self.tmp.cleanup()
    def vorbereiten(self):
        output=UdpOutput('127.0.0.1',0)  # Kein start(), kein Netzwerk oder Motor.
        output.ready=True;self.e.steering=SteeringController(self.cfg.steering,output);self.e.steering.arm()
        self.e.line=GuidanceLine('ab',[(0,0),(0,1000)],3.)
        self.e.guidance=GuidanceState(active=True,cross_track_m=0.,heading_error_deg=0.)
        self.e.radmitte.start()
        return output
    def fahren(self,output,frisch=False,anzahl=600):
        start=time.time()
        for i in range(anzahl):
            now=start+i/10
            if frisch:output.feedback=OutputFeedback(wheel_angle_deg=1.2,received_at=now)
            fix=Fix(lat=48.,lon=11.,speed_ms=2.,fix_quality=4,received_at=now)
            with mock.patch('time.time',return_value=now):self.e._update_steering(fix)
    def test_alter_radwinkel_wird_nicht_als_radmitte_gelernt(self):
        output=self.vorbereiten();output.feedback=OutputFeedback(wheel_angle_deg=1.2,received_at=time.time()-10)
        self.fahren(output)
        self.assertEqual(len(self.e.radmitte.proben),0)
        self.assertFalse(self.e.radmitte.ergebnis()['gueltig'])
        self.assertEqual(len(self.e.nachlauf.proben),0)
    def test_frische_radwinkel_bleiben_fuer_kalibrierung_verwendbar(self):
        output=self.vorbereiten();self.fahren(output,frisch=True)
        self.assertEqual(len(self.e.radmitte.proben),600)
        self.assertTrue(self.e.radmitte.ergebnis()['gueltig'])
    def test_simulator_stempelt_nur_tatsaechlich_gemessene_rueckmeldung(self):
        output=self.vorbereiten()
        simulator=mock.Mock();simulator.gemessener_radwinkel.return_value=1.2
        self.e.simulator=simulator;self.fahren(output,anzahl=1)
        self.assertTrue(output.feedback.fresh)
        self.assertEqual(output.feedback.wheel_angle_deg,1.2)
        self.assertEqual(len(self.e.radmitte.proben),1)
    def test_beschaedigte_oder_fehlende_arbeitskarte_bleibt_beim_neustart_erhalten(self):
        for blob in (b'kap',b'',struct.pack('<f',float('nan')),struct.pack('<f',.5)+b'x',None):
            with self.subTest(blob=blob), tempfile.TemporaryDirectory() as tmp:
                store=Storage(Path(tmp)/'restart.db');engine=Engine(self.cfg,store)
                try:
                    f=store.save_field(dict(name='Testfeld',datum_lat=48.,datum_lon=9.))
                    engine.load_field(f['id']);j=engine.start_job('Pruefung');engine.suspend_job();engine.close()
                    # Beschädigung ausschließlich in temporärer Testdatenbank herstellen.
                    store.db.execute('UPDATE jobs SET coverage=?,area_ha=.25,working_time_s=300 WHERE id=?',(blob,j['id']));store.db.commit()
                    engine=Engine(self.cfg,store);result=engine.resume_session()
                    self.assertIsNone(engine.job)
                    self.assertFalse(result['arbeit'])
                    self.assertIn('nicht fortgesetzt',result['text'])
                    engine.suspend_job()
                    self.assertEqual(store.get_job_coverage(j['id']),blob)
                    self.assertEqual(store.get_job(j['id'])['area_ha'],.25)
                    self.assertEqual(store.get_job(j['id'])['working_time_s'],300)
                    self.assertTrue(store.get_job(j['id'])['pausiert'])
                    engine.close();engine=Engine(self.cfg,store);engine.resume_session()
                    self.assertIsNone(engine.job)
                finally:
                    engine.close();store.close()
