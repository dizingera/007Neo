"""Lenkausgang 1.5.85: eigener Regelfaden, Befehls-Wächter, Eingriff in Radrichtung,
fehlende Rückmeldung, Vorwärts bestätigen ohne neue Radmitte, Lenkgeschwindigkeit."""

import asyncio
import time
import unittest
from unittest import mock

from agripilot import actuators, phidget21
from agripilot.config import PhidgetConfig
from agripilot.guidance import HeadingFilter

from test_phidget21 import AttrappeDLL


def ausgang(**werte):
    cfg = dict(bibliothek="phidget21", serial_number=557397, control="velocity", feedback="encoder",
               encoder_counts_per_deg=40.0, gain_p=0.1, gain_i=0.0, gain_d=0.0, max_duty=0.5)
    cfg.update(werte)
    return actuators.PhidgetOutput(PhidgetConfig(**cfg))


class MitDLL(unittest.TestCase):
    def setUp(self):
        self.dll = AttrappeDLL()
        for ziel, wert in (("_DLL", self.dll), ("_DLL_FEHLER", "")):
            patcher = mock.patch.object(phidget21, ziel, wert)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(phidget21._OFFEN.clear)


class Faden(MitDLL):
    def test_the_control_loop_runs_in_its_own_thread_and_stops_without_commands(self):
        a = ausgang()

        async def ablauf():
            await a.start()
            self.assertIsNotNone(a._thread)
            self.assertTrue(a._thread.is_alive())
            a.command(True, 5.0, actuators.SteerContext(speed_ms=2.0))
            await asyncio.sleep(0.1)
            self.assertNotEqual(self.dll.geschwindigkeit.get(0, 0.0), 0.0)   # lenkt
            # Das Programm hängt: kein Lenkbefehl mehr. Der Faden hält den Motor an.
            await asyncio.sleep(actuators.KOMMANDO_FRIST_S + 0.2)
            self.assertEqual(self.dll.geschwindigkeit[0], 0.0)
            self.assertEqual(a.last_error_text, actuators.KEIN_BEFEHL)
            await a.stop()
            self.assertIsNone(a._thread)

        asyncio.run(ablauf())


