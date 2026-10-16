"""Kalman-Position an der Filtergrenze und in der tatsächlichen Engine."""
import math
import tempfile
import unittest
from agripilot.kalman import Positionsfilter
from agripilot.config import Config
from agripilot.engine import Engine
from agripilot.nmea import Fix
from agripilot.storage import Storage

class KalmanTests(unittest.TestCase):
    def test_bekannter_stillstand_wird_ruhiger(self):
        f=Positionsfilter();werte=[]
        for i in range(100):
            x,y=f.update((0.4 if i%2 else -0.4,0),0,0,i/10,1,1.0)
            if i>20:werte.append(x)
        self.assertLess(math.sqrt(sum(x*x for x in werte)/len(werte)),0.2)

    def test_gleichfoermige_fahrt_bleibt_ohne_schleppfehler(self):
        f=Positionsfilter()
        for i in range(100):p=f.update((0,i*0.2),2,0,i/10,4,0.03)
        self.assertAlmostEqual(p[1],19.8,places=5)

    def test_ausreisser_zaehlt_nicht_als_normale_position(self):
        f=Positionsfilter();f.update((0,0),0,0,0,4,0.03)
        p=f.update((50,50),0,0,0.1,4,0.03)
        self.assertFalse(f.status()["akzeptiert"])
        self.assertLess(math.hypot(*p),0.1)

    def test_luecke_qualitaetswechsel_und_zeit_rueckwaerts_setzen_neu_an(self):
        for zeit,qualitaet in ((2,4),(-1,4),(0.1,5)):
            f=Positionsfilter();f.update((0,0),0,0,0,4,0.03)
            self.assertEqual(f.update((20,20),0,0,zeit,qualitaet,0.03),(20,20))
            self.assertTrue(f.status()["akzeptiert"])

    def test_engine_nutzt_filter_ohne_rohfix_oder_rtk_qualitaet_zu_faelschen(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Config();config.gnss.kalman_enabled=True
            store=Storage(tmp+'/test.db')
            try:
                e=Engine(config,store)
                for i in range(20):
                    fix=Fix(lat=48+((-1 if i%2 else 1)*0.4/111200),lon=11,
                        course_deg=0,heading_deg=0,speed_ms=0,fix_quality=1,received_at=100+i/10)
                    e.on_fix(fix)
                self.assertIs(e.fix,fix)
                self.assertEqual(e.state()["fix"]["fix_quality"],1)
                self.assertTrue(e.state()["positionsfilter"]["aktiv"])
            finally:store.close()