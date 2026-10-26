"""Öffentliche Arbeitsstart-Gegenfälle aus der Gesamtprüfung 1.5.105."""
import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from agripilot.config import Config
from agripilot.server import create_app
from agripilot.nmea import Fix


class ArbeitsstartPruefung(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Config()
        cfg.server.data_dir = self.tmp.name
        cfg.path = Path(self.tmp.name) / 'config.yaml'
        cfg.gnss.source = cfg.imu.source = cfg.corrections.source = 'aus'
        cfg.steering.output = 'none'
        self.api = create_app(cfg)
        self.app = self.api.state.app
        self.e, self.s = self.app.engine, self.app.store
        self.c = TestClient(self.api)
        self.f = self.s.save_field(dict(name='Testfeld', datum_lat=48., datum_lon=9., boundary=[[0,0],[100,0],[100,100],[0,100]]))
        self.e.load_field(self.f['id'])
        self.e.fix = Fix(lat=48., lon=9., speed_ms=0, fix_quality=4, received_at=time.time())
        self.traktor = self.e.aktiver_traktor()['id']
        self.geraet = next(x['id'] for x in self.e.list_profiles() if x['aktiv'])
        self.drescher = self.e.save_traktor_as('Test-Drescher', {'bauform':'maehdrescher'})['id']
        self.e.geraet_zum_fahrzeug()
        self.schneidwerk = next(x['id'] for x in self.e.list_profiles() if x['aktiv'])
        self.e.traktor_waehlen(self.traktor)

    def tearDown(self):
        self.c.close()
        self.app.diagnose16.schliessen()
        self.e.close()
        self.s.close()
        self.tmp.cleanup()

    def start(self, tractor, profile):
        p = next(x for x in self.e.list_profiles() if x['id'] == profile)
        return self.c.post('/api/arbeitsstart', json=dict(field_id=self.f['id'], feld_stand=self.f['updated_at'], traktor_id=tractor, profile_id=profile, breite_m=p['width_m'], operation='Testarbeit'))

    def test_drescher_mit_heckgeraet_wird_vor_jeder_aenderung_abgewiesen(self):
        before = (self.s.get_setting('traktor_aktiv'), self.s.get_setting('vehicle_profile_active'))
        result = self.start(self.drescher, self.geraet)
        self.assertEqual(result.status_code, 400, result.text)
        self.assertIsNone(self.e.job)
        self.assertEqual(before, (self.s.get_setting('traktor_aktiv'), self.s.get_setting('vehicle_profile_active')))

    def test_traktor_mit_schneidwerk_wird_abgewiesen(self):
        result = self.start(self.traktor, self.schneidwerk)
        self.assertEqual(result.status_code, 400, result.text)
        self.assertIsNone(self.e.job)

    def test_passender_drescher_erhaelt_schneidwerk_und_bleibt_unscharf(self):
        result = self.start(self.drescher, self.schneidwerk)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.e.profile.geraet_art, 'schneidwerk')
        self.assertEqual(self.e.profile.bauform, 'maehdrescher')
        self.assertFalse(self.e.steering.armed)

    def test_feldwechsel_waehrend_arbeit_vermischt_keine_feldkoordinaten(self):
        other = self.s.save_field(dict(name='Anderes Feld', datum_lat=49., datum_lon=10., boundary=[[0,0],[90,0],[90,90],[0,90]]))
        self.start(self.traktor, self.geraet)
        self.e.coverage.cells = {(1, 2), (3, 4)}
        before = self.e.coverage
        result = self.c.post('/api/fields/' + other['id'] + '/load')
        self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(self.e.field['id'], self.f['id'])
        self.assertEqual(self.e.job['field_id'], self.f['id'])
        self.assertIs(self.e.coverage, before)
        self.assertEqual(self.e.coverage.cells, {(1, 2), (3, 4)})

    def test_erneutes_laden_des_aktiven_feldes_loescht_keine_aufzeichnung(self):
        self.start(self.traktor, self.geraet)
        self.e.coverage.cells = {(1, 2), (3, 4)}
        result = self.c.post('/api/fields/' + self.f['id'] + '/load')
        self.assertIn(result.status_code, (200, 400))
        self.assertEqual(self.e.coverage.cells, {(1, 2), (3, 4)})
