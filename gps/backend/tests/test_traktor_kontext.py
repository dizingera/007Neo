import tempfile
import unittest
from fastapi.testclient import TestClient
from agripilot.config import Config
from agripilot.server import create_app

class TraktorKontextTests(unittest.TestCase):
    def test_alter_entwurf_darf_anderen_traktor_nicht_aendern(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=Config();cfg.server.data_dir=d;cfg.gnss.source='aus';cfg.imu.source='aus';cfg.steering.output='none'
            api=create_app(cfg);app=api.state.app;e=app.engine
            try:
                with TestClient(api) as c:
                    a=c.get('/api/state').json()['traktor_id']
                    b=e.save_traktor_as('Zweiter Traktor')
                    e.select_traktor(b['id'])
                    alt=e.profile.cerea_lenk_aggr
                    r=c.post('/api/traktor',json={'erwartete_traktor_id':a,'cerea_lenk_aggr':140})
                    self.assertEqual(r.status_code,400,r.text)
                    self.assertEqual(e.profile.cerea_lenk_aggr,alt)
                    r=c.post('/api/traktor',json={'erwartete_traktor_id':b['id'],'cerea_lenk_aggr':140})
                    self.assertEqual(r.status_code,200,r.text)
                    self.assertEqual(e.profile.cerea_lenk_aggr,140)
            finally:app.store.close()

    def test_verzoegerte_automatik_und_probefahrt_bleiben_beim_traktor(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as d:
            cfg=Config();cfg.server.data_dir=d;cfg.gnss.source='aus';cfg.imu.source='aus';cfg.steering.output='none'
            api=create_app(cfg);app=api.state.app;e=app.engine
            try:
                a=e.aktiver_traktor()['id'];b=e.save_traktor_as('Traktor B');e.select_traktor(b['id'])
                e.automatik_bedienen=Mock(return_value={});e.probefahrt_bedienen=Mock(return_value={})
                with TestClient(api) as c:
                    for route,fn in (('/api/lenkautomatik',e.automatik_bedienen),('/api/probefahrt',e.probefahrt_bedienen)):
                        r=c.post(route,json={'aktion':'start','erwartete_traktor_id':a})
                        self.assertEqual(r.status_code,400,r.text);fn.assert_not_called()
            finally:app.store.close()
