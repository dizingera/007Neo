import copy
import math
import unittest

from agripilot.probefahrt import Probefahrt, kennzahlen


class Vergleich(unittest.TestCase):
    def setUp(self):
        self.berichte = []
        self.p = Probefahrt(self.berichte.append)
        self.t = 0.
        self.cfg = {'gain': 1}

    def fahrt(self, quer=.1, tempo=2., kontext='ab', gps=.02, gueltig=True):
        for _ in range(311):
            self.t = round(self.t+.1, 1)
            self.p.beobachten(self.t, quer, tempo, gueltig, kontext, self.cfg,
                             gps, 0., 3., 2., .55, .55)

    def vorher(self):
        self.p.bedienen('vorher', self.cfg)
        self.fahrt()
        self.assertEqual(self.p.phase, 'bereit')

    def test_vorher_nachher_misst_und_sichert_echte_verbesserung(self):
        self.vorher()
        self.cfg = {'gain': 2}
        self.p.bedienen('nachher', self.cfg)
        self.fahrt(.02)
        b = self.p.bericht
        self.assertTrue(b['besser'])
        self.assertAlmostEqual(b['rms_aenderung_prozent'], 80.)
        self.assertEqual(b['vorher']['werte']['motor_grenze_anteil'], 1.)
        self.assertEqual(b['vorher']['werte']['radfehler_deg'], 1.)
        self.assertEqual(b['nachher']['einstellungen'], {'gain': 2})
        self.assertEqual(len(self.berichte), 1)
        self.assertEqual(self.cfg, {'gain': 2})

    def test_andere_spur_oder_richtung_wird_nicht_verglichen(self):
        self.vorher()
        self.p.bedienen('nachher', self.cfg)
        self.fahrt(.01, kontext='gegenrichtung')
        self.assertEqual(self.p.phase, 'nachher')
        self.assertEqual(self.p.status()['sammelt_s'], 0.)
        self.assertIsNone(self.p.bericht)

    def test_andere_empfangsqualitaet_ist_keine_bestaetigte_verbesserung(self):
        self.vorher()
        self.p.bedienen('nachher', self.cfg)
        self.fahrt(.01, gps=.2)
        self.assertFalse(self.p.bericht['vergleichbar'])
        self.assertFalse(self.p.bericht['besser'])

    def test_messluecke_und_ungueltige_daten_beginnen_neu(self):
        self.p.bedienen('vorher', self.cfg)
        for t in range(20):
            self.p.beobachten(t, .1, 2., True, 'ab', self.cfg)
        self.p.beobachten(30., .1, 2., True, 'ab', self.cfg)
        self.assertEqual(self.p.status()['sammelt_s'], 0.)
        self.fahrt(gueltig=False)
        self.assertEqual(self.p.status()['sammelt_s'], 0.)
        self.assertIsNone(self.p.vorher)

    def test_einstellungswechsel_in_messung_bricht_sie_ab(self):
        self.p.bedienen('vorher', self.cfg)
        self.cfg['gain'] = 2
        self.fahrt()
        self.assertEqual(self.p.phase, 'aus')
        self.assertIsNone(self.p.vorher)

    def test_erst_vorher_messen_und_neustart_setzt_nicht_fort(self):
        with self.assertRaises(ValueError):
            self.p.bedienen('nachher', self.cfg)
        self.vorher()
        self.p.bedienen('nachher', self.cfg)
        self.fahrt(.02)
        neu = Probefahrt(lambda b: None, self.p.bericht)
        self.assertFalse(neu.sammelt)
        self.assertEqual(neu.bericht, self.p.bericht)
        neu.bericht['vorher']['werte']['rms_cm'] = 999
        self.assertNotEqual(neu.bericht, self.p.bericht)

    def test_nachlauf_aus_dem_zeitversetzten_radwinkel(self):
        punkte = [dict(t=j/10, quer=.01, tempo=2., gps=.02, roll=0.,
                       soll=5*math.sin(j/10), ist=5*math.sin(j/10-.6),
                       leistung=None, grenze=None) for j in range(400)]
        k = kennzahlen(punkte)
        self.assertAlmostEqual(k['nachlauf_s'], .6, delta=.1)
        self.assertIsNone(k['motor_grenze_anteil'])
        for p in punkte:
            p['ist'] = None
        self.assertIsNone(kennzahlen(punkte)['nachlauf_s'])


