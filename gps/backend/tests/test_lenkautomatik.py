import math
import tempfile
import unittest
from dataclasses import asdict
from agripilot.guidance import VehicleProfile
from agripilot.lenkautomatik import Lenkautomatik, FELDER


class Automatik(unittest.TestCase):
    def setUp(self):
        self.w={k:v for k,v in asdict(VehicleProfile()).items() if k in FELDER}
        self.gesichert={}
        self.a=Lenkautomatik(lambda:dict(self.w),self.w.update,self.gesichert.update)
        self.t=0.

    def fahrt(self,fehler,sekunden=21,tempo=2.,gueltig=True,kontext='spur-a'):
        for _ in range(round(sekunden*10)):
            self.t+=.1
            e=fehler(self.t) if callable(fehler) else fehler
            self.a.beobachten(self.t,e,tempo,gueltig,kontext,.012,0.)

    def test_gute_spur_wird_ohne_unnoetige_aenderung_ueberwacht(self):
        self.a.starten()
        vorher=dict(self.w)
        self.fahrt(.01,45)
        self.assertEqual(self.w,vorher)
        self.assertEqual(self.a.status()['phase'],'überwacht')
        self.assertEqual(self.a.status()['versuche'],0)

    def test_verschlechterung_wird_zurueckgenommen(self):
        self.a.starten()
        vorher=dict(self.w)
        self.fahrt(lambda t:.12*math.sin(2*math.pi*t/3))
        self.assertLess(self.w['cerea_lenk_aggr'],vorher['cerea_lenk_aggr'])
        self.fahrt(lambda t:.22*math.sin(2*math.pi*t/3),30)
        self.assertEqual(self.w,vorher)
        self.assertEqual(self.a.status()['protokoll'][-1]['aktion'],'zurück')

    def test_messbare_verbesserung_wird_uebernommen(self):
        self.a.starten()
        self.fahrt(lambda t:.12*math.sin(2*math.pi*t/3))
        neu=dict(self.w)
        self.fahrt(.015,30)
        self.assertEqual(self.w,neu)
        self.assertEqual(self.a.status()['protokoll'][-1]['aktion'],'übernommen')
        self.assertIsNone(self.gesichert['versuch'])

    def test_kein_lernen_ohne_freigabe_und_versuch_bei_ausfall_zurueck(self):
        self.a.starten()
        vorher=dict(self.w)
        self.fahrt(.2,45,gueltig=False)
        self.assertEqual(self.w,vorher)
        self.fahrt(lambda t:.12*math.sin(2*math.pi*t/3))
        self.assertNotEqual(self.w,vorher)
        self.fahrt(.2,1,gueltig=False)
        self.assertEqual(self.w,vorher)

    def test_neustart_rollt_unbestaetigten_versuch_zurueck(self):
        self.a.starten()
        vorher=dict(self.w)
        self.fahrt(lambda t:.12*math.sin(2*math.pi*t/3))
        neu=Lenkautomatik(lambda:dict(self.w),self.w.update,self.gesichert.update,dict(self.gesichert))
        self.assertEqual(self.w,vorher)
        self.assertFalse(neu.status()['aktiv'])

    def test_externe_aenderungen_haben_vorrang(self):
        self.a.starten()
        self.fahrt(lambda t:.12*math.sin(2*math.pi*t/3))
        self.w['cerea_lenk_aggr']=67.
        self.fahrt(.1,1)
        self.assertEqual(self.w['cerea_lenk_aggr'],67.)
        self.assertFalse(self.a.status()['aktiv'])

    def test_spurwechsel_nimmt_versuch_zurueck(self):
        self.a.starten()
        vorher=dict(self.w)
        self.fahrt(lambda t:.12*math.sin(2*math.pi*t/3))
        self.fahrt(.1,1,kontext='andere-spur')
        self.assertEqual(self.w,vorher)

    def test_schlechteres_gps_erhoeht_das_erreichbare_ziel(self):
        self.a.starten(2)
        for j in range(450):
            self.a.beobachten(j*.1,.035,2.,True,'spur',.04,0.)
        self.assertAlmostEqual(self.a.status()['effektives_ziel_cm'],6.)
        self.assertEqual(self.a.status()['versuche'],0)

    def test_zurueck_stellt_auch_vorherige_regelart_wieder_her(self):
        vorher=dict(self.w)
        self.a.starten()
        self.a.stoppen(True)
        self.assertEqual(self.w,vorher)


