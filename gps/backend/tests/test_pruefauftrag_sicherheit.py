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
    a.motor = mock.Mock()
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
