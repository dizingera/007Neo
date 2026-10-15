"""Lenkung einrichten: Drehrichtung aus Beobachtung und Drehgeber, Stand der Schritte."""

import asyncio
import unittest
from unittest import mock

from agripilot import actuators, lenkung_einrichten as le, phidget21
from agripilot.config import PhidgetConfig

from tests.test_phidget21 import AttrappeDLL


class RichtungTest(unittest.TestCase):
    def test_everything_right_changes_nothing_and_is_done(self):
        e = le.richtung_auswerten("rechts", 60, False, False)
        self.assertEqual(e["aenderungen"], {})
        self.assertTrue(e["erledigt"])

    def test_wheels_went_left_flips_the_motor_but_not_a_correct_encoder(self):
        # Nach links gegangen, Drehgeber zählte dabei herunter: nach rechts zählt er also hoch.
        e = le.richtung_auswerten("links", -60, False, False)
        self.assertEqual(e["aenderungen"], {"phidget.invert_motor": True})
        self.assertFalse(e["erledigt"])                       # zur Kontrolle noch einmal testen

    def test_encoder_counting_down_to_the_right_is_flipped(self):
        e = le.richtung_auswerten("rechts", -45, False, False)
        self.assertEqual(e["aenderungen"], {"phidget.encoder_invert": True})

    def test_both_wrong_flips_both(self):
        e = le.richtung_auswerten("links", 45, False, False)
        self.assertEqual(e["aenderungen"], {"phidget.invert_motor": True, "phidget.encoder_invert": True})

    def test_an_already_inverted_encoder_is_taken_into_account(self):
        e = le.richtung_auswerten("rechts", -45, False, True)
        self.assertEqual(e["aenderungen"], {})
        self.assertTrue(e["erledigt"])
        e = le.richtung_auswerten("rechts", 45, True, True)
        self.assertEqual(e["aenderungen"], {"phidget.encoder_invert": False})

    def test_no_movement_or_no_encoder_changes_nothing(self):
        self.assertEqual(le.richtung_auswerten("nichts", 0, False, False)["aenderungen"], {})
        e = le.richtung_auswerten("rechts", 3, False, False)
        self.assertEqual(e["aenderungen"], {})
        self.assertIn("Drehgeber", e["text"])
        with self.assertRaises(ValueError):
            le.richtung_auswerten("oben", 50, False, False)


class StatusTest(unittest.TestCase):
    def test_later_steps_wait_for_earlier_ones_except_the_emergency_stop(self):
        schritte = le.status({}, {"typ": "phidget", "bereit": True}, False, False, {})
        nach_id = {s["id"]: s for s in schritte}
        self.assertTrue(nach_id["verbindung"]["fertig"])
        self.assertTrue(nach_id["richtung"]["moeglich"])
        self.assertFalse(nach_id["mindestleistung"]["moeglich"])
        self.assertTrue(nach_id["notaus"]["moeglich"])
        self.assertEqual([s["id"] for s in schritte],
                         ["verbindung", "richtung", "mindestleistung", "zaehlwerte", "notaus",
                          "freigabe", "probefahrt"])

    def test_all_done(self):
        erledigt = {"richtung": {"invert_motor": False}, "mindestleistung": {"min_duty": 0.25},
                    "zaehlwerte": {"zaehlwerte_je_grad": 210.0}}
        schritte = le.status(erledigt, {"typ": "phidget", "bereit": True}, True, True,
                             {"befund": {"art": "gut"}})
        self.assertTrue(all(s["fertig"] for s in schritte))
        self.assertEqual(schritte[2]["werte"], {"min_duty": 0.25})

    def test_without_a_phidget_motor_nothing_after_the_connection_is_possible(self):
        schritte = le.status({}, {"typ": "udp", "bereit": True}, False, False, {})
        self.assertFalse(schritte[0]["fertig"])
        self.assertFalse(schritte[1]["moeglich"])


