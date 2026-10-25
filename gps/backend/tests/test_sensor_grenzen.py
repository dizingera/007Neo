"""Unabhängige Gegenproben: Motor-, Empfangs- und IMU-Grenzen ohne Hardware."""
import math
import tempfile
import unittest
from unittest.mock import Mock, patch
from agripilot.actuators import PhidgetOutput, SteerContext
from agripilot.config import Config, PhidgetConfig, SteeringConfig
from agripilot.gnss import GnssSource
from agripilot.guidance import GuidanceState, HeadingFilter
from agripilot.imu import ImuSource
from agripilot.nmea import Fix, build_gga
from agripilot.steering import SteeringController
from agripilot.actuators import NullOutput


def satz(body):
    crc = 0
    for c in body:
        crc ^= ord(c)
    return f"${body}*{crc:02X}"

RMC = 'GPRMC,120100,A,4800.000,N,01100.000,E,4,90,251026,,,A'
VTG = 'GPVTG,90,T,,M,0,N,7.2,K'


class MotorGrenzen(unittest.TestCase):
    def motor(self, mode='was'):
        a = PhidgetOutput(PhidgetConfig(feedback=mode, gain_p=.1, gain_i=.1,
                                       gain_d=.01, max_duty=.55))
        a.motor = Mock(spec=['setTargetVelocity', 'resetFailsafe'])
        a.ready = True
        a._status_ok = a.status = 'bereit'
        a.measured_angle = Mock(return_value=0.)
        a.command(True, 5., SteerContext(speed_ms=2.))
        a._step(.02)
        self.assertGreater(a.feedback.duty, 0.)
        return a

    def test_invalid_wheel_feedback_stops_in_the_first_motor_step(self):
        for mode in ('encoder', 'was'):
            for wert in (None, math.nan, math.inf, -math.inf):
                with self.subTest(mode=mode, wert=wert):
                    a = self.motor(mode)
                    a.measured_angle.return_value = wert
                    a._step(.02)
                    self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)
                    self.assertEqual(a.feedback.duty, 0.)
                    self.assertEqual(a._integral, 0.)
                    self.assertEqual(a._last_error, 0.)

    def test_not_ready_never_drives_even_with_valid_feedback(self):
        a = self.motor()
        a.ready = False
        a._step(.02)
        self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)

    def test_not_ready_also_blocks_an_active_workshop_impulse(self):
        a = self.motor()
        a._engaged = False
        a._test_bis = 1e20
        a._test_duty = .5
        a.ready = False
        a._step(.02)
        self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)

    def test_valid_feedback_can_recover_after_a_fault(self):
        a = self.motor()
        a.measured_angle.return_value = math.nan
        a._step(.02)
        a.measured_angle.return_value = 0.
        for _ in range(2):
            a.command(True, 5., SteerContext(speed_ms=2.))
            a._step(.02)
        self.assertTrue(a.ready)
        self.assertGreater(a.feedback.duty, 0.)


class AusgeschalteteSensorAnzeige(unittest.TestCase):
    def ausgang(self, modus, wert):
        a = PhidgetOutput(PhidgetConfig(feedback=modus))
        a.motor = Mock(spec=['setTargetVelocity', 'resetFailsafe'])
        a.encoder = Mock()  # Auch die separat gelesene Werkstattanzeige prüfen.
        a.ready = True
        a._status_ok = a.status = 'bereit'
        a.measured_angle = Mock(return_value=wert)
        a.command(False, 0., SteerContext())
        return a

    def test_off_feedback_and_controller_status_are_valid_json_for_all_six_cases(self):
        import json
        from starlette.responses import JSONResponse
        for modus in ('was', 'encoder'):
            for wert in (math.nan, math.inf, -math.inf):
                with self.subTest(modus=modus, wert=wert):
                    a = self.ausgang(modus, wert)
                    a.encoder = None  # Feedback-JSON unabhängig von der Werkstattanzeige.
                    a._step(.02)
                    c = SteeringController(SteeringConfig(enabled=True), a)
                    status = c.status()
                    JSONResponse(status)
                    json.dumps(status, allow_nan=False)
                    self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)
                    self.assertEqual(a.feedback.duty, 0.)
                    self.assertIsNone(a.feedback.wheel_angle_deg)
                    self.assertIsNone(status['wheel_angle_deg'])

    def test_direct_output_status_is_valid_before_any_motor_step(self):
        from starlette.responses import JSONResponse
        for modus in ('was', 'encoder'):
            for wert in (math.nan, math.inf, -math.inf):
                with self.subTest(modus=modus, wert=wert):
                    a = self.ausgang(modus, wert)
                    status = a.status_dict()
                    JSONResponse(status)
                    self.assertIsNone(status['radwinkel'])

    def test_valid_off_angle_is_preserved_in_feedback_and_status(self):
        from starlette.responses import JSONResponse
        for modus in ('was', 'encoder'):
            for wert in (0., 12.25, -12.25):
                with self.subTest(modus=modus, wert=wert):
                    a = self.ausgang(modus, wert)
                    a._step(.02)
                    status = SteeringController(SteeringConfig(enabled=True), a).status()
                    JSONResponse(status)
                    self.assertEqual(status['wheel_angle_deg'], wert)
                    self.assertEqual(status['output']['radwinkel'], wert)


