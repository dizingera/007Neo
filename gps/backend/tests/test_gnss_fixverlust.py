import unittest
from agripilot.gnss import GnssSource
from agripilot.nmea import build_gga


def satz(body):
    crc = 0
    for c in body: crc ^= ord(c)
    return '$' + body + '*' + format(crc, '02X')


class Empfangsweg(unittest.IsolatedAsyncioTestCase):
    async def test_gga_fix_loss_is_delivered_immediately(self):
        daten=[]
        source=GnssSource(daten.append)
        await source.handle_line(build_gga(48,11,500,4))
        await source.handle_line(build_gga(48,11,500,0))
        self.assertEqual(len(daten),2)
        self.assertTrue(daten[0].valid)
        self.assertFalse(daten[1].valid)
        await source.handle_line(build_gga(48,11,500,4))
        self.assertTrue(daten[-1].valid)

    async def test_rmc_void_is_delivered_as_invalid(self):
        daten=[]
        source=GnssSource(daten.append)
        await source.handle_line(build_gga(48,11,500,4))
        await source.handle_line(satz('GPRMC,120001,V,,,,,,,251026,,,A'))
        self.assertEqual(len(daten),2)
        self.assertFalse(daten[-1].valid)

    async def test_rmc_recovery_does_not_reuse_rtk_quality_from_before_loss(self):
        daten=[]
        source=GnssSource(daten.append)
        await source.handle_line(build_gga(48,11,500,4))
        await source.handle_line(satz('GPRMC,120001,V,,,,,,,251026,,,A'))
        await source.handle_line(satz('GPRMC,120002,A,4800.000,N,01100.000,E,5,0,251026,,,A'))
        self.assertTrue(daten[-1].valid)
        self.assertLess(daten[-1].rank,4)
        await source.handle_line(build_gga(48,11,500,4))
        self.assertEqual(daten[-1].rank,4)

    async def test_following_sentences_do_not_mutate_published_fix(self):
        daten=[]
        source=GnssSource(daten.append)
        await source.handle_line(build_gga(48,11,500,4))
        vor=daten[0].to_dict()
        await source.handle_line(satz('GPVTG,90,T,,M,0,N,7.2,K'))
        await source.handle_line(build_gga(49,12,600,4))
        self.assertEqual(daten[0].to_dict(),vor)
        self.assertIsNot(daten[0],daten[-1])


class FixverlustInDerArbeit(unittest.IsolatedAsyncioTestCase):
    async def test_actual_receiver_stops_steering_sections_and_leaves_gap_unpainted(self):
        await self._pruefe_ausfall(False)

    async def test_receiver_silence_also_leaves_gap_unpainted(self):
        await self._pruefe_ausfall(True)

    async def _pruefe_ausfall(self, stille):
        import tempfile
        from unittest.mock import Mock
        from agripilot.config import Config
        from agripilot.engine import Engine
        from agripilot.geo import LocalPlane
        from agripilot.storage import Storage
        from agripilot.steering import SteeringController
        from agripilot.actuators import NullOutput
        with tempfile.TemporaryDirectory() as d:
            store=Storage(d+'/x.db')
            try:
                cfg=Config()
                cfg.steering.enabled=True
                cfg.gnss.kalman_enabled=False
                e=Engine(cfg,store)
                out=NullOutput()
                out.ready=True
                e.steering=SteeringController(cfg.steering,out)
                field=store.save_field({'name':'Test','datum_lat':48,'datum_lon':11})
                e.load_field(field['id'])
                line=store.save_line({'field_id':field['id'],'name':'AB','mode':'ab',
                                     'points':[(0,0),(0,100)],'spacing_m':3})
                e.load_line(line['id'])
                source=GnssSource(e.on_fix)
                plane=LocalPlane(48,11)
                async def position(y,quality=4):
                    lat,lon=plane.to_wgs(0,y)
                    await source.handle_line(build_gga(lat,lon,500,quality))
                await source.handle_line(satz('GPVTG,0,T,,M,0,N,7.2,K'))
                await position(0)
                e.start_job('Test')
                e.teilbreiten_modus='manuell'
                e.steering.arm()
                await position(1)
                self.assertTrue(e.steering.command.engaged)
                vorher=e.coverage.area_m2
                self.assertGreater(vorher,0)
                control=Mock()
                e.teilbreiten=control
                e.turn=object()
                if stille:
                    e.tick(e.fix.received_at + 1.3)
                else:
                    await position(2,0)
                self.assertFalse(e.steering.command.engaged)
                self.assertIsNone(e.implement_position)
                self.assertIsNone(e.turn)
                control.wunsch.assert_called_with(0,len(e.sections))
                e.teilbreiten=None
                await position(3)
                self.assertEqual(e.coverage.area_m2,vorher)
                await position(4)
                self.assertGreater(e.coverage.area_m2,vorher)
            finally:
                store.close()
