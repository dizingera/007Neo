"""Phidget21: MotorControl 1065 über den älteren Treiber, ohne Hardware geprüft.

Die ``phidget21.dll`` wird durch eine Attrappe ersetzt, die mitschreibt, was
aufgerufen wird, und Werte so zurückgibt wie die echte (über ``byref``).
"""

import asyncio
import ctypes
import unittest
from unittest import mock

from agripilot import actuators, geraete, phidget21
from agripilot.config import Config, PhidgetConfig


class AttrappeDLL:
    """Tut so, als wäre eine MotorControl 1065 (Seriennummer 557397) angesteckt."""

    def __init__(self, angesteckt=True):
        self.angesteckt = angesteckt
        self.geschwindigkeit = {}
        self.beschleunigung = {}
        self.drehgeber = 0
        self.sensor = 500
        self.offen = False
        self.aufrufe = []

    @staticmethod
    def _setzen(ref, wert):
        ref._obj.value = wert
        return 0

    def CPhidget_getLibraryVersion(self, ref):
        return self._setzen(ref, b"Phidget21 - Version 2.1.8")

    def CPhidgetMotorControl_create(self, ref):
        return self._setzen(ref, 1234)

    def CPhidget_open(self, handle, seriennummer):
        self.offen = True
        self.aufrufe.append(("open", seriennummer))
        return 0

    def CPhidget_waitForAttachment(self, handle, ms):
        return 0 if self.angesteckt else 13

    def CPhidget_getErrorDescription(self, code, ref):
        return self._setzen(ref, b"Timed Out")

    def CPhidget_close(self, handle):
        self.offen = False
        return 0

    def CPhidget_delete(self, handle):
        return 0

    def CPhidget_getSerialNumber(self, handle, ref):
        return self._setzen(ref, 557397)

    def CPhidget_getDeviceName(self, handle, ref):
        return self._setzen(ref, b"Phidget High Current Motor Controller 1-motor")

    def CPhidgetMotorControl_getMotorCount(self, handle, ref):
        return self._setzen(ref, 1)

    def CPhidgetMotorControl_getEncoderCount(self, handle, ref):
        return self._setzen(ref, 1)

    def CPhidgetMotorControl_getSensorCount(self, handle, ref):
        return self._setzen(ref, 2)

    losbrech = None                  # Leistung in %, ab der sich der Motor bewegt; None = Drehgeber steht

    def CPhidgetMotorControl_setVelocity(self, handle, kanal, wert):
        self.geschwindigkeit[kanal] = wert.value
        if self.losbrech is not None and abs(wert.value) >= self.losbrech:
            self.drehgeber += int(wert.value)            # je Aufruf (50 Hz) ein Stück weiter
        return 0

    def CPhidgetMotorControl_setAcceleration(self, handle, kanal, wert):
        self.beschleunigung[kanal] = wert.value
        return 0

    def CPhidgetMotorControl_getAcceleration(self, handle, kanal, ref):
        return self._setzen(ref, self.beschleunigung.get(kanal, 0.0))

    def CPhidgetMotorControl_setBraking(self, handle, kanal, wert):
        self.bremsen = getattr(self, "bremsen", {})
        self.bremsen[kanal] = wert.value
        return 0

    def CPhidgetMotorControl_getAccelerationMin(self, handle, kanal, ref):
        return self._setzen(ref, 24.51)

    def CPhidgetMotorControl_getAccelerationMax(self, handle, kanal, ref):
        return self._setzen(ref, 6125.0)

    def CPhidgetMotorControl_getEncoderPosition(self, handle, kanal, ref):
        return self._setzen(ref, self.drehgeber)

    def CPhidgetMotorControl_getSensorValue(self, handle, kanal, ref):
        return self._setzen(ref, self.sensor)

    def CPhidgetMotorControl_getCurrent(self, handle, kanal, ref):
        return self._setzen(ref, 1.2)