class Bedienung(unittest.TestCase):
    def test_api_start_stopp_und_manuelle_uebernahme(self):
        import warnings
        from fastapi.testclient import TestClient
        from agripilot.config import Config
        from agripilot.server import create_app
        warnings.filterwarnings('ignore')
        with tempfile.TemporaryDirectory() as d:
            cfg=Config()
            cfg.server.data_dir=d
            cfg.steering.enabled=True
            app=create_app(cfg)
            with TestClient(app) as c:
                for ziel in (None, 'kaputt', -1, 0, 11):
                    r=c.post('/api/lenkautomatik',json={'aktion':'start','ziel_cm':ziel})
                    self.assertEqual(r.status_code,400,r.text)
                r=c.post('/api/lenkautomatik',json={'aktion':'start'})
                self.assertEqual(r.status_code,200,r.text)
                self.assertTrue(r.json()['data']['aktiv'])
                self.assertEqual(c.get('/api/state').json()['profile']['regelart'],'cerea')
                self.assertTrue(c.get('/api/lenkautomatik').json()['data']['aktiv'])
                c.post('/api/traktor',json={'cerea_d':.4})
                self.assertFalse(c.get('/api/lenkautomatik').json()['data']['aktiv'])
                self.assertEqual(c.get('/api/state').json()['profile']['cerea_d'],.4)
                self.assertEqual(c.post('/api/lenkautomatik',json={'aktion':'stop'}).status_code,200)


class Fahrzeugmodell(unittest.TestCase):
    def test_automatik_verringert_pendeln_am_traegen_motor(self):
        import random
        from collections import deque
        from dataclasses import replace
        from agripilot.guidance import GuidanceLine
        from agripilot.cerearegler import CereaIntegral
        def fahrt(aktiv):
            rng=random.Random(11)
            p=VehicleProfile(regelart='cerea')
            def lesen():return {k:getattr(p,k) for k in FELDER}
            def setzen(w):
                nonlocal p
                p=replace(p,**w)
            a=Lenkautomatik(lesen,setzen,lambda s:None)
            line=GuidanceLine('ab',[(0,0),(0,50000)],3.)
            ci=CereaIntegral()
            warte=deque([0.]*6)
            dt=.1;tempo=2.5;x=.15;y=h=rad=0.;werte=[]
            for j in range(3600):
                if j==200 and aktiv:a.starten()
                g=line.solve((x+rng.gauss(0,.015),y),h,tempo,p)
                plus=ci.update(g.cross_track_m,dt,tempo,True,True,p,'hin')
                warte.append(p.einschlag_begrenzen(g.steer_angle_deg+plus))
                rad+=max(-6*dt,min(6*dt,(warte.popleft()-rad)*dt))
                hr=math.radians(h);x+=tempo*math.sin(hr)*dt;y+=tempo*math.cos(hr)*dt
                h=(h+math.degrees(tempo/2.6*math.tan(math.radians(rad)))*dt+180)%360-180
                a.beobachten(j*dt,g.cross_track_m,tempo,True,'ab',.015,0.)
                werte.append(x)
            rest=werte[-600:]
            return math.sqrt(sum(v*v for v in rest)/len(rest)),a.status()
        ohne,_=fahrt(False)
        mit,status=fahrt(True)
        self.assertGreater(ohne,.10)
        self.assertLess(mit,.03)
        self.assertLess(mit,ohne*.3)
        self.assertTrue(any(s['aktion']=='übernommen' for s in status['protokoll']))
