"""Unabhängig gefundene Integrationsgrenzen dauerhaft prüfen."""
import asyncio
from contextlib import contextmanager
import json
import tempfile
import time
import unittest
from agripilot.config import Config,TeilbreitenConfig
from agripilot.engine import Engine
from agripilot.headland import HeadlandSettings
from agripilot.nmea import Fix
from agripilot.storage import Storage
from agripilot.teilbreiten import TeilbreitenAusgang
from test_teilbreiten import Platine

@contextmanager
def motor():
    with tempfile.TemporaryDirectory() as tmp:
        config=Config();store=Storage(tmp+'/t.db')
        try:yield Engine(config,store)
        finally:store.close()

class ReviewEngineTests(unittest.TestCase):
    def test_dubins_beruecksichtigt_schwaecheren_gegenanschlag(self):
        with motor() as e:
            e.update_profile({'wheelbase_m':2.8,'max_steer_left_deg':40,'max_steer_right_deg':20,'width_m':6})
            e.on_fix(Fix(lat=48,lon=11,course_deg=0,speed_ms=2,fix_quality=4,received_at=time.time()))
            e.headland=HeadlandSettings(muster='dubins',richtung='links',radius_m=4,nur_im_feld=False)
            stand=e.plan_turn()
            self.assertFalse(stand['fahrbar'])
            self.assertGreater(stand['kleinster_radius_m'],7.6)
    def test_kurze_arbeit_beenden_schaltet_sofort_aus(self):
        with motor() as e:
            e.config.teilbreiten.output='udp';e.teilbreiten=TeilbreitenAusgang(e.config.teilbreiten)
            feld=e.store.save_field({'name':'Probe','datum_lat':48,'datum_lon':11})
            e.load_field(feld['id']);e.on_fix(Fix(lat=48,lon=11,course_deg=0,heading_deg=0,speed_ms=0,fix_quality=4,received_at=time.time()))
            e.start_job('Spritzen');e.teilbreiten.wunsch(1,1);e.stop_job()
            self.assertEqual(e.teilbreiten.status_dict()['angefordert'],0)

    def test_gnss_ausreisser_schaltet_teilbreiten_sofort_ab(self):
        with motor() as e:
            e.config.gnss.kalman_enabled=True;e.config.teilbreiten.output='udp'
            e.teilbreiten=TeilbreitenAusgang(e.config.teilbreiten)
            jetzt=time.time()
            e.on_fix(Fix(lat=48,lon=11,course_deg=0,heading_deg=0,speed_ms=0,fix_quality=4,received_at=jetzt))
            e.teilbreiten.wunsch(1,1)
            e.on_fix(Fix(lat=48.01,lon=11,course_deg=0,heading_deg=0,speed_ms=0,fix_quality=4,received_at=jetzt+.1))
            self.assertFalse(e.positionsfilter.akzeptiert)
            self.assertEqual(e.teilbreiten.status_dict()['angefordert'],0)

class ReviewAckTests(unittest.IsolatedAsyncioTestCase):
    async def test_altes_ack_wird_auch_bei_erneuertem_wunsch_abgewiesen(self):
        platine=Platine();platine.antworten=False;loop=asyncio.get_running_loop()
        transport,_=await loop.create_datagram_endpoint(lambda:platine,local_addr=('127.0.0.1',0))
        ausgang=TeilbreitenAusgang(TeilbreitenConfig(output='udp',host='127.0.0.1',port=transport.get_extra_info('sockname')[1]))
        try:
            await ausgang.start();ausgang.wunsch(7,3);await asyncio.sleep(.12)
            alt=platine.pakete[-1]
            for _ in range(4):ausgang.wunsch(7,3);await asyncio.sleep(.13)
            # Antwort auf den alten Befehl aus dem tatsächlichen UDP-Peer senden.
            adresse=ausgang.transport.get_extra_info('sockname')
            transport.sendto(json.dumps({'protokoll':alt['protokoll'],'sitzung':alt['sitzung'],'seq':alt['seq'],'maske':7}).encode(),adresse)
            await asyncio.sleep(.03)
            self.assertEqual(ausgang.bestaetigt(),0)
        finally:
            await ausgang.stop();transport.abort();await asyncio.wait_for(platine.geschlossen,1)