"""Teilbreiten über einen echten lokalen UDP-Empfänger statt angenommener Hardware."""
import asyncio
import json
import unittest
from agripilot.teilbreiten import TeilbreitenAusgang
from agripilot.config import TeilbreitenConfig

class Platine(asyncio.DatagramProtocol):
    def __init__(self):self.pakete=[];self.antworten=True
    def connection_made(self,transport):
        self.transport=transport;self.geschlossen=asyncio.get_running_loop().create_future()
    def connection_lost(self,exc):
        if not self.geschlossen.done():self.geschlossen.set_result(None)
    def datagram_received(self,data,addr):
        p=json.loads(data);self.pakete.append(p)
        if self.antworten:
            self.transport.sendto(json.dumps({"protokoll":p["protokoll"],"sitzung":p["sitzung"],
                "seq":p["seq"],"maske":p["maske"]}).encode(),addr)

class TeilbreitenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.platine=Platine();loop=asyncio.get_running_loop()
        self.transport,_=await loop.create_datagram_endpoint(lambda:self.platine,local_addr=('127.0.0.1',0))
        port=self.transport.get_extra_info('sockname')[1]
        self.ausgang=TeilbreitenAusgang(TeilbreitenConfig(output='udp',host='127.0.0.1',port=port))
        await self.ausgang.start()
    async def asyncTearDown(self):
        await self.ausgang.stop();self.transport.abort();await asyncio.wait_for(self.platine.geschlossen,1.)
    async def test_soll_wird_gesendet_aber_erst_rueckmeldung_gilt(self):
        self.ausgang.wunsch(5,3)
        self.assertEqual(self.ausgang.bestaetigt(),0)
        await asyncio.sleep(.16)
        self.assertEqual(self.ausgang.bestaetigt(),5)
        p=self.platine.pakete[-1]
        self.assertEqual((p['maske'],p['anzahl'],p['watchdog_ms']),(5,3,500))
    async def test_veralteter_wunsch_und_fehlerhafte_rueckmeldung_schalten_nicht_ein(self):
        self.ausgang.wunsch(7,3);await asyncio.sleep(.16)
        self.assertEqual(self.ausgang.bestaetigt(),7)
        self.platine.antworten=False
        await asyncio.sleep(.6)
        self.assertEqual(self.ausgang.bestaetigt(),0)
        self.assertEqual(self.platine.pakete[-1]['maske'],0)
    async def test_abgeschaltete_teilbreite_wird_sofort_aus_bestaetigung_entfernt(self):
        self.ausgang.wunsch(7,3);await asyncio.sleep(.16)
        self.ausgang.wunsch(1,3)
        self.assertEqual(self.ausgang.bestaetigt() & 6,0)
        await asyncio.sleep(.16)
        self.assertEqual(self.ausgang.bestaetigt(),1)
    async def test_stop_sendet_aus(self):
        self.ausgang.wunsch(7,3);await asyncio.sleep(.16)
        await self.ausgang.stop();await asyncio.sleep(.03)
        self.assertEqual(self.platine.pakete[-1]['maske'],0)
    async def test_ungueltige_anzahl_wird_abgewiesen(self):
        for anzahl in (0,33):
            with self.assertRaises(ValueError):self.ausgang.wunsch(1,anzahl)
class TeilbreitenEngineTests(unittest.TestCase):
    def test_ohne_hardwarebestaetigung_wird_keine_flaeche_als_behandelt_markiert(self):
        import tempfile,time
        from agripilot.config import Config
        from agripilot.engine import Engine
        from agripilot.storage import Storage
        from agripilot.nmea import Fix
        with tempfile.TemporaryDirectory() as tmp:
            config=Config();config.teilbreiten.output="udp"
            store=Storage(tmp+"/t.db")
            try:
                e=Engine(config,store)
                feld=store.save_field({"name":"Test","datum_lat":48,"datum_lon":11,"boundary":[]})
                e.load_field(feld["id"])
                now=time.time()
                e.on_fix(Fix(lat=48,lon=11,course_deg=0,speed_ms=2,fix_quality=4,received_at=now))
                e.start_job("Spritzen")
                for i in range(1,15):
                    e.on_fix(Fix(lat=48+i*.2/111200,lon=11,course_deg=0,speed_ms=2,
                        fix_quality=4,received_at=now+i*.1))
                self.assertEqual(e.coverage.area_m2,0)
                self.assertFalse(e.state()["sections"][0]["enabled"])
            finally:store.close()
