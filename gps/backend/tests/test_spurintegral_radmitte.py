"""Dauerversatz ausgleichen, Hang-Vorsteuerung, Radmitte aus der Fahrt."""

import math
import random
import unittest
from collections import deque

from agripilot.guidance import GuidanceLine, VehicleProfile
from agripilot.radmitte import MIN_PROBEN, Radmitte
from agripilot.spurintegral import GRENZE_DEG, SpurIntegral, hang_vorsteuerung


def fahrt(staerke, drift=0.05, versatz=0.0, sekunden=90, tempo=2.5, regelart="stanley", rauschen=0.02):
    """Geradeaus nach Norden mit seitlichem Abtrieb (+ rechts) und verstellter Radmitte."""
    random.seed(3)
    linie = GuidanceLine("ab", [(0, 0), (0, 5000)], 3.0)
    p = VehicleProfile(steer_gain=0.9, regelart=regelart)
    integ = SpurIntegral()
    x = y = h = delta = 0.0
    dt, puffer, quer = 0.1, deque([0.0] * 2), []
    for _ in range(int(sekunden / dt)):
        g = linie.solve((x + random.gauss(0, rauschen), y), h, tempo, p)
        zusatz = integ.update(g.cross_track_m, dt, tempo, True, True, staerke, "hin")
        puffer.append(p.einschlag_begrenzen(g.steer_angle_deg + zusatz))
        delta += max(-2.5, min(2.5, (puffer.popleft() - delta) * min(1.0, dt / 0.3)))
        hr = math.radians(h)
        x += (tempo * math.sin(hr) + drift * math.cos(hr)) * dt
        y += (tempo * math.cos(hr) - drift * math.sin(hr)) * dt
        h = (h + math.degrees(tempo / 2.6 * math.tan(math.radians(delta + versatz))) * dt) % 360.0
        quer.append(x)
    rest = quer[len(quer) // 2:]
    return sum(rest) / len(rest), integ


class Dauerversatz(unittest.TestCase):
    def test_without_it_the_slope_pushes_the_machine_aside(self):
        mittel, _ = fahrt(0)
        self.assertGreater(mittel, 0.05)

    def test_with_it_the_offset_disappears(self):
        for regelart in ("stanley", "voraus"):
            mittel, integ = fahrt(2, regelart=regelart)
            self.assertLess(abs(mittel), 0.015, regelart)
            self.assertLess(integ.wert_deg, 0)             # nach links gegen den Abtrieb

    def test_a_shifted_wheel_centre_is_absorbed_too(self):
        mittel, _ = fahrt(2, drift=0.0, versatz=1.0)
        self.assertLess(abs(mittel), 0.015)

    def test_bounded_and_switched_off(self):
        i = SpurIntegral()
        for _ in range(5000):
            i.update(0.2, 0.1, 3.0, True, True, 5.0, "hin")
        self.assertEqual(i.wert_deg, -GRENZE_DEG)
        self.assertEqual(i.update(0.2, 0.1, 3.0, True, True, 0.0, "hin"), 0.0)

    def test_far_from_the_line_or_turning_it_does_not_grow(self):
        i = SpurIntegral()
        for _ in range(100):
            i.update(0.8, 0.1, 3.0, True, True, 2.0, "hin")      # Einlenken aus 80 cm
            i.update(0.1, 0.1, 3.0, True, False, 2.0, "hin")     # Wende
        self.assertAlmostEqual(i.wert_deg, 0.0, places=6)

    def test_remembered_per_direction(self):
        i = SpurIntegral()
        for _ in range(300):
            i.update(0.05, 0.1, 3.0, True, True, 2.0, "hin")
        hin = i.wert_deg
        for _ in range(300):
            i.update(-0.05, 0.1, 3.0, True, True, 2.0, "her")
        self.assertLess(hin, 0)
        self.assertGreater(i.wert_deg, 0)
        i.update(0.0, 0.1, 3.0, False, True, 2.0, "her")           # Lenkung aus (Wende)
        self.assertEqual(i.update(0.0, 0.1, 3.0, True, True, 2.0, "hin"), hin)


    def test_shifting_after_a_centre_correction(self):
        i = SpurIntegral()
        for _ in range(300):
            i.update(0.05, 0.1, 3.0, True, True, 2.0, "hin")
        vorher = i.wert_deg
        i.verschieben(1.0)
        self.assertAlmostEqual(i.wert_deg, vorher + 1.0)
        self.assertAlmostEqual(i.gemerkt["hin"], vorher + 1.0)


class HangVorsteuerung(unittest.TestCase):
    def test_leaning_right_steers_left(self):
        self.assertAlmostEqual(hang_vorsteuerung(4.0, 0.25), -1.0)
        self.assertEqual(hang_vorsteuerung(4.0, 0.0), 0.0)
        self.assertEqual(hang_vorsteuerung(90.0, 1.0), -GRENZE_DEG)


class RadmitteLernen(unittest.TestCase):
    def _sammeln(self, r, n, mitte=-1.2, streuung=0.4):
        random.seed(5)
        for _ in range(n):
            r.probe(mitte + random.gauss(0, streuung), True, True, 2.5, 0.05, 1.0)

    def test_needs_enough_samples_and_finds_the_centre(self):
        r = Radmitte()
        r.start()
        self._sammeln(r, MIN_PROBEN - 1)
        self.assertFalse(r.ergebnis()["gueltig"])
        self._sammeln(r, 1200)
        e = r.ergebnis()
        self.assertTrue(e["gueltig"])
        self.assertAlmostEqual(e["korrektur_deg"], -1.2, delta=0.05)

    def test_only_meaningful_samples(self):
        r = Radmitte()
        self.assertFalse(r.probe(1.0, True, True, 2.5, 0.0, 0.0))          # nicht gestartet
        r.start()
        self.assertFalse(r.probe(1.0, False, True, 2.5, 0.0, 0.0))          # Lenkung aus
        self.assertFalse(r.probe(1.0, True, False, 2.5, 0.0, 0.0))          # Kurve
        self.assertFalse(r.probe(1.0, True, True, 0.2, 0.0, 0.0))           # zu langsam
        self.assertFalse(r.probe(1.0, True, True, 2.5, 0.9, 0.0))           # weit weg
        self.assertFalse(r.probe(20.0, True, True, 2.5, 0.0, 0.0))          # großer Winkel
        self.assertTrue(r.probe(1.0, True, True, 2.5, 0.1, 1.0))

    def test_scattered_data_is_not_trusted_and_shifting_after_applying(self):
        r = Radmitte()
        r.start()
        self._sammeln(r, 1200, streuung=4.0)
        self.assertFalse(r.ergebnis()["gueltig"])
        r.zuruecksetzen()
        self._sammeln(r, 1200)
        r.verschieben(-1.2)
        self.assertAlmostEqual(r.ergebnis()["median_deg"], 0.0, delta=0.05)


class RadmitteUebernehmen(unittest.TestCase):
    def test_simulator_reads_straight_as_zero_after_applying(self):
        from agripilot.gnss import SimulatorSource
        sim = SimulatorSource(lambda fix: None)
        sim.rad_versatz_deg = 1.0                # das Rad zieht 1° nach rechts
        sim.set_steer(-1.0)                      # geradeaus braucht -1° am Rad
        sim.rad_deg = sim.steer_deg
        self.assertAlmostEqual(sim.gemessener_radwinkel(), -1.0)
        sim.mess_korrektur_deg -= -1.0           # was radmitte_uebernehmen tut (Korrektur = Median -1°)
        self.assertAlmostEqual(sim.gemessener_radwinkel(), 0.0)
        sim.set_steer(0.0)                       # Befehl 0 heißt jetzt wirklich geradeaus
        self.assertAlmostEqual(sim.steer_deg + sim.rad_versatz_deg, 0.0)

    def test_phidget_output_shifts_its_measured_angle(self):
        from types import SimpleNamespace
        from agripilot.actuators import PhidgetOutput
        ausgang = PhidgetOutput.__new__(PhidgetOutput)
        ausgang.config = SimpleNamespace(mitte_versatz_deg=0.0)
        ausgang._roher_winkel = lambda: -1.2
        ausgang.mitte_korrigieren(-1.2)
        self.assertAlmostEqual(ausgang.measured_angle(), 0.0)
        self.assertAlmostEqual(ausgang.config.mitte_versatz_deg, -1.2)

    def test_api_refuses_without_a_sure_result(self):
        import tempfile
        from test_tablet_lenkung import geraet
        with tempfile.TemporaryDirectory() as ordner:
            with geraet(ordner, "client", "tablet-a") as tablet:
                self.assertEqual(tablet.post("/api/lenkung/radmitte", json={"aktion": "start"}).status_code, 200)
                self.assertTrue(tablet.get("/api/lenkung/radmitte").json()["sammelt"])
                self.assertEqual(tablet.post("/api/lenkung/radmitte", json={"aktion": "uebernehmen"}).status_code, 400)
                self.assertEqual(tablet.post("/api/lenkung/radmitte", json={"aktion": "quatsch"}).status_code, 400)
                # Mit sicherem Ergebnis und einem Ausgang, der die Mitte verschieben kann:
                anwendung = tablet.app.state.app
                ausgang = type("A", (), {"verschoben": 0.0})()
                ausgang.mitte_korrigieren = lambda grad: setattr(ausgang, "verschoben", grad)
                anwendung.engine.steering = type("S", (), {"output": ausgang})()
                anwendung.config.save = lambda: None
                for _ in range(600):
                    anwendung.engine.radmitte.probe(-0.8, True, True, 2.5, 0.0, 0.0)
                r = tablet.post("/api/lenkung/radmitte", json={"aktion": "uebernehmen"})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertAlmostEqual(ausgang.verschoben, -0.8)
                self.assertAlmostEqual(r.json()["data"]["median_deg"], 0.0)


if __name__ == "__main__":
    unittest.main()


def setUpModule():
    # Diese Tests schalten den Regeltakt selbst (_step / _control_loop), ohne eigenen Faden.
    from agripilot import actuators as _a
    _a.REGEL_FADEN = False


def tearDownModule():
    from agripilot import actuators as _a
    _a.REGEL_FADEN = True
