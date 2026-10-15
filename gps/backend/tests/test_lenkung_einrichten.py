"""Lenkung einrichten: Drehrichtung aus Beobachtung und Drehgeber, Stand der Schritte."""

import asyncio
import math
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
                         ["verbindung", "richtung", "mindestleistung", "zaehlwerte", "feinabstimmung",
                          "notaus", "freigabe", "probefahrt"])

    def test_all_done(self):
        erledigt = {"richtung": {"invert_motor": False}, "mindestleistung": {"min_duty": 0.25},
                    "zaehlwerte": {"zaehlwerte_je_grad": 210.0},
                    "feinabstimmung": {"gain_p": 0.1, "gain_i": 0.0, "gain_d": 0.02}}
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
                self.assertEqual(len(stand["schritte"]), 8)
                self.assertEqual(client.post("/api/lenkung/einrichten/feinabstimmung").status_code, 400)
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


def sprungreihe(fn, sekunden=2.5, dt=0.02):
    return [(i * dt, fn(i * dt)) for i in range(int(sekunden / dt))]


class SprungTest(unittest.TestCase):
    def test_a_clean_step(self):
        reihe = sprungreihe(lambda t: 6.0 * min(1.0, t / 0.4))
        b = le.bewerte_sprung(reihe, 0.0, 6.0)
        self.assertAlmostEqual(b["anstieg_s"], 0.36, delta=0.03)
        self.assertEqual(b["ueberschwingen"], 0.0)
        self.assertTrue(b["steht"])
        self.assertFalse(b["schwingt"])
        self.assertTrue(le.feinabstimmung_schritt([b, b], 0.09, 0.0, 0.0)["gut"])

    def test_overshoot_lowers_p_and_adds_damping(self):
        reihe = sprungreihe(lambda t: 6.0 * (1 - math.exp(-t / 0.6) * math.cos(t * 6)))
        b = le.bewerte_sprung(reihe, 0.0, 6.0)
        self.assertGreater(b["ueberschwingen"], le.FEIN_UEBERSCHWINGEN_MAX)
        s = le.feinabstimmung_schritt([b], 0.4, 0.0, 0.0)
        self.assertAlmostEqual(s["p"], 0.3)
        self.assertGreater(s["d"], 0.0)
        self.assertFalse(s["fertig"])

    def test_too_slow_raises_p_and_still_moving_counts_as_slow(self):
        reihe = sprungreihe(lambda t: 6.0 * t / 3.0)                  # erreicht das Ziel nicht
        b = le.bewerte_sprung(reihe, 0.0, 6.0)
        self.assertIsNone(b["anstieg_s"])
        self.assertFalse(b["steht"])
        self.assertAlmostEqual(le.feinabstimmung_schritt([b], 0.1, 0.0, 0.0)["p"], 0.135)

    def test_standing_short_of_the_target_adds_integral(self):
        reihe = sprungreihe(lambda t: 5.0 * min(1.0, t / 0.4))         # steht bei 5 von 6 Grad
        b = le.bewerte_sprung(reihe, 0.0, 6.0)
        self.assertTrue(b["steht"])
        self.assertAlmostEqual(b["rest_deg"], 1.0, delta=0.01)
        s = le.feinabstimmung_schritt([b], 0.1, 0.0, 0.0)
        self.assertAlmostEqual(s["i"], 0.03)

    def test_no_movement_stops_with_a_hint(self):
        b = le.bewerte_sprung(sprungreihe(lambda t: 0.0), 0.0, 6.0)
        s = le.feinabstimmung_schritt([b], 0.1, 0.0, 0.0)
        self.assertTrue(s["fertig"])
        self.assertFalse(s["gut"])
        self.assertIn("folgen", s["text"])

    def test_the_gains_stay_within_their_limits(self):
        langsam = le.bewerte_sprung(sprungreihe(lambda t: 6.0 * t / 3.0), 0.0, 6.0)
        s = le.feinabstimmung_schritt([langsam], le.FEIN_P[1], 0.0, 0.0)
        self.assertTrue(s["fertig"])
        self.assertIn("größtem P", s["text"])


class TraegeDLL(AttrappeDLL):
    """Motor mit Losbrechleistung und Trägheit: die Geschwindigkeit folgt der Leistung verzögert."""

    def __init__(self, losbrech=15.0, traegheit=0.03, faktor=1.0):
        super().__init__()
        self.lb, self.tr, self.fk = losbrech, traegheit, faktor
        self.v, self.rest = 0.0, 0.0

    def CPhidgetMotorControl_setVelocity(self, handle, kanal, wert):
        self.geschwindigkeit[kanal] = wert.value
        w = wert.value
        soll = 0.0 if abs(w) < self.lb else math.copysign(abs(w) - self.lb, w) * self.fk
        self.v += (soll - self.v) * self.tr
        self.rest += self.v
        schritt = int(self.rest)
        self.rest -= schritt
        self.drehgeber += schritt
        return 0


class FeinabstimmungTest(unittest.TestCase):
    """Der Motorregler am nachgebildeten trägen Motor: von selbst zur Ruhe."""

    def abstimmen(self, dll, **cfg):
        werte = dict(bibliothek="phidget21", control="velocity", feedback="encoder",
                     encoder_counts_per_deg=40.0, min_duty=0.2, max_duty=0.55, dead_band_deg=0.3)
        werte.update(cfg)
        ausgang = actuators.PhidgetOutput(PhidgetConfig(**werte))
        with mock.patch.object(phidget21, "_DLL", dll), mock.patch.object(phidget21, "_DLL_FEHLER", ""), \
                mock.patch.object(actuators, "FEIN_HALTEN_S", 1.5):
            async def ablauf():
                await ausgang.start()
                ausgang.running = True
                laeuft = asyncio.create_task(ausgang._control_loop())
                try:
                    return await ausgang.feinabstimmen()
                finally:
                    ausgang.running = False
                    await laeuft
                    await ausgang.stop()
            ergebnis = asyncio.run(ablauf())
        phidget21._OFFEN.clear()
        return ausgang, ergebnis

    def test_an_overshooting_controller_is_damped(self):
        dll = TraegeDLL()
        ausgang, e = self.abstimmen(dll, gain_p=0.6, gain_i=0.0, gain_d=0.0)
        self.assertTrue(e["gut"], e["text"])
        self.assertLess(e["ende"]["gain_p"], 0.6)
        self.assertGreater(e["ende"]["gain_d"], 0.0)
        self.assertGreater(e["verlauf"][0]["hin"]["ueberschwingen"], le.FEIN_UEBERSCHWINGEN_MAX)
        self.assertLessEqual(e["verlauf"][-1]["hin"]["ueberschwingen"], le.FEIN_UEBERSCHWINGEN_MAX)
        self.assertEqual(dll.geschwindigkeit[0], 0.0)

    def test_a_motor_that_does_not_move_keeps_the_start_values(self):
        dll = TraegeDLL(losbrech=200.0)                             # läuft nie an
        ausgang, e = self.abstimmen(dll, gain_p=0.09, gain_i=0.01, gain_d=0.002)
        self.assertFalse(e["gut"])
        self.assertEqual(e["ende"], {"gain_p": 0.09, "gain_i": 0.01, "gain_d": 0.002})
        self.assertEqual(len(e["verlauf"]), 1)