class DrehgeberUmkehrTest(unittest.TestCase):
    """Mit umgekehrtem Drehgeber muss der Regler zum Ziel hin lenken, nicht weg."""

    def setUp(self):
        self.dll = AttrappeDLL()
        for ziel, wert in (("_DLL", self.dll), ("_DLL_FEHLER", "")):
            patcher = mock.patch.object(phidget21, ziel, wert)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(phidget21._OFFEN.clear)

    def test_an_inverted_encoder_reads_the_angle_the_right_way_round(self):
        cfg = PhidgetConfig(bibliothek="phidget21", control="velocity", feedback="encoder",
                            encoder_counts_per_deg=40.0, encoder_invert=True, gain_p=0.1,
                            gain_i=0.0, gain_d=0.0, min_duty=0.0, max_duty=0.5)
        ausgang = actuators.PhidgetOutput(cfg)

        async def ablauf():
            await ausgang.start()
            ausgang.command(True, 2.0, actuators.SteerContext(speed_ms=2.0))
            self.dll.drehgeber = -40                     # umgekehrt: das sind +1 Grad
            self.assertAlmostEqual(ausgang.measured_angle(), 1.0)
            ausgang._step(0.02)
            self.assertGreater(self.dll.geschwindigkeit[0], 0.0)    # weiter nach rechts, zum Ziel
            self.dll.drehgeber = -80                     # 2 Grad erreicht
            ausgang._step(0.02)
            self.assertAlmostEqual(self.dll.geschwindigkeit[0], 0.0, places=3)
            await ausgang.stop()

        asyncio.run(ablauf())

    def test_the_direction_test_pulses_right_with_the_configured_motor_inversion(self):
        self.dll.losbrech = 5.0
        cfg = PhidgetConfig(bibliothek="phidget21", control="velocity", feedback="encoder",
                            invert_motor=True)
        ausgang = actuators.PhidgetOutput(cfg)

        async def ablauf():
            await ausgang.start()
            laeuft = asyncio.create_task(ausgang._control_loop())
            ausgang.running = True
            with mock.patch.object(actuators, "EINMESS_REGELTAKT_S", 0.03), \
                    mock.patch.object(actuators, "EINMESS_AUSROLLEN_S", 0.02):
                ergebnis = await ausgang.richtung_testen(0.2, 0.1)
            ausgang.running = False
            await laeuft
            await ausgang.stop()
            return ergebnis

        ergebnis = asyncio.run(ablauf())
        self.assertLess(ergebnis["bewegt"], 0)          # Motor umgekehrt: "rechts" dreht hardwareseitig negativ
        self.assertEqual(self.dll.geschwindigkeit[0], 0.0)


if __name__ == "__main__":
    unittest.main()


class EinrichtenApiTest(unittest.TestCase):
    """Von außen, wie die Oberfläche es aufruft - im Simulator ohne Motor."""

    def test_status_steps_and_refusals(self):
        import tempfile
        from fastapi.testclient import TestClient
        from agripilot import config as config_module
        from agripilot.server import create_app

        with tempfile.TemporaryDirectory() as ordner:
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = ordner
            config.gnss.source = "simulator"
            with TestClient(create_app(config)) as client:
                stand = client.get("/api/lenkung/einrichten").json()["data"]
                self.assertEqual(len(stand["schritte"]), 7)
                self.assertFalse(stand["schritte"][0]["fertig"])          # Simulator ist kein Phidget
                self.assertIn("encoder_invert", stand["einstellungen"])
                # Ohne Phidget-Motor kein Richtungstest.
                self.assertEqual(client.post("/api/lenkung/einrichten/richtung-test").status_code, 400)
                antwort = client.post("/api/lenkung/einrichten/richtung", json={"gesehen": "nichts"}).json()
                self.assertEqual(antwort["data"]["aenderungen"], {})
                self.assertEqual(client.post("/api/lenkung/einrichten/richtung",
                                             json={"gesehen": "oben"}).status_code, 400)
                self.assertEqual(client.post("/api/lenkung/einrichten/erledigt",
                                             json={"schritt": "zaehlwerte",
                                                   "werte": {"zaehlwerte_je_grad": 210}}).status_code, 200)
                stand = client.get("/api/lenkung/einrichten").json()["data"]
                self.assertTrue(next(s for s in stand["schritte"] if s["id"] == "zaehlwerte")["fertig"])
                self.assertEqual(client.post("/api/lenkung/einrichten/erledigt",
                                             json={"schritt": "unsinn"}).status_code, 400)
                client.post("/api/lenkung/einrichten/neu")
                stand = client.get("/api/lenkung/einrichten").json()["data"]
                self.assertFalse(next(s for s in stand["schritte"] if s["id"] == "zaehlwerte")["fertig"])
