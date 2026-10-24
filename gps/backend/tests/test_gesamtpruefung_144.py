"""Öffentliche Gegenfälle der Gesamtprüfung, ohne Quellen und echte Hardware."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from starlette.websockets import WebSocketDisconnect
from test_tablet_lenkung import geraet
from agripilot.sync import SyncClient
from agripilot.config import Config
from agripilot.storage import Storage
from agripilot.engine import Engine
from agripilot.nmea import Fix
from agripilot.steering import SteeringController
from agripilot.actuators import NullOutput

class Gesamtpruefung144(unittest.TestCase):
    def test_waehrend_des_abgleichs_gespeichertes_feld_erreicht_master_naechste_runde(self):
        async def pruefen():
            with tempfile.TemporaryDirectory() as d:
                with geraet(d+'/m','master','m') as m, geraet(d+'/a','client','a') as a:
                    app=a.app.state.app
                    inner=httpx.ASGITransport(app=m.app)
                    sync=SyncClient(app.config,app.store,app.engine)
                    erstellt=[]
                    class Transport(httpx.AsyncBaseTransport):
                        async def handle_async_request(selbst,request):
                            if not erstellt:
                                erstellt.append(app.store.save_field({'name':'Während Abgleich','datum_lat':48,'datum_lon':11}))
                                await asyncio.sleep(.02)
                            return await inner.handle_async_request(request)
                    original=httpx.AsyncClient
                    with patch('httpx.AsyncClient',side_effect=lambda **kw:original(transport=Transport(),**kw)):
                        self.assertTrue((await sync.sync_once())['ok'])
                        # Auch ein neuer Client nach einem Neustart verwendet den lokalen Sendecursor.
                        sync=SyncClient(app.config,app.store,app.engine)
                        self.assertTrue((await sync.sync_once())['ok'])
                    self.assertEqual([f['id'] for f in m.app.state.app.store.list_fields()],[erstellt[0]['id']])
        asyncio.run(pruefen())

    def test_ungueltiger_fix_stoppt_lenkung_und_wende_sofort(self):
        with tempfile.TemporaryDirectory() as d:
            s=Storage(d+'/x.db')
            try:
                cfg=Config();cfg.steering.enabled=True
                e=Engine(cfg,s);out=NullOutput();out.ready=True
                e.steering=SteeringController(cfg.steering,out)
                f=s.save_field({'name':'Feld','datum_lat':48,'datum_lon':11});e.load_field(f['id'])
                l=s.save_line({'field_id':f['id'],'name':'AB','mode':'ab','points':[(0,0),(0,100)],'spacing_m':3});e.load_line(l['id'])
                now=time.time();e.steering.arm()
                e.on_fix(Fix(lat=48,lon=11,course_deg=0,speed_ms=2,fix_quality=4,received_at=now))
                self.assertTrue(e.steering.command.engaged)
                e.turn=object()
                e.on_fix(Fix(lat=48,lon=11,course_deg=0,speed_ms=2,fix_quality=0,received_at=now+.1))
                # Ab 1.5.80 pausiert ein ungültiger Fix nur: sofort kein Lenken, scharf bleibt sie.
                self.assertFalse(e.steering.command.engaged);self.assertTrue(e.steering.armed)
                self.assertIn('ungültig',e.steering.command.reason)
                self.assertIsNone(e.turn);self.assertEqual(e.state()['fix']['fix_quality'],0)
                self.assertIsNone(e.implement_position)
                e.on_fix(Fix(lat=48,lon=11,course_deg=0,speed_ms=2,fix_quality=4,received_at=now+.2))
                self.assertTrue(e.steering.armed)
            finally:s.close()

    def test_online_update_prueft_fahrt_und_lenkung_nach_download_erneut(self):
        for art in ('fahrt','lenkung'):
            with self.subTest(art=art),tempfile.TemporaryDirectory() as d:
                with geraet(d,'client','a') as c:
                    a=c.app.state.app
                    a.engine.fix=Fix(lat=48,lon=11,fix_quality=4,speed_ms=0,received_at=time.time())
                    a.online_info=object()
                    a.steering.config.enabled=True
                    a.steering.output.ready=True
                    def laden(*args):
                        if art=='fahrt':a.engine.fix.speed_ms=2
                        else:a.steering.arm()
                        return Path(d)/'nicht-gestartet.exe'
                    with patch('agripilot.server.update_module.als_programm',return_value=True), \
                         patch.object(a,'online_pruefen',return_value={'fehler':'','verfuegbar':True,'neu':'9.9.9'}), \
                         patch('agripilot.server.onlineupdate_module.herunterladen',side_effect=laden), \
                         patch('agripilot.server.exe_module.setup_starten') as start, \
                         patch.object(a.engine,'suspend_job') as sichern:
                        r=c.post('/api/update/online')
                        self.assertEqual(r.status_code,409,r.text);start.assert_not_called();sichern.assert_not_called()

    def test_aussenverbindung_kann_keine_livedaten_ueber_websocket_lesen(self):
        with tempfile.TemporaryDirectory() as d:
            with geraet(d,'master','buero') as c:
                for name in ('cf-ray','cf-connecting-ip','x-forwarded-for'):
                    with self.subTest(kopf=name):
                        kopf={name:'lokaler-prueffall'}
                        self.assertEqual(c.get('/api/state',headers=kopf).status_code,403)
                        with self.assertRaises(WebSocketDisconnect) as fehler:
                            with c.websocket_connect('/ws',headers=kopf) as w:w.receive_json()
                        self.assertEqual(fehler.exception.code,1008)
                with c.websocket_connect('/ws') as w:self.assertEqual(w.receive_json()['type'],'init')