class EmpfangGrenzen(unittest.IsolatedAsyncioTestCase):
    async def senden(self, quelle, t, body):
        with patch('agripilot.gnss.time.time', return_value=t):
            await quelle.handle_line(satz(body) if not body.startswith('$') else body)

    async def test_expired_quality_and_heading_do_not_allow_rtk_steering(self):
        daten = []
        q = GnssSource(daten.append)
        await self.senden(q, 1000., build_gga(48, 11, quality=4))
        await self.senden(q, 1000., 'GPHDT,0,T')
        await self.senden(q, 1004., RMC)
        f = daten[-1]
        self.assertLess(f.rank, 4)
        self.assertIsNone(f.heading_deg)
        out = NullOutput()
        out.ready = True
        c = SteeringController(SteeringConfig(enabled=True, require_rtk=True), out)
        c.arm()
        self.assertFalse(c.update(GuidanceState(active=True), f, now=1004.).engaged)
        self.assertEqual(HeadingFilter().update(f.course_deg, f.heading_deg, f.speed_ms), 90.)

    async def test_only_heading_and_motion_never_publish_an_old_position(self):
        daten = []
        q = GnssSource(daten.append)
        await self.senden(q, 1000., build_gga(48, 11, quality=4))
        for t in (1001., 1004., 1060.):
            await self.senden(q, t, 'GPHDT,45,T')
            await self.senden(q, t, VTG)
        self.assertEqual(len(daten), 1)
        self.assertEqual(daten[0].received_at, 1000.)

    async def test_motion_expiration_is_independent_of_fresh_gga_and_hdt(self):
        daten = []
        q = GnssSource(daten.append)
        await self.senden(q, 1000., VTG)
        await self.senden(q, 1004., 'GPHDT,45,T')
        await self.senden(q, 1004., build_gga(48, 11, quality=4))
        self.assertEqual(daten[-1].rank, 4)
        self.assertEqual(daten[-1].speed_ms, 0.)
        self.assertIsNone(daten[-1].course_deg)
        self.assertEqual(daten[-1].heading_deg, 45.)

    async def test_healthy_one_hz_receiver_and_rmc_only_still_work(self):
        daten = []
        q = GnssSource(daten.append)
        for t in (1000., 1001., 1002.):
            await self.senden(q, t, VTG)
            await self.senden(q, t, 'GPHDT,45,T')
            await self.senden(q, t, build_gga(48, 11, quality=4))
            await self.senden(q, t + .1, RMC)
            self.assertEqual(daten[-1].rank, 4)
            self.assertEqual(daten[-1].heading_deg, 45.)
            self.assertGreater(daten[-1].speed_ms, 0.)
        r = GnssSource(daten.append)
        await self.senden(r, 1010., RMC)
        self.assertTrue(daten[-1].valid)
        self.assertEqual(daten[-1].rank, 1)
        self.assertEqual(Fix(lat=48, lon=11, fix_quality=4).rank, 4)

    async def test_blank_course_does_not_get_the_new_speed_timestamp(self):
        daten = []
        q = GnssSource(daten.append)
        await self.senden(q, 1000., VTG)
        await self.senden(q, 1002., RMC.replace(',4,90,', ',4,,'))
        await self.senden(q, 1004., build_gga(48, 11, quality=4))
        self.assertIsNone(daten[-1].course_deg)
        self.assertGreater(daten[-1].speed_ms, 0.)

    async def test_quality_survives_normal_two_second_gga_interval(self):
        daten = []
        q = GnssSource(daten.append)
        await self.senden(q, 1000., build_gga(48, 11, quality=4))
        await self.senden(q, 1002., RMC)
        self.assertEqual(daten[-1].rank, 4)


