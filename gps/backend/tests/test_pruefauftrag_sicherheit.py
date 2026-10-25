"""Regressionen aus der Gesamtprüfung: echte Regelschritte ohne Hardware."""
import asyncio
import threading
import time
import unittest
from unittest import mock

from agripilot.actuators import PhidgetOutput, SteerContext, OutputFeedback
from agripilot.config import PhidgetConfig, SteeringConfig
from agripilot.steering import SteeringController


def ausgang(**werte):
    cfg = dict(feedback="encoder", gain_p=.1, gain_i=0., gain_d=0., min_duty=0.)
    cfg.update(werte)
    a = PhidgetOutput(PhidgetConfig(**cfg))
    a.motor = mock.Mock(spec=["setTargetVelocity", "resetFailsafe"])
    a.ready = True
    a._status_ok = a.status = "bereit"
    a.measured_angle = mock.Mock(return_value=0.)
    return a


class Abschalten(unittest.TestCase):
    def test_abschalten_stoppt_sofort_und_verwirft_begonnenen_regeltakt(self):
        a = ausgang()
        s = SteeringController(SteeringConfig(enabled=True), a)
        s.armed = True
        a.command(True, 5., SteerContext(speed_ms=2.))
        a._step(.02)
        self.assertGreater(a.motor.setTargetVelocity.call_args.args[0], 0.)
        begonnen, weiter = threading.Event(), threading.Event()
        fehler = []

        def regelfehler(messung):
            begonnen.set()
            if not weiter.wait(2.):
                raise RuntimeError("Testbarriere nicht freigegeben")
            return 5.

        def schritt():
            try:
                a._step(.02)
            except BaseException as exc:
                fehler.append(exc)

        a._error = regelfehler
        faden = threading.Thread(target=schritt)
        faden.start()
        try:
            self.assertTrue(begonnen.wait(2.))
            s.disarm()
            self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)
        finally:
            weiter.set()
            faden.join(2.)
        self.assertFalse(faden.is_alive())
        self.assertFalse(fehler)
        self.assertFalse(s.armed)
        self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)


class WerkstattAbbruch(unittest.IsolatedAsyncioTestCase):
    async def test_aus_beendet_impuls_und_laesst_keine_leistung_nach(self):
        a = ausgang()
        s = SteeringController(SteeringConfig(enabled=True), a)
        task = asyncio.create_task(a.motortest(1, .25, 1.))
        await asyncio.sleep(0)
        a._step(.02)
        self.assertGreater(a.motor.setTargetVelocity.call_args.args[0], 0.)
        try:
            s.disarm()
            a._step(.02)
            self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)
            with self.assertRaisesRegex(RuntimeError, "abgebrochen"):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_aus_beendet_feinabstimmung_und_stellt_werte_zurueck(self):
        a = ausgang()
        s = SteeringController(SteeringConfig(enabled=True), a)
        start = (a.config.gain_p, a.config.max_duty)
        task = asyncio.create_task(a.feinabstimmen())
        await asyncio.sleep(0)
        try:
            a.config.gain_p, a.config.max_duty = 2., .9
            s.disarm()
            a._step(.02)
            self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)
            with self.assertRaisesRegex(RuntimeError, "abgebrochen"):
                await task
            self.assertEqual((a.config.gain_p, a.config.max_duty), start)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_nur_ein_werkstattlauf_und_keine_automatik_daneben(self):
        a = ausgang()
        s = SteeringController(SteeringConfig(enabled=True), a)
        task = asyncio.create_task(a.motortest(1, .25, 1.))
        await asyncio.sleep(0)
        try:
            self.assertNotEqual(s.arm(), "scharf")
            self.assertFalse(s.armed)
            with self.assertRaisesRegex(RuntimeError, "Werkstatt"):
                await a.motortest(-1, .25, .1)
        finally:
            s.disarm()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class GierratenAusfall(unittest.TestCase):
    def test_fehlende_oder_nicht_endliche_gierrate_stoppt_integral_und_d_anteil(self):
        for rate in (None, float("nan"), float("inf")):
            with self.subTest(rate=rate):
                a = ausgang(feedback="yaw_rate", gain_i=.4, gain_d=.2)
                a._integral, a._last_error = .25, 8.
                a.command(True, 10., SteerContext(speed_ms=2., yaw_rate_deg_s=rate))
                a._step(.02)
                self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)
                self.assertEqual(a._integral, 0.)
                self.assertEqual(a._last_error, 0.)
                self.assertFalse(a.ready)
                self.assertIn("Gierrate", a.status)
                a.command(True, 5., SteerContext(speed_ms=2., yaw_rate_deg_s=0.))
                a._step(.02)
                self.assertTrue(a.ready)
                self.assertGreater(a.motor.setTargetVelocity.call_args.args[0], 0.)


class WerkstattSensorAusfall(unittest.IsolatedAsyncioTestCase):
    async def test_sensorverlust_bricht_ab_und_verwirft_angehobene_reglerwerte(self):
        a = ausgang()
        start = (a.config.gain_p, a.config.max_duty)
        task = asyncio.create_task(a.feinabstimmen())
        await asyncio.sleep(0)
        try:
            a._step(.02)
            a.config.gain_p, a.config.max_duty = 2., .9
            a.measured_angle.return_value = None
            a._step(.02)
            self.assertFalse(a.ready)
            self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)
            with self.assertRaisesRegex(RuntimeError, "Rückmeldung"):
                await asyncio.wait_for(task, .5)
            self.assertEqual((a.config.gain_p, a.config.max_duty), start)
            a._step(.02)
            self.assertEqual(a.motor.setTargetVelocity.call_args.args[0], 0.)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
