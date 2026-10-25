import math
import unittest
from agripilot.nmea import Fix, NmeaParser


def satz(body):
    crc = 0
    for c in body: crc ^= ord(c)
    return '$' + body + '*' + format(crc, '02X')


class BeschaedigteGpsDaten(unittest.TestCase):
    def test_bad_gga_is_rejected_and_next_valid_sentence_recovers(self):
        for lat, ns, lon, ew, quality in (
                ('nan','N','01100.000','E','4'), ('inf','N','01100.000','E','4'),
                ('4860.000','N','01100.000','E','4'), ('9100.000','N','01100.000','E','4'),
                ('4800.000','N','18100.000','E','4'), ('4800.000','X','01100.000','E','4'),
                ('4800.000','E','01100.000','N','4'),
                ('4800.000','N','01100.000','E','inf')):
            with self.subTest(lat=lat, ns=ns, quality=quality):
                p = NmeaParser()
                self.assertIsNone(p.feed(satz(f'GPGGA,120000,{lat},{ns},{lon},{ew},{quality},12,0.7,500,M,0,M,,')))
                f = p.feed(satz('GPGGA,120001,4800.000,N,01100.000,E,4,12,0.7,500,M,0,M,,'))
                self.assertIsNotNone(f)
                self.assertAlmostEqual(f.lat,48)

    def test_bad_rmc_does_not_publish_old_position_as_fresh(self):
        p = NmeaParser()
        p.feed(satz('GPGGA,120000,4800.000,N,01100.000,E,4,12,0.7,500,M,0,M,,'))
        self.assertIsNone(p.feed(satz('GPRMC,120001,A,garbage,N,01100.000,E,5,0,251026,,,A')))

    def test_nonfinite_optional_values_do_not_poison_speed_or_heading(self):
        p = NmeaParser()
        p.feed(satz('GPVTG,0,T,,M,0,N,7.2,K'))
        p.feed(satz('GPHDT,90,T'))
        p.feed(satz('GPVTG,nan,T,,M,0,N,inf,K'))
        p.feed(satz('GPHDT,nan,T'))
        self.assertTrue(math.isfinite(p.fix.speed_ms))
        self.assertTrue(math.isfinite(p.fix.course_deg))
        self.assertTrue(math.isfinite(p.fix.heading_deg))

    def test_rejected_fix_can_still_be_displayed_as_valid_json(self):
        import json
        f = Fix(lat=math.nan, lon=11.0, speed_ms=math.inf, fix_quality=4)
        daten = f.to_dict()
        json.dumps(daten, allow_nan=False)
        self.assertIsNone(daten['lat'])
        self.assertIsNone(daten['speed_ms'])

    def test_fix_rejects_nonfinite_and_out_of_range_coordinates(self):
        for lat,lon in ((math.nan,11),(48,math.inf),(91,11),(48,-181)):
            self.assertFalse(Fix(lat=lat,lon=lon,fix_quality=4).valid)


class UngueltigePositionInDerEngine(unittest.TestCase):
    def test_invalid_coordinates_stop_steering_before_projecting_or_marking(self):
        import tempfile
        import time
        from agripilot.actuators import NullOutput
        from agripilot.config import Config
        from agripilot.engine import Engine
        from agripilot.guidance import GuidanceState
        from agripilot.steering import SteeringController
        from agripilot.storage import Storage
        for lat, lon in ((math.nan, 11), (48, math.inf), (91, 11), (48, -181)):
            with self.subTest(lat=lat, lon=lon), tempfile.TemporaryDirectory() as d:
                store = Storage(d + '/x.db')
                try:
                    cfg = Config()
                    cfg.steering.enabled = True
                    e = Engine(cfg, store)
                    out = NullOutput()
                    out.ready = True
                    e.steering = SteeringController(cfg.steering, out)
                    e.steering.arm()
                    valid = Fix(lat=48, lon=11, speed_ms=2, fix_quality=4, received_at=time.time())
                    e.steering.update(GuidanceState(active=True), valid)
                    self.assertTrue(e.steering.command.engaged)
                    invalid = Fix(lat=lat, lon=lon, speed_ms=2, fix_quality=4, received_at=time.time())
                    e.on_fix(invalid)
                    self.assertFalse(e.steering.command.engaged)
                    self.assertIsNone(e.plane)
                    self.assertIsNone(e.tool_position)
                    self.assertEqual(e.coverage.area_m2, 0)
                finally:
                    store.close()