class ImuGrenzen(unittest.TestCase):
    def test_level_is_idempotent_without_another_sample(self):
        s = ImuSource()
        s._publish(3., 1., None, 0.)
        a = s.level_here()
        self.assertEqual(s.level_here(), a)
        self.assertEqual(s.attitude.roll_deg, 0.)
        self.assertEqual(s.attitude.pitch_deg, 0.)

    def test_stale_and_nonfinite_level_do_not_change_offsets(self):
        for bad in ('stale', 'nan', 'inf'):
            s = ImuSource()
            s._publish(3., 1., None, 0.)
            if bad == 'stale':
                s.attitude.received_at = 1.
            else:
                s.attitude.roll_deg = float(bad)
            with self.assertRaises((RuntimeError, ValueError)):
                s.level_here()
            self.assertEqual((s.roll_offset, s.pitch_offset), (0., 0.))

    def test_invalid_imu_sample_is_not_published_as_fresh(self):
        s = ImuSource()
        s._publish(3., 1., None, 0.)
        for bad in (math.nan, math.inf, -math.inf):
            s._publish(3., 1., None, bad)
            self.assertFalse(s.attitude.fresh)
        s._publish(3., 1., None, 0.)
        self.assertTrue(s.attitude.fresh)

    def test_direct_and_replay_attitudes_reject_invalid_values_as_fresh(self):
        from agripilot.imu import Attitude
        import time
        for feld in ('roll_deg', 'pitch_deg', 'yaw_rate_deg_s', 'yaw_deg'):
            for wert in (math.nan, math.inf, -math.inf):
                with self.subTest(feld=feld, wert=wert):
                    lage = Attitude(received_at=time.time(), **{feld: wert})
                    self.assertFalse(lage.fresh)
                    import json
                    json.dumps(lage.to_dict(), allow_nan=False)
        self.assertFalse(Attitude(received_at=time.time() + 5).fresh)

    def test_heading_ignores_bad_gyro_and_recovers_poisoned_state(self):
        f = HeadingFilter()
        f.update(0., None, 2.)
        self.assertTrue(math.isfinite(f.update(0., None, 2., math.nan, .1)))
        f.value = math.nan
        self.assertEqual(f.update(90., None, 2., 0., .1), 90.)

    def test_level_api_returns_400_and_never_persists_failure(self):
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        with tempfile.TemporaryDirectory() as d:
            cfg = Config()
            cfg.server.data_dir = d
            app = create_app(cfg)
            app.state.app.imu = ImuSource()
            store = app.state.app.store
            try:
                with patch.object(store, 'set_setting', wraps=store.set_setting) as write:
                    with TestClient(app) as c:
                        # Lifespan may install the configured source; replace it explicitly.
                        app.state.app.imu = ImuSource()
                        response = c.post('/api/imu/level')
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(any(c.args[0] == 'imu_offsets' for c in write.call_args_list))
            finally:
                if not getattr(store, 'db', None) is None:
                    try: store.close()
                    except Exception: pass

    def test_level_api_success_is_repeatable_and_persists_valid_offsets(self):
        from fastapi.testclient import TestClient
        from agripilot.server import create_app
        with tempfile.TemporaryDirectory() as d:
            cfg = Config()
            cfg.server.data_dir = d
            app = create_app(cfg)
            with TestClient(app) as c:
                source = ImuSource()
                source._publish(3., 1., None, 0.)
                app.state.app.imu = source
                first = c.post('/api/imu/level')
                second = c.post('/api/imu/level')
                self.assertEqual(first.status_code, 200)
                self.assertEqual(second.json(), first.json())
                self.assertEqual(app.state.app.store.get_setting('imu_offsets'),
                                 {'roll_offset': 3., 'pitch_offset': 1.})