class Schnittstelle(unittest.TestCase):
    def test_api_startet_beobachtung_ohne_motorfreigabe_und_sperrt_paralleles_lernen(self):
        import tempfile
        from fastapi.testclient import TestClient
        from agripilot.config import Config
        from agripilot.server import create_app
        with tempfile.TemporaryDirectory() as d:
            cfg = Config()
            cfg.server.data_dir = d
            cfg.steering.enabled = True
            with TestClient(create_app(cfg)) as c:
                self.assertEqual(c.get('/api/probefahrt/bericht').status_code, 404)
                self.assertEqual(c.post('/api/lenkautomatik', json={'aktion':'start'}).status_code,200)
                self.assertEqual(c.post('/api/probefahrt', json={'aktion':'nachher'}).status_code, 400)
                self.assertTrue(c.get('/api/lenkautomatik').json()['data']['aktiv'])
                r = c.post('/api/probefahrt', json={'aktion':'vorher'})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertTrue(r.json()['data']['sammelt'])
                self.assertFalse(c.get('/api/state').json()['steering']['armed'])
                self.assertEqual(c.post('/api/lenkautomatik', json={'aktion':'start'}).status_code, 400)
                self.assertEqual(c.post('/api/abstimmung', json={'modus':'automatisch'}).status_code, 400)
                self.assertEqual(c.post('/api/probefahrt', json={'aktion':'abbrechen'}).status_code, 200)


class EngineMessung(unittest.TestCase):
    def test_engine_verlangt_frische_imu_und_schreibt_ihren_nachweis(self):
        import tempfile
        import time
        from types import SimpleNamespace
        from fastapi.testclient import TestClient
        from agripilot.config import Config
        from agripilot.server import create_app
        from agripilot.guidance import GuidanceLine, GuidanceState
        from agripilot.nmea import Fix
        with tempfile.TemporaryDirectory() as d:
            cfg=Config(); cfg.server.data_dir=d; cfg.steering.enabled=True
            app=create_app(cfg)
            with TestClient(app) as c:
                e=app.state.app.engine
                e.line=GuidanceLine('ab',[(0,0),(0,5000)],3.)
                e.guidance=GuidanceState(active=True,cross_track_m=.1)
                e.imu=SimpleNamespace(attitude=SimpleNamespace(fresh=False,roll_deg=0.))
                self.assertEqual(c.post('/api/probefahrt',json={'aktion':'vorher'}).status_code,200)
                beginn=time.time()
                for j in range(201):
                    f=Fix(lat=48.,lon=11.,fix_quality=4,speed_ms=2.,received_at=beginn+j/10)
                    e._automatik_beobachten(True,f)
                self.assertIsNone(e.probefahrt.vorher)
                self.assertEqual(e.probefahrt.status()['sammelt_s'],0.)
                e.imu.attitude.fresh=True
                for j in range(201,522):
                    f=Fix(lat=48.,lon=11.,fix_quality=4,speed_ms=2.,received_at=beginn+j/10)
                    e._automatik_beobachten(True,f)
                self.assertIsNotNone(e.probefahrt.vorher)
                self.assertEqual(e.probefahrt.vorher['werte']['imu_anteil'],1.)
                self.assertEqual(c.post('/api/probefahrt',json={'aktion':'nachher'}).status_code,200)
                e.guidance.cross_track_m=.02
                for j in range(530,852):
                    f=Fix(lat=48.,lon=11.,fix_quality=4,speed_ms=2.,received_at=beginn+j/10)
                    e._automatik_beobachten(True,f)
                bericht=c.get('/api/probefahrt/bericht')
                self.assertEqual(bericht.status_code,200,bericht.text)
                self.assertIn('attachment',bericht.headers['Content-Disposition'])
                self.assertTrue(bericht.json()['besser'])
                self.assertFalse(e.steering.armed)

    def test_anderes_gespann_darf_keine_nachher_messung_beginnen(self):
        p=Probefahrt(lambda b:None)
        p.bedienen('vorher',{'traktor':'a','geraet':'g'})
        for j in range(311):
            p.beobachten(j/10,.1,2.,True,'ab',{'traktor':'a','geraet':'g'})
        with self.assertRaises(ValueError):
            p.bedienen('nachher',{'traktor':'b','geraet':'g'})