class Phidget21Test(unittest.TestCase):
    def setUp(self):
        self.dll = AttrappeDLL()
        for ziel, wert in (("_DLL", self.dll), ("_DLL_FEHLER", "")):
            patcher = mock.patch.object(phidget21, ziel, wert)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(phidget21._OFFEN.clear)

    def test_the_motor_runs_in_percent_and_stops_on_close(self):
        geraet = phidget21.MotorControl21()
        geraet.oeffnen(557397)
        self.assertEqual(self.dll.aufrufe, [("open", 557397)])
        motor = phidget21.Motor(geraet, 0)
        motor.setTargetVelocity(0.4)
        self.assertAlmostEqual(self.dll.geschwindigkeit[0], 40.0)
        motor.setTargetVelocity(-3.0)                     # nie über volle Leistung
        self.assertAlmostEqual(self.dll.geschwindigkeit[0], -100.0)
        # Beschleunigung: Phidget22-Anteil je Sekunde -> Prozent je Sekunde.
        self.assertAlmostEqual(motor.getMaxAcceleration(), 61.25)
        motor.setAcceleration(12.5)
        self.assertAlmostEqual(self.dll.beschleunigung[0], 1250.0)
        with self.assertRaises(phidget21.Phidget21Fehler):
            motor.enableFailsafe(500)                     # die 1065 kennt keinen
        motor.resetFailsafe()                             # darf im Regeltakt nicht stören
        motor.close()
        self.assertEqual(self.dll.geschwindigkeit[0], 0.0)
        self.assertFalse(self.dll.offen)

    def test_encoder_and_analog_input_are_read_from_the_same_board(self):
        geraet = phidget21.MotorControl21()
        geraet.oeffnen()
        self.dll.drehgeber, self.dll.sensor = 1234, 750
        self.assertEqual(phidget21.Drehgeber(geraet, 0).getPosition(), 1234)
        self.assertAlmostEqual(phidget21.Spannungsverhaeltnis(geraet, 1).getVoltageRatio(), 0.75)

    def test_a_board_that_does_not_answer_is_a_readable_error_and_released(self):
        self.dll.angesteckt = False
        with self.assertRaises(phidget21.Phidget21Fehler) as fehler:
            phidget21.MotorControl21().oeffnen(557397, 100)
        self.assertIn("Timed Out", str(fehler.exception))
        self.assertFalse(self.dll.offen)
        self.assertEqual(phidget21._OFFEN, [])

    def test_auto_takes_phidget21_when_phidget22_is_missing(self):
        with mock.patch.object(actuators, "phidget_available", return_value=(False, "kein Phidget22")):
            self.assertEqual(actuators.phidget_bibliothek(PhidgetConfig())[0], "phidget21")
            self.assertEqual(actuators.phidget_bibliothek(PhidgetConfig(bibliothek="phidget22")),
                             (None, "kein Phidget22"))
            # Positionsregler gibt es unter Phidget21 nicht: dann der eigene Regelkreis.
            config = Config()
            config.steering.output = "phidget"
            config.phidget.control = "position"
            self.assertIsInstance(actuators.build_output(config), actuators.PhidgetOutput)
        with mock.patch.object(actuators, "phidget_available", return_value=(True, "")):
            self.assertEqual(actuators.phidget_bibliothek(PhidgetConfig())[0], "phidget22")

    def test_the_steering_loop_drives_the_1065_with_the_motor_encoder(self):
        cfg = PhidgetConfig(bibliothek="phidget21", serial_number=557397, control="velocity",
                            feedback="encoder", encoder_counts_per_deg=40.0, gain_p=0.1,
                            gain_i=0.0, gain_d=0.0, max_duty=0.5)
        ausgang = actuators.PhidgetOutput(cfg)

        async def ablauf():
            await ausgang.start()
            self.assertTrue(ausgang.ready, ausgang.status)
            self.assertIn("557397", ausgang.status)
            self.assertIn("phidget21", ausgang.status)
            self.assertIn("keinen Failsafe", ausgang.last_error_text)
            ausgang.command(True, 2.0, actuators.SteerContext(speed_ms=2.0))
            ausgang._step(0.02)                          # 2° Soll, 0° Ist -> 0,2 Leistung
            self.assertAlmostEqual(self.dll.geschwindigkeit[0], 20.0, places=3)
            self.dll.drehgeber = 80                      # 2° erreicht
            ausgang._step(0.02)
            self.assertAlmostEqual(self.dll.geschwindigkeit[0], 0.0, places=3)
            ausgang.command(False, 0.0, actuators.SteerContext())
            ausgang._step(0.02)
            self.assertEqual(self.dll.geschwindigkeit[0], 0.0)
            await ausgang.stop()

        asyncio.run(ablauf())
        self.assertFalse(self.dll.offen)

    def test_the_motor_test_pulses_weakly_reports_the_encoder_and_stops(self):
        cfg = PhidgetConfig(bibliothek="phidget21", control="velocity", feedback="yaw_rate")
        ausgang = actuators.PhidgetOutput(cfg)

        async def ablauf():
            await ausgang.start()
            self.assertTrue(ausgang.ready, ausgang.status)
            laeuft = asyncio.create_task(ausgang._control_loop())
            ausgang.running = True
            await asyncio.sleep(0.05)
            gesehen = []

            async def beobachten():
                for _ in range(8):
                    gesehen.append(self.dll.geschwindigkeit.get(0))
                    self.dll.drehgeber += 25
                    await asyncio.sleep(0.05)

            b = asyncio.create_task(beobachten())
            # 90 % verlangt, gedeckelt auf 35 %; 5 s verlangt, gedeckelt auf 1 s.
            ergebnis = await ausgang.motortest(-1, 0.9, 5.0)
            await b
            ausgang.running = False
            await laeuft
            self.assertEqual(ergebnis["richtung"], "links")
            # Was die Platine wirklich eingestellt hat, für die Fehlersuche.
            self.assertAlmostEqual(ergebnis["beschleunigung_ist"], 1250.0)
            self.assertEqual(ergebnis["beschleunigung_bereich"], [24.5, 6125.0])
            self.assertAlmostEqual(ergebnis["strom_max_a"], 1.2)
            self.assertEqual(self.dll.bremsen[0], 0.0)                 # Bremsen aus, wie im Terminal
            self.assertGreater(ergebnis["zaehlwerte_je_s"], 0)
            self.assertAlmostEqual(ergebnis["leistung"], actuators.MOTORTEST_MAX)
            self.assertAlmostEqual(ergebnis["dauer_s"], actuators.MOTORTEST_DAUER_MAX_S)
            self.assertGreater(ergebnis["drehgeber_bewegt"], 0)       # Encoder lief mit, obwohl nicht Rückmeldung
            self.assertIn(-35.0, [round(g, 1) for g in gesehen if g is not None])
            self.assertEqual(self.dll.geschwindigkeit[0], 0.0)        # danach steht der Motor
            # Scharf: kein Test.
            ausgang.command(True, 0.0, actuators.SteerContext())
            with self.assertRaises(RuntimeError):
                await ausgang.motortest(1, 0.1, 0.1)
            await ausgang.stop()

        asyncio.run(ablauf())

    def test_a_small_error_gets_the_minimum_duty_and_none_inside_the_dead_band(self):
        cfg = PhidgetConfig(bibliothek="phidget21", control="velocity", feedback="encoder",
                            encoder_counts_per_deg=40.0, gain_p=0.09, gain_i=0.0, gain_d=0.0,
                            max_duty=0.5, min_duty=0.2, dead_band_deg=0.3)
        ausgang = actuators.PhidgetOutput(cfg)

        async def ablauf():
            await ausgang.start()
            ausgang.command(True, 1.0, actuators.SteerContext(speed_ms=2.0))
            ausgang._step(0.02)                          # 1 Grad * 0,09 = 9 % -> angehoben auf 20 %
            self.assertAlmostEqual(self.dll.geschwindigkeit[0], 20.0, places=3)
            self.dll.drehgeber = 38                      # 0,95 Grad: Abweichung 0,05 -> Totband
            ausgang._step(0.02)
            self.assertAlmostEqual(self.dll.geschwindigkeit[0], 0.0, places=3)
            await ausgang.stop()

        asyncio.run(ablauf())

    def _ausgang(self):
        cfg = PhidgetConfig(bibliothek="phidget21", control="velocity", feedback="encoder")
        return actuators.PhidgetOutput(cfg)

    def test_calibration_finds_the_breakaway_duty_and_suggests_a_minimum(self):
        self.dll.losbrech = 22.0
        ausgang = self._ausgang()

        async def ablauf():
            await ausgang.start()
            laeuft = asyncio.create_task(ausgang._control_loop())
            ausgang.running = True
            with mock.patch.object(actuators, "EINMESS_IMPULS_S", 0.06), \
                    mock.patch.object(actuators, "EINMESS_REGELTAKT_S", 0.03), \
                    mock.patch.object(actuators, "EINMESS_AUSROLLEN_S", 0.02):
                ergebnis = await ausgang.einmessen()
            ausgang.running = False
            await laeuft
            await ausgang.stop()
            return ergebnis

        ergebnis = asyncio.run(ablauf())
        self.assertEqual(ergebnis["losbrech_rechts"], 0.23)            # Stufen 8, 11, 14, 17, 20, 23 %
        self.assertEqual(ergebnis["losbrech_links"], 0.23)
        self.assertAlmostEqual(ergebnis["mindestleistung_vorschlag"], 0.29)
        self.assertEqual(ergebnis["hinweis"], "")
        self.assertEqual(self.dll.geschwindigkeit[0], 0.0)

    def test_calibration_reports_a_motor_that_never_moves_the_encoder(self):
        self.dll.losbrech = None
        ausgang = self._ausgang()

        async def ablauf():
            await ausgang.start()
            laeuft = asyncio.create_task(ausgang._control_loop())
            ausgang.running = True
            with mock.patch.object(actuators, "EINMESS_IMPULS_S", 0.03), \
                    mock.patch.object(actuators, "EINMESS_REGELTAKT_S", 0.02), \
                    mock.patch.object(actuators, "EINMESS_AUSROLLEN_S", 0.01):
                ergebnis = await ausgang.einmessen()
            ausgang.running = False
            await laeuft
            await ausgang.stop()
            return ergebnis

        ergebnis = asyncio.run(ablauf())
        self.assertIsNone(ergebnis["mindestleistung_vorschlag"])
        self.assertIn("nichts bewegt", ergebnis["hinweis"])

    def test_the_stops_are_measured_by_hand_and_give_counts_per_degree(self):
        ausgang = self._ausgang()

        async def ablauf():
            await ausgang.start()
            laeuft = asyncio.create_task(ausgang._control_loop())
            ausgang.running = True
            self.dll.drehgeber = 100
            ausgang.anschlag_start()
            for stand in (-4000, 2000, 5000, 11000, 100):          # von Hand von Anschlag zu Anschlag
                self.dll.drehgeber = stand
                await asyncio.sleep(0.04)
            ergebnis = ausgang.anschlag_ende(70.0)
            ausgang.running = False
            await laeuft
            await ausgang.stop()
            return ergebnis

        ergebnis = asyncio.run(ablauf())
        self.assertEqual((ergebnis["klein"], ergebnis["gross"]), (-4000, 11000))
        self.assertEqual(ergebnis["zaehlwerte"], 15000)
        self.assertAlmostEqual(ergebnis["zaehlwerte_je_grad"], 214.29, places=2)
        with self.assertRaises(RuntimeError):
            ausgang.anschlag_ende(70.0)                               # nicht gestartet

    def test_the_device_search_finds_the_1065_and_suggests_phidget21(self):
        with mock.patch.object(geraete, "_phidget22_kanaele",
                               return_value=([], {}, ["Phidget: Treiber fehlt"])):
            funde, vorschlag, probleme = geraete.phidget_kanaele()
        self.assertEqual(probleme, [])
        self.assertEqual(len(funde), 1)
        self.assertIn("Phidget21", funde[0].name)
        self.assertEqual(vorschlag["phidget.bibliothek"], "phidget21")
        self.assertEqual(vorschlag["phidget.serial_number"], 557397)
        self.assertEqual(vorschlag["phidget.control"], "velocity")
        self.assertEqual(vorschlag["phidget.feedback"], "encoder")
        self.assertFalse(self.dll.offen)                 # nach dem Suchen wieder freigegeben


if __name__ == "__main__":
    unittest.main()
