"""Teilbreiten schalten wie in AgOpenGPS: zwei Vorlauflinien, Mindesttempo, Vorgewende,
Hauptschalter auto/manuell/aus und je Teilbreite auto/an/aus."""

import tempfile
import time
import unittest

from agripilot.coverage import CoverageMap, build_sections
from agripilot.nmea import Fix
from test_tablet_lenkung import geraet


def bearbeitet(karte, x0, x1, y0, y1):
    karte._rasterise([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


class Vorlauflinien(unittest.TestCase):
    """Gerät fährt nach Norden (Kurs 0), Mitte bei x = 0, drei Teilbreiten zu je 2 m."""

    def setUp(self):
        self.karte = CoverageMap(0.25)
        self.sektionen = build_sections(6.0, 3)

    def schalten(self, y, tempo=2.0, **extra):
        args = dict(speed_ms=tempo, look_ahead_m=0.0, vorlauf_an_s=1.5, vorlauf_aus_s=0.5, pcc=0.9)
        args.update(extra)
        self.karte.update_auto_sections((0.0, y), 0.0, self.sektionen, **args)
        return [s.enabled for s in self.sektionen]

    def test_closes_shortly_before_worked_ground_not_at_the_long_line(self):
        bearbeitet(self.karte, -3.0, 3.0, 13.0, 40.0)          # vorn liegt Bearbeitetes
        self.assertEqual(self.schalten(10.0), [True] * 3)       # kurz (11 m) noch frei
        self.assertEqual(self.schalten(12.2), [False] * 3)      # kurz (13,2 m) und lang überdeckt

    def test_opens_early_when_leaving_worked_ground_and_does_not_flicker(self):
        bearbeitet(self.karte, -3.0, 3.0, 0.0, 20.0)            # hinter dem Bearbeiteten frei
        self.assertEqual(self.schalten(10.0), [False] * 3)
        # Lange Linie (18,5 + 3 = 21,5 m) schon frei, kurze (19,5 m) noch nicht: auf.
        for _ in range(5):                                      # und bleibt auf, Schritt für Schritt
            self.assertEqual(self.schalten(18.5), [True] * 3)

    def test_slow_or_reversing_closes_the_automatic_ones_only(self):
        self.sektionen[1].auto = False                          # fest an
        self.sektionen[1].enabled = True
        self.assertEqual(self.schalten(10.0, tempo=0.2, min_speed_ms=0.3), [False, True, False])
        self.assertEqual(self.schalten(10.0, rueckwaerts=True), [False, True, False])
        self.assertEqual(self.schalten(10.0), [True, True, True])

    def test_headland_is_measured_at_the_real_boundary(self):
        grenze = [(-50.0, 0.0), (50.0, 0.0), (50.0, 200.0), (-50.0, 200.0)]
        # Kurze Linie bei y + 1 m: 11 m von der Südgrenze - im 21-m-Vorgewende.
        self.assertEqual(self.schalten(10.0, boundary=grenze, vorgewende_m=21.0), [False] * 3)
        self.assertEqual(self.schalten(60.0, boundary=grenze, vorgewende_m=21.0), [True] * 3)
        self.assertEqual(self.schalten(10.0, boundary=grenze), [True] * 3)   # ohne Vorgewende-Aus

    def test_old_calls_behave_as_before(self):
        bearbeitet(self.karte, -3.0, -1.0, 0.0, 40.0)
        self.karte.update_auto_sections((0.0, 10.0), 0.0, self.sektionen, look_ahead_m=1.0, pcc=0.9)
        self.assertEqual([s.enabled for s in self.sektionen], [False, True, True])


class HauptschalterUndZustaende(unittest.TestCase):
    def _feld(self, client):
        app = client.app.state.app
        client.post("/api/profile", json={"width_m": 6.0, "sections": 3})
        feld = app.store.save_field(dict(name="Teilbreiten", datum_lat=50, datum_lon=10,
                                         boundary=[[-50, 0], [50, 0], [50, 200], [-50, 200]]))
        app.engine.load_field(feld["id"])
        return app.engine

    def _schritt(self, engine, y=100.0, tempo=2.0):
        engine.job = engine.job or {"id": "probe", "operation": "Spritzen"}
        engine.implement_position = (0.0, y)
        engine.heading = 0.0
        engine.fix = Fix(lat=50.0, lon=10.0, fix_quality=4, speed_ms=tempo, course_deg=0.0,
                         received_at=time.time())
        engine._update_coverage(None)
        return [s.enabled for s in engine.sections]

    def test_master_switch_and_per_section_states(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            engine = self._feld(c)
            bearbeitet(engine.coverage, -3.0, 3.0, 0.0, 200.0)   # alles schon gemacht
            self.assertEqual(self._schritt(engine), [False] * 3)  # auto: zu
            r = c.post("/api/sections/1", json={"zustand": "an"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self._schritt(engine), [False, True, False])
            c.post("/api/sections/0", json={"zustand": "aus"})
            self.assertTrue(engine.sections[0].forced_off)
            self.assertEqual(c.post("/api/sections/modus", json={"modus": "manuell"}).status_code, 200)
            self.assertEqual(self._schritt(engine), [False, True, True])   # alle an, nur "aus" bleibt zu
            c.post("/api/sections/modus", json={"modus": "aus"})
            self.assertEqual(self._schritt(engine), [False] * 3)
            state = c.get("/api/state").json()
            self.assertEqual(state["teilbreiten_modus"], "aus")
            self.assertFalse(state["auto_sections"])
            c.post("/api/sections/0", json={"zustand": "auto"})
            c.post("/api/sections/modus", json={"modus": "auto"})
            self.assertTrue(c.get("/api/state").json()["auto_sections"])

    def test_bad_values_are_refused(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            self._feld(c)
            self.assertEqual(c.post("/api/sections/modus", json={"modus": "egal"}).status_code, 400)
            self.assertEqual(c.post("/api/sections/0", json={"zustand": "halb"}).status_code, 400)
            self.assertEqual(c.post("/api/sections/9", json={"zustand": "an"}).status_code, 404)

    def test_lookahead_lines_grow_with_speed(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            engine = self._feld(c)
            self._schritt(engine, tempo=3.0)
            v = engine.teilbreiten_vorlauf()
            # Tempo × Vorlauf, eine halbe Kartenzelle (0,25 m) weniger, kein fester Meter mehr.
            self.assertAlmostEqual(v["an_m"], 3.0 * 1.0 - 0.25)
            self.assertAlmostEqual(v["aus_m"], 3.0 * 0.5 - 0.25)
            self._schritt(engine, tempo=0.5)
            self.assertAlmostEqual(engine.teilbreiten_vorlauf()["aus_m"], 0.75)   # Mindestvorlauf

    def _kreuzen(self, engine, tempo_kmh):
        """Bahn 1 (6 m, x -3..3) liegt schon; quer darüber nach Osten. Gibt (Lücke, doppelt) in m²."""
        import math
        bearbeitet(engine.coverage, -3.0, 3.0, 0.0, 200.0)
        erste = set(engine.coverage.cells)
        v, dt, t, x = tempo_kmh / 3.6, 0.1, 1000.0, -30.0
        engine.job = {"id": "kreuzen", "operation": "Spritzen"}
        engine.heading = 90.0
        schatten = CoverageMap(engine.coverage.cell_size)        # nur die zweite Bahn
        echt = engine.coverage.add_swath

        def mitschreiben(*args):
            schatten.add_swath(*args)
            return echt(*args)
        engine.coverage.add_swath = mitschreiben
        vorher = None
        while x < 30.0:
            x += v * dt
            t += dt
            engine.implement_position = (x, 100.0)
            engine.fix = Fix(lat=50.0, lon=10.0, fix_quality=4, speed_ms=v, course_deg=90.0, received_at=t)
            engine._update_coverage(vorher)
            vorher = (x, 100.0)
        c = engine.coverage.cell_size
        streifen = {(i, j) for i in range(math.floor(-20 / c), math.ceil(20 / c))
                    for j in range(math.floor(94.0 / c), math.ceil(106.0 / c))}
        engine.coverage.add_swath = echt
        luecke = len(streifen - erste - schatten.cells) * c * c
        doppelt = len(streifen & erste & schatten.cells) * c * c   # Bahn 1, über die Bahn 2 noch Mittel gab
        return luecke, doppelt, schatten.cells

    def test_crossing_worked_ground_leaves_at_most_one_cell_each_side(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            c.post("/api/profile", json={"width_m": 12.0, "sections": 6})
            engine = self._feld(c)
            c.post("/api/profile", json={"width_m": 12.0, "sections": 6})
            luecke, doppelt, _ = self._kreuzen(engine, 8.0)
            # 12 m breit: eine Kartenzelle (0,5 m) auf jeder Seite wären 6 m².
            self.assertLessEqual(luecke, 6.0)
            self.assertLessEqual(doppelt, 6.0 + 1e-6)

    def test_the_map_marks_after_the_valve_delay(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            engine = self._feld(c)
            teile = [s for s in engine.sections]
            engine.fix = Fix(lat=50.0, lon=10.0, fix_quality=4, speed_ms=2.0, course_deg=0.0, received_at=100.0)
            self.assertTrue(all(s.enabled for s in engine._gemalte_teilbreiten(teile)))
            teile[0].enabled = False                                          # Befehl: zu
            engine.fix = Fix(lat=50.0, lon=10.0, fix_quality=4, speed_ms=2.0, course_deg=0.0, received_at=100.3)
            self.assertTrue(engine._gemalte_teilbreiten(teile)[0].enabled)    # Ventil noch offen
            engine.fix = Fix(lat=50.0, lon=10.0, fix_quality=4, speed_ms=2.0, course_deg=0.0, received_at=100.81)
            self.assertFalse(engine._gemalte_teilbreiten(teile)[0].enabled)   # nach 0,5 s zu
            teile[0].enabled = True                                           # Befehl: auf
            engine.fix = Fix(lat=50.0, lon=10.0, fix_quality=4, speed_ms=2.0, course_deg=0.0, received_at=101.5)
            self.assertFalse(engine._gemalte_teilbreiten(teile)[0].enabled)   # 1,0 s Einschaltzeit
            engine.fix = Fix(lat=50.0, lon=10.0, fix_quality=4, speed_ms=2.0, course_deg=0.0, received_at=102.51)
            self.assertTrue(engine._gemalte_teilbreiten(teile)[0].enabled)


if __name__ == "__main__":
    unittest.main()
