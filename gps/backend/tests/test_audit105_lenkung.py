"""Konkrete Gegenproben der Lenkungsprüfung 1.5.105, ohne echte Hardware."""
import copy
import asyncio
import math
import sys
import time
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest import mock

from agripilot.actuators import PhidgetPositionOutput, SteerContext
from agripilot.config import PhidgetConfig
from agripilot.guidance import VehicleProfile
from agripilot.imu import ImuSource
from agripilot.lenkautomatik import FELDER, Lenkautomatik


class Positionsplatine:
    def __init__(self):
        self.engaged = False
        self.position = 0.
        self.duty = .1
        self.target = 0.
        self.lesefehler = False
        self.schreibfehler = False
        self.reset_anzahl = 0

    def getPosition(self):
        if self.lesefehler:
            raise RuntimeError("Drehgeber nicht lesbar")
        return self.position

    def getDutyCycle(self):
        return self.duty

    def setTargetPosition(self, wert):
        if self.schreibfehler:
            raise RuntimeError("Sollwinkel nicht geschrieben")
        self.target = wert

    def setEngaged(self, wert):
        self.engaged = wert

    def resetFailsafe(self):
        self.reset_anzahl += 1

    def addPositionOffset(self, wert):
        self.position += wert


def positionsausgang():
    a = PhidgetPositionOutput(PhidgetConfig())
    a.controller = Positionsplatine()
    a.centred = a.ready = True
    a.status = "bereit"
    return a


class PositionsSicherheit(unittest.TestCase):
    def test_rueckmeldung_lesefehler_stoppt_bereits_laufenden_motor(self):
        a = positionsausgang()
        a.command(True, 8., SteerContext())
        self.assertTrue(a.controller.engaged)
        a.controller.lesefehler = True
        a.command(True, 8., SteerContext())
        self.assertFalse(a.controller.engaged)
        self.assertFalse(a.ready)
        self.assertFalse(a.feedback.fresh)

    def test_nicht_endliche_rueckmeldung_darf_keinen_motor_freigeben(self):
        for feld in ("position", "duty"):
            for wert in (float("nan"), float("inf"), -float("inf")):
                with self.subTest(feld=feld, wert=wert):
                    a = positionsausgang()
                    setattr(a.controller, feld, wert)
                    a.command(True, 8., SteerContext())
                    self.assertFalse(a.controller.engaged)
                    self.assertFalse(a.ready)
                    self.assertFalse(a.feedback.fresh)

    def test_sollwert_schreibfehler_laesst_alten_motorbefehl_nicht_weiterlaufen(self):
        a = positionsausgang()
        a.command(True, 8., SteerContext())
        a.controller.schreibfehler = True
        a.command(True, -8., SteerContext())
        self.assertFalse(a.controller.engaged)
        self.assertFalse(a.ready)

    def test_schutz_fordert_neue_freigabe_nach_erkannter_fahrerblockade(self):
        a = positionsausgang()
        a.controller.duty = .95
        a.command(True, 10., SteerContext())
        a._error_since = time.time() - 1.
        a.command(True, 10., SteerContext())
        self.assertTrue(a.feedback.driver_override)
        self.assertFalse(a.controller.engaged)
        a.command(True, 10., SteerContext())
        self.assertFalse(a.controller.engaged)
        self.assertTrue(a.feedback.driver_override)

    def test_fehlererholung_bleibt_im_ersten_gueltigen_takt_stromlos(self):
        a = positionsausgang()
        a.command(True, 8., SteerContext())
        a.controller.lesefehler = True
        a.command(True, 8., SteerContext())
        a.controller.lesefehler = False
        a.command(True, 8., SteerContext())
        self.assertTrue(a.ready)
        self.assertFalse(a.controller.engaged)
        a.command(True, 8., SteerContext())
        self.assertTrue(a.controller.engaged)

    def test_fahrerblockade_bleibt_bis_zum_erneuten_scharfschalten(self):
        a = positionsausgang()
        a.controller.duty = .95
        a.command(True, 10., SteerContext())
        a._error_since = time.time() - 1.
        a.command(True, 10., SteerContext())
        a.controller.duty = 0.
        a.command(False, 0., SteerContext())
        a.command(True, 10., SteerContext())
        self.assertFalse(a.controller.engaged)
        self.assertTrue(a.feedback.driver_override)
        a.on_arm()
        a.command(True, 10., SteerContext())
        self.assertTrue(a.controller.engaged)
        self.assertFalse(a.feedback.driver_override)