class OhneFaden(MitDLL):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(actuators, "REGEL_FADEN", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_motorstaerke_wirkt_gespeichert_sofort_bis_zur_ausgabe(self):
        import tempfile
        from pathlib import Path
        from agripilot.config import Config
        from agripilot.settings import uebernehmen, EinstellungsFehler
        cfg = Config()
        a = ausgang(max_duty=.55)
        cfg.phidget = a.config
        async def ablauf():
            await a.start()
            try:
                a.command(True, 20., actuators.SteerContext(speed_ms=2.))
                a._step(.02)
                self.assertAlmostEqual(abs(self.dll.geschwindigkeit[0]), 55.)
                with tempfile.TemporaryDirectory() as d:
                    cfg.path = Path(d) / 'config.yaml'
                    ergebnis = uebernehmen(cfg, {'phidget.max_duty': .75})
                    self.assertEqual(ergebnis['neustart_noetig'], [])
                    self.assertIn('max_duty: 0.75', cfg.path.read_text(encoding='utf-8'))
                    a._step(.02)
                    self.assertAlmostEqual(abs(self.dll.geschwindigkeit[0]), 75.)
                    for falsch in (0., 1.01, float('nan')):
                        with self.assertRaises(EinstellungsFehler):
                            uebernehmen(cfg, {'phidget.max_duty': falsch})
                    self.assertEqual(a.config.max_duty, .75)
                    a.command(False, 0., actuators.SteerContext())
                    a._step(.02)
                    self.assertEqual(self.dll.geschwindigkeit[0], 0.)
            finally:
                await a.stop()
        asyncio.run(ablauf())

    def test_an_inverted_motor_following_quickly_is_no_driver(self):
        a = ausgang(invert_motor=True, gain_p=0.5)

        async def ablauf():
            await a.start()
            a.command(True, 20.0, actuators.SteerContext(speed_ms=2.0))
            # Das Rad läuft zügig nach rechts, genau wohin der Motor es schickt (30°/s).
            for i in range(30):
                self.dll.drehgeber = int(40.0 * 0.6 * i)
                a.command(True, 20.0, actuators.SteerContext(speed_ms=2.0))
                a._step(0.02)
                time.sleep(0.02)                # länger als die 0,3 s, nach denen ein Eingriff gälte
                self.assertFalse(a.feedback.driver_override, f"Schritt {i}")
            await a.stop()

        asyncio.run(ablauf())

    def test_a_driver_against_an_inverted_motor_is_still_seen(self):
        a = ausgang(invert_motor=True, gain_p=0.5)

        async def ablauf():
            await a.start()
            gesehen = False
            for i in range(30):
                self.dll.drehgeber = -int(40.0 * 0.6 * i)      # das Rad geht nach links, gegen den Motor
                a.command(True, 20.0, actuators.SteerContext(speed_ms=2.0))
                a._step(0.02)
                time.sleep(0.02)
                gesehen = gesehen or a.feedback.driver_override
            self.assertTrue(gesehen)
            await a.stop()

        asyncio.run(ablauf())

    def test_missing_feedback_while_steering_marks_the_output_disturbed_and_recovers(self):
        a = ausgang()

        async def ablauf():
            await a.start()
            self.assertTrue(a.ready)
            with mock.patch.object(a, "measured_angle", return_value=None):
                for _ in range(5):
                    a.command(True, 3.0, actuators.SteerContext(speed_ms=2.0))
                    a._step(0.02)
                    time.sleep(0.1)
            self.assertFalse(a.ready)
            self.assertIn("Rückmeldung fehlt", a.status)
            a.command(True, 3.0, actuators.SteerContext(speed_ms=2.0))
            a._step(0.02)
            self.assertTrue(a.ready)
            self.assertIn("557397", a.status)
            await a.stop()

        asyncio.run(ablauf())


class PositionsReglerEingriff(unittest.TestCase):
    def test_pushing_is_measured_on_the_duty_cycle(self):
        cfg = PhidgetConfig(override_deg=4.0, velocity_limit=50.0)
        a = actuators.PhidgetPositionOutput(cfg)
        a.target_deg = 10.0
        self.assertFalse(a._detect_override(0.0, 0.9, True))         # beginnt zu zählen
        a._error_since = time.time() - 1.0
        self.assertTrue(a._detect_override(0.0, 0.9, True))
        self.assertFalse(a._detect_override(0.0, 0.3, True))         # drückt nicht: kein Eingriff


class VorwaertsBestaetigen(unittest.TestCase):
    def test_confirming_forward_also_works_in_the_slow_case(self):
        f = HeadingFilter()
        f.update(90.0, None, 2.0)
        f.update(270.0, None, 0.7)               # von Hand gewendet, langsam los
        self.assertTrue(f.gegenlaeufig)
        self.assertFalse(f.rueckwaerts)
        f.bestaetige_vorwaerts()
        f.update(270.0, None, 0.7)
        self.assertAlmostEqual(f.value, 270.0)
        self.assertFalse(f.gegenlaeufig)


class Lenkgeschwindigkeit(unittest.TestCase):
    def test_the_tractor_setting_reaches_the_steering(self):
        import tempfile
        import warnings
        from fastapi.testclient import TestClient
        from agripilot import config as config_module
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")
        with tempfile.TemporaryDirectory() as ordner:
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = ordner
            with TestClient(create_app(config)) as c:
                c.post("/api/traktor", json={"max_steer_rate_deg_s": 12.0})
                anwendung = c.app.state.app
                deadline = time.time() + 5
                while time.time() < deadline and anwendung.steering.max_rate_deg_s != 12.0:
                    time.sleep(0.1)                  # der Simulator liefert die nächste Position
                self.assertEqual(anwendung.steering.max_rate_deg_s, 12.0)
                self.assertEqual(c.post("/api/steering/vorwaerts").status_code, 200)


if __name__ == "__main__":
    unittest.main()


class MitteLernen(MitDLL):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(actuators, "REGEL_FADEN", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_learned_centre_survives_arming_with_turned_wheels(self):
        a = ausgang(mitte_versatz_deg=0.7)

        async def ablauf():
            await a.start()
            self.dll.drehgeber = 400                     # Räder stehen gerade bei 400
            a.learn_centre()
            self.assertEqual(a.config.mitte_versatz_deg, 0.0)
            self.dll.drehgeber = 800                     # nach der Wende 10° eingeschlagen
            a.on_arm()                                   # Scharfschalten: Mitte bleibt
            self.assertAlmostEqual(a.measured_angle(), 10.0)
            await a.stop()

        asyncio.run(ablauf())

    def test_without_a_learned_centre_arming_still_sets_it(self):
        a = ausgang()

        async def ablauf():
            await a.start()
            self.dll.drehgeber = 800
            a.on_arm()
            self.assertAlmostEqual(a.measured_angle(), 0.0)
            await a.stop()

        asyncio.run(ablauf())