class PositionsAufbau(unittest.TestCase):
    def test_fehler_beim_einrichten_stoppt_und_schliesst_geoeffnete_platine(self):
        c = mock.Mock()
        c.getMinCurrentLimit.return_value = .1
        c.getMaxCurrentLimit.return_value = 10.
        c.setCurrentLimit.side_effect = RuntimeError("Stromgrenze nicht geschrieben")
        module = SimpleNamespace(MotorPositionController=lambda: c)
        a = PhidgetPositionOutput(PhidgetConfig())
        with mock.patch('agripilot.actuators.phidget_available', return_value=(True, '')):
            with mock.patch.dict(sys.modules, {'Phidget22.Devices.MotorPositionController': module}):
                asyncio.run(a.start())
        self.assertFalse(a.ready)
        c.setEngaged.assert_called_with(False)
        c.close.assert_called_once()
        self.assertIsNone(a.controller)

    def test_schliessen_wird_auch_bei_fehler_des_ausschaltbefehls_versucht(self):
        a = positionsausgang()
        c = mock.Mock()
        c.setEngaged.side_effect = RuntimeError("Ausschalten fehlgeschlagen")
        a.controller = c
        asyncio.run(a.stop())
        c.close.assert_called_once()
        self.assertIsNone(a.controller)
        self.assertFalse(a.ready)


class CereaRadmitte(unittest.TestCase):
    def test_mittenkorrektur_gilt_auch_fuer_gemerkte_gegenrichtung(self):
        from agripilot.cerearegler import CereaIntegral
        p = VehicleProfile(cerea_i=0., cerea_i2=.05)
        c = CereaIntegral()
        for _ in range(100):
            c.update(.1, .1, 2., True, True, p, 'hin')
        hin = c.summe
        for _ in range(100):
            c.update(-.1, .1, 2., True, True, p, 'her')
        her = c.summe
        c.verschieben(-1.2)
        self.assertAlmostEqual(c.summe, her - 1.2)
        self.assertAlmostEqual(c.update(0., .1, 2., False, True, p, 'hin'), hin - 1.2)
        self.assertAlmostEqual(c.update(0., .1, 2., False, True, p, 'her'), her - 1.2)


class AutomatikRuecknahme(unittest.TestCase):
    def setUp(self):
        self.w = {k: v for k, v in asdict(VehicleProfile()).items() if k in FELDER}
        self.stand = {}
        self.a = Lenkautomatik(lambda: dict(self.w), self.w.update, self.sichern)
        self.a.starten()
        self.vor = dict(self.w)
        for j in range(211):
            t = j / 10
            self.a.beobachten(t, .12 * math.sin(2 * math.pi * t / 3), 2., True, "ab", .012)
        self.assertIsNotNone(self.a.versuch)
        self.feld = next(iter(self.a.versuch["nach"]))
        self.nach = self.w[self.feld]

    def sichern(self, stand):
        self.stand = copy.deepcopy(stand)

    def test_fremdaenderung_eines_anderen_felds_nimmt_offenen_versuch_zurueck(self):
        self.w["cerea_d"] = .6
        self.a.beobachten(21.2, .1, 2., True, "ab", .012)
        self.assertFalse(self.a.aktiv)
        self.assertEqual(self.w[self.feld], self.vor[self.feld])
        self.assertEqual(self.w["cerea_d"], .6)
        self.assertIsNone(self.stand["versuch"])

    def test_stopp_ueberschreibt_keine_manuelle_aenderung_des_versuchsfelds(self):
        self.w[self.feld] = 67.
        self.a.stoppen()
        self.assertEqual(self.w[self.feld], 67.)
        self.assertIsNone(self.stand["versuch"])

    def test_neustart_erledigt_ruecknahme_dauerhaft(self):
        Lenkautomatik(lambda: dict(self.w), self.w.update, self.sichern, copy.deepcopy(self.stand))
        self.assertEqual(self.w[self.feld], self.vor[self.feld])
        self.assertIsNone(self.stand["versuch"])
        # Später wählt der Nutzer bewusst denselben Wert wie beim alten Versuch.
        self.w[self.feld] = self.nach
        Lenkautomatik(lambda: dict(self.w), self.w.update, self.sichern, copy.deepcopy(self.stand))
        self.assertEqual(self.w[self.feld], self.nach)

    def test_neustart_verwirft_alten_versuch_auch_bei_abweichendem_aktuellen_wert(self):
        self.w[self.feld] = 67.
        Lenkautomatik(lambda: dict(self.w), self.w.update, self.sichern, copy.deepcopy(self.stand))
        self.assertEqual(self.w[self.feld], 67.)
        self.assertIsNone(self.stand["versuch"])


class ImuKalibrierung(unittest.TestCase):
    def test_m2_korrigiert_aktuellen_schnappschuss_ohne_neuen_zeitstempel(self):
        s = ImuSource()
        s._publish(4.5, 2.4, 30., 1.)
        s.messpunkt_m1()
        s._publish(-1.5, -1.6, 30., 1.)
        zeit = s.attitude.received_at
        s.messpunkt_m2()
        self.assertAlmostEqual(s.attitude.roll_deg, -3.)
        self.assertAlmostEqual(s.attitude.pitch_deg, -2.)
        self.assertAlmostEqual(s.roh()[0], -1.5)
        self.assertAlmostEqual(s.roh()[1], -1.6)
        self.assertEqual(s.attitude.received_at, zeit)


if __name__ == "__main__":
    unittest.main()
