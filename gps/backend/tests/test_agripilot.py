"""Tests für AgriPilot.

Bewusst mit `unittest` aus der Standardbibliothek: so laufen sie auf einem
frisch aufgesetzten Raspberry Pi ohne zusätzliche Installation.

    cd gps/backend && python3 -m unittest discover -s tests -v

Geprüft wird vor allem das, was auf dem Feld Geld kostet, wenn es falsch ist:
Flächen, Vorzeichen der Abweichung, und die Bedingungen, unter denen die
Lenkautomatik einschalten darf.
"""

import asyncio
import math
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agripilot import geo, nmea, sync
from agripilot.coverage import CoverageMap, build_sections
from agripilot.guidance import GuidanceLine, HeadingFilter, VehicleProfile, lightbar_offset
from agripilot.storage import Storage


def _fix(**werte):
    """Ein Fix mit dem Nötigsten für die Führung."""
    from agripilot.nmea import Fix
    grund = {"lat": 48.0, "lon": 11.0, "fix_quality": 4, "speed_ms": 0.0}
    grund.update(werte)
    return Fix(**grund)


def sentence(body: str) -> str:
    crc = 0
    for ch in body:
        crc ^= ord(ch)
    return f"${body}*{crc:02X}"


class GeoTest(unittest.TestCase):
    def test_local_plane_roundtrip(self):
        plane = geo.LocalPlane(48.1372, 11.5756)
        for lat, lon in [(48.1372, 11.5756), (48.1400, 11.5800), (48.1300, 11.5700)]:
            east, north = plane.to_local(lat, lon)
            back_lat, back_lon = plane.to_wgs(east, north)
            self.assertAlmostEqual(lat, back_lat, places=9)
            self.assertAlmostEqual(lon, back_lon, places=9)

    def test_local_plane_scale_matches_the_ellipsoid(self):
        """Ein Grad Breite sind bei 48° rund 111,24 km - das muss stimmen."""
        plane = geo.LocalPlane(48.0, 11.0)
        _, north = plane.to_local(49.0, 11.0)
        self.assertAlmostEqual(north / 1000.0, 111.24, delta=0.1)
        east, _ = plane.to_local(48.0, 12.0)
        self.assertAlmostEqual(east / 1000.0, 74.63, delta=0.1)

    def test_distances_barely_depend_on_the_datum(self):
        """Zwei Geräte am selben Feld setzen den Bezugspunkt nie exakt gleich.

        Die gemessene Strecke darf sich dadurch nicht bewegen - sonst lägen die
        Spuren zweier Traktoren auf demselben Feld auseinander.
        """
        a, b = (48.100, 11.500), (48.109, 11.513)      # 1,4 km auseinander
        first = geo.distance(geo.LocalPlane(48.095, 11.495).to_local(*a),
                             geo.LocalPlane(48.095, 11.495).to_local(*b))
        second = geo.distance(geo.LocalPlane(48.105, 11.505).to_local(*a),
                              geo.LocalPlane(48.105, 11.505).to_local(*b))
        self.assertLess(abs(first - second), 0.05)     # unter 5 cm auf 1,4 km

    def test_area_survives_the_round_trip(self):
        plane = geo.LocalPlane(48.1, 11.5)
        square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
        back = [plane.to_local(*plane.to_wgs(e, n)) for e, n in square]
        self.assertAlmostEqual(geo.polygon_area(back), 10_000.0, places=3)

    def test_polygon_area_hectares(self):
        square = [(0, 0), (100, 0), (100, 100), (0, 100)]
        self.assertAlmostEqual(geo.polygon_area(square), 10_000.0)
        # Umgekehrter Umlaufsinn darf nichts ändern
        self.assertAlmostEqual(geo.polygon_area(square[::-1]), 10_000.0)

    def test_cross_track_sign_is_positive_to_the_right(self):
        _, _, lateral = geo.project_on_segment((5.0, 50.0), (0.0, 0.0), (0.0, 100.0))
        self.assertAlmostEqual(lateral, 5.0)
        _, _, lateral = geo.project_on_segment((-5.0, 50.0), (0.0, 0.0), (0.0, 100.0))
        self.assertAlmostEqual(lateral, -5.0)

    def test_point_in_polygon(self):
        field = [(0, 0), (100, 0), (100, 50), (0, 50)]
        self.assertTrue(geo.point_in_polygon((50, 25), field))
        self.assertFalse(geo.point_in_polygon((150, 25), field))

    def test_simplify_keeps_shape(self):
        points = [(x, 0.0) for x in range(0, 100)] + [(99.0, y) for y in range(0, 50)]
        thinned = geo.simplify(points, 0.25)
        self.assertLess(len(thinned), 10)
        self.assertEqual(thinned[0], points[0])
        self.assertEqual(thinned[-1], points[-1])

    def test_heading_and_angle_difference(self):
        self.assertAlmostEqual(geo.heading_deg((0, 0), (0, 10)), 0.0)
        self.assertAlmostEqual(geo.heading_deg((0, 0), (10, 0)), 90.0)
        self.assertAlmostEqual(geo.angle_difference(1.0, 359.0), 2.0)
        self.assertAlmostEqual(geo.angle_difference(359.0, 1.0), -2.0)


class NmeaTest(unittest.TestCase):
    def test_gga_position_and_quality(self):
        parser = nmea.NmeaParser()
        fix = parser.feed(nmea.build_gga(48.1234567, 11.7654321, 540.2, 4, 18, 0.6))
        self.assertIsNotNone(fix)
        self.assertAlmostEqual(fix.lat, 48.1234567, places=6)
        self.assertAlmostEqual(fix.lon, 11.7654321, places=6)
        self.assertEqual(fix.fix_label, "RTK fix")
        self.assertEqual(fix.rank, 4)

    def test_broken_checksum_is_dropped(self):
        parser = nmea.NmeaParser()
        self.assertIsNone(parser.feed("$GPGGA,120000,4807.038,N,01131.000,E,4,12,0.9,540,M,,M,,*00"))

    def test_speed_from_vtg_and_rmc(self):
        parser = nmea.NmeaParser()
        parser.feed(sentence("GPVTG,123.4,T,,M,4.59,N,8.5,K,A"))
        self.assertAlmostEqual(parser.fix.speed_ms, 8.5 / 3.6, places=3)
        self.assertAlmostEqual(parser.fix.course_deg, 123.4)

    def test_two_digit_year(self):
        parser = nmea.NmeaParser()
        fix = parser.feed(sentence("GPRMC,120000.00,A,4807.038,N,01131.000,E,5.5,84.4,230426,,,A"))
        self.assertEqual(fix.utc.year, 2026)

    def test_accuracy_prefers_gst(self):
        parser = nmea.NmeaParser()
        parser.feed(nmea.build_gga(48.0, 11.0, 500.0, 4))
        parser.feed(sentence("GPGST,120000.00,0.9,0.02,0.01,15.2,0.014,0.011,0.030"))
        self.assertAlmostEqual(parser.fix.accuracy_m, math.hypot(0.014, 0.011), places=4)

    def test_dual_antenna_heading(self):
        parser = nmea.NmeaParser()
        parser.feed(sentence("GPHDT,271.5,T"))
        self.assertAlmostEqual(parser.fix.heading_deg, 271.5)


class GuidanceTest(unittest.TestCase):
    def setUp(self):
        self.profile = VehicleProfile(width_m=6.0)
        self.line = GuidanceLine("ab", [(0, 0), (0, 100)], self.profile.spacing_m)

    def test_pass_number_and_cross_track(self):
        state = self.line.solve((0.2, 50.0), 0.0, 3.0, self.profile)
        self.assertEqual(state.pass_number, 0)
        self.assertAlmostEqual(state.cross_track_m, 0.2)

        state = self.line.solve((6.3, 50.0), 0.0, 3.0, self.profile)
        self.assertEqual(state.pass_number, 1)
        self.assertAlmostEqual(state.cross_track_m, 0.3)

    def test_steering_pulls_towards_the_line(self):
        right = self.line.solve((0.5, 50.0), 0.0, 3.0, self.profile)
        self.assertLess(right.steer_angle_deg, 0)      # rechts daneben -> links lenken
        left = self.line.solve((-0.5, 50.0), 0.0, 3.0, self.profile)
        self.assertGreater(left.steer_angle_deg, 0)

    def test_reverse_pass_flips_the_side(self):
        """Auf der Rückfahrt ist 'rechts' aus Fahrersicht die andere Seite."""
        state = self.line.solve((0.3, 50.0), 180.0, 3.0, self.profile)
        self.assertTrue(state.reversed_direction)
        self.assertAlmostEqual(state.cross_track_m, -0.3)
        self.assertAlmostEqual(state.target_heading_deg, 180.0)

    def test_steer_angle_is_limited(self):
        state = self.line.solve((25.0, 50.0), 90.0, 3.0, self.profile)
        self.assertLessEqual(abs(state.steer_angle_deg), self.profile.max_steer_deg)

    def test_nudge_moves_the_whole_pattern(self):
        before = self.line.solve((0.0, 50.0), 0.0, 3.0, self.profile).cross_track_m
        self.line.nudge_m = 0.10
        after = self.line.solve((0.0, 50.0), 0.0, 3.0, self.profile).cross_track_m
        self.assertAlmostEqual(after - before, -0.10)

    def test_overlap_reduces_spacing(self):
        profile = VehicleProfile(width_m=6.0, overlap_m=0.5)
        self.assertAlmostEqual(profile.spacing_m, 5.5)

    def test_curve_follows_the_recorded_track(self):
        points = [(0.0, float(y)) for y in range(0, 60, 5)] + \
                 [(float(x), 55.0) for x in range(5, 60, 5)]
        curve = GuidanceLine("curve", points, 6.0)
        state = curve.solve((0.4, 20.0), 0.0, 3.0, VehicleProfile(width_m=6.0))
        self.assertEqual(state.pass_number, 0)
        self.assertAlmostEqual(state.cross_track_m, 0.4, places=2)

    def test_tool_position_uses_antenna_offsets(self):
        profile = VehicleProfile(antenna_forward_m=1.5, tool_trailing_m=2.0)
        # Nach Norden fahrend liegt das Gerät 3,5 m hinter der Antenne
        east, north = profile.tool_position((0.0, 100.0), 0.0)
        self.assertAlmostEqual(east, 0.0, places=6)
        self.assertAlmostEqual(north, 96.5, places=6)
        # Nach Osten fahrend entsprechend westlich davon
        east, north = profile.tool_position((0.0, 100.0), 90.0)
        self.assertAlmostEqual(east, -3.5, places=6)
        self.assertAlmostEqual(north, 100.0, places=6)

    def test_lightbar_scaling(self):
        self.assertEqual(lightbar_offset(0.0), 0)
        self.assertEqual(lightbar_offset(0.10), 2)
        self.assertEqual(lightbar_offset(-0.10), -2)
        self.assertEqual(lightbar_offset(99.0), 10)     # begrenzt

    def test_heading_filter_holds_value_when_slow(self):
        heading = HeadingFilter()
        heading.update(90.0, None, 3.0)
        self.assertAlmostEqual(heading.value, 90.0)
        heading.update(270.0, None, 0.1)                 # zu langsam: ignorieren
        self.assertAlmostEqual(heading.value, 90.0)
        heading.update(None, 12.0, 0.0)                  # echter Kurs schlägt alles
        self.assertAlmostEqual(heading.value, 12.0)

    def test_heading_filter_uses_the_yaw_rate_when_crawling(self):
        """Am Vorgewende steht der Kurs sonst still, obwohl der Traktor dreht."""
        heading = HeadingFilter()
        heading.update(90.0, None, 3.0)
        heading.update(None, None, 0.1, yaw_rate_deg_s=20.0, dt=0.5)
        self.assertAlmostEqual(heading.value, 100.0, places=6)

    def test_heading_filter_takes_the_short_way(self):
        heading = HeadingFilter(smoothing=0.5)
        heading.value = 359.0
        heading.update(1.0, None, 3.0)
        self.assertAlmostEqual(heading.value, 0.0, places=6)


class SpeedGainTest(unittest.TestCase):
    """Absenkung der Lenkaggressivität bei Tempo (Cerea RWFVLIMIT)."""

    def _profil(self, **werte):
        from agripilot.guidance import VehicleProfile
        return VehicleProfile(**werte)

    def test_off_by_default(self):
        from agripilot.guidance import speed_gain_factor
        profil = self._profil()
        for kmh in (0.0, 5.0, 12.0, 25.0):
            self.assertEqual(speed_gain_factor(kmh / 3.6, profil), 1.0)

    def test_below_the_threshold_nothing_changes(self):
        from agripilot.guidance import speed_gain_factor
        profil = self._profil(speed_gain_limit_kmh=8.0, speed_gain_rest=0.5)
        self.assertEqual(speed_gain_factor(7.9 / 3.6, profil), 1.0)

    def test_linear_between_threshold_and_double(self):
        from agripilot.guidance import speed_gain_factor
        profil = self._profil(speed_gain_limit_kmh=8.0, speed_gain_rest=0.5)
        # Bei 12 km/h ist die Hälfte des Wegs von 8 auf 16 zurückgelegt.
        self.assertAlmostEqual(speed_gain_factor(12.0 / 3.6, profil), 0.75, places=6)
        self.assertAlmostEqual(speed_gain_factor(16.0 / 3.6, profil), 0.5, places=6)

    def test_it_does_not_keep_falling_above_double(self):
        """Sonst stünde die Lenkung bei hohem Tempo praktisch still."""
        from agripilot.guidance import speed_gain_factor
        profil = self._profil(speed_gain_limit_kmh=8.0, speed_gain_rest=0.5)
        self.assertAlmostEqual(speed_gain_factor(40.0 / 3.6, profil), 0.5, places=6)

    def test_a_fast_machine_steers_more_gently(self):
        """Der eigentliche Zweck: gleicher Fehler, weniger Ausschlag."""
        from agripilot.guidance import GuidanceLine
        profil = self._profil(speed_gain_limit_kmh=6.0, speed_gain_rest=0.4)
        linie = GuidanceLine("ab", [(0.0, 0.0), (0.0, 100.0)], profil.spacing_m)
        langsam = linie.solve((0.5, 10.0), 0.0, 4.0 / 3.6, profil).steer_angle_deg
        schnell = linie.solve((0.5, 10.0), 0.0, 14.0 / 3.6, profil).steer_angle_deg
        self.assertLess(abs(schnell), abs(langsam))


class SectionPccTest(unittest.TestCase):
    """Abschaltschwelle nach Überdeckungsanteil (Cerea pcc)."""

    def setUp(self):
        from agripilot.coverage import CoverageMap, build_sections
        self.karte = CoverageMap(cell_size=0.5)
        self.sektionen = build_sections(6.0, 3)   # je 2 m breit

    def _bedecke(self, x0, x1, y0=0.0, y1=40.0):
        """Einen Streifen als bearbeitet markieren."""
        self.karte._rasterise([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    def test_a_section_barely_touching_worked_ground_stays_on(self):
        """Ein Punkt in der Mitte hätte hier schon abgeschaltet - zu früh."""
        # Nur der linke Rand des linken Teilstücks liegt auf bearbeitetem Boden.
        self._bedecke(-3.0, -2.6)
        self.karte.update_auto_sections((0.0, 10.0), 0.0, self.sektionen,
                                        look_ahead_m=1.0, pcc=0.9)
        self.assertTrue(self.sektionen[0].enabled)

    def test_a_fully_covered_section_closes(self):
        self._bedecke(-3.0, -1.0)
        self.karte.update_auto_sections((0.0, 10.0), 0.0, self.sektionen,
                                        look_ahead_m=1.0, pcc=0.9)
        self.assertFalse(self.sektionen[0].enabled)
        self.assertTrue(self.sektionen[1].enabled)

    def test_a_lower_threshold_closes_earlier(self):
        self._bedecke(-3.0, -2.0)     # die Hälfte des linken Teilstücks
        self.karte.update_auto_sections((0.0, 10.0), 0.0, self.sektionen,
                                        look_ahead_m=1.0, pcc=0.9)
        self.assertTrue(self.sektionen[0].enabled)
        self.karte.update_auto_sections((0.0, 10.0), 0.0, self.sektionen,
                                        look_ahead_m=1.0, pcc=0.4)
        self.assertFalse(self.sektionen[0].enabled)

    def test_the_boundary_does_not_wait_for_the_threshold(self):
        """Draußen zu arbeiten ist kein Schönheitsfehler - das schaltet sofort ab."""
        grenze = [(-2.5, 0.0), (2.5, 0.0), (2.5, 40.0), (-2.5, 40.0)]
        self.karte.update_auto_sections((0.0, 10.0), 0.0, self.sektionen,
                                        look_ahead_m=1.0, boundary=grenze, pcc=0.9)
        self.assertFalse(self.sektionen[0].enabled)   # ragt links hinaus
        self.assertTrue(self.sektionen[1].enabled)    # liegt ganz drin
        self.assertFalse(self.sektionen[2].enabled)   # ragt rechts hinaus

    def test_the_driver_keeps_the_last_word(self):
        self._bedecke(-3.0, 3.0)
        for sektion in self.sektionen:
            sektion.auto = False
            sektion.enabled = True
        self.karte.update_auto_sections((0.0, 10.0), 0.0, self.sektionen, pcc=0.9)
        self.assertTrue(all(s.enabled for s in self.sektionen))


class LatencyTest(unittest.TestCase):
    """Aktor-/GNSS-Latenz: geführt wird auf den Punkt, an dem die Maschine sein wird."""

    def _engine(self, latenz_ms):
        import tempfile as tf
        from agripilot import config as config_module
        from agripilot.engine import Engine
        from agripilot.storage import Storage
        ordner = tf.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "e.db"))
        self.addCleanup(store.close)
        config = config_module.load("/kein-solcher-pfad.yaml")
        motor = Engine(config, store)
        motor.update_profile({"actuator_latency_ms": latenz_ms,
                              "antenna_forward_m": 0.0, "tool_trailing_m": 0.0})
        return motor

    def test_zero_latency_guides_on_the_tool(self):
        from agripilot.guidance import GuidanceLine
        motor = self._engine(0)
        motor.line = GuidanceLine("ab", [(0.0, 0.0), (0.0, 100.0)], motor.profile.spacing_m)
        motor.tool_position = (0.4, 10.0)
        motor.heading = 0.0
        motor._update_guidance(_fix(speed_ms=3.0))
        self.assertAlmostEqual(motor.guidance.distance_along_m, 10.0, places=3)

    def test_latency_moves_the_guidance_point_ahead(self):
        """240 ms bei 3 m/s sind gut 70 cm - in einer Kurve genau der Nachlauf."""
        from agripilot.guidance import GuidanceLine
        motor = self._engine(240)
        motor.line = GuidanceLine("ab", [(0.0, 0.0), (0.0, 100.0)], motor.profile.spacing_m)
        motor.tool_position = (0.4, 10.0)
        motor.heading = 0.0
        motor._update_guidance(_fix(speed_ms=3.0))
        self.assertAlmostEqual(motor.guidance.distance_along_m, 10.72, places=2)

    def test_standing_still_nothing_is_projected(self):
        from agripilot.guidance import GuidanceLine
        motor = self._engine(240)
        motor.line = GuidanceLine("ab", [(0.0, 0.0), (0.0, 100.0)], motor.profile.spacing_m)
        motor.tool_position = (0.4, 10.0)
        motor.heading = 0.0
        motor._update_guidance(_fix(speed_ms=0.0))
        self.assertAlmostEqual(motor.guidance.distance_along_m, 10.0, places=3)


class CoverageTest(unittest.TestCase):
    def test_area_is_exact_for_a_straight_pass(self):
        coverage = CoverageMap(0.5)
        sections = build_sections(6.0, 1)
        previous = (0.0, 0.0)
        for i in range(1, 201):
            current = (0.0, i * 0.5)
            coverage.add_swath(previous, current, 0.0, sections)
            previous = current
        self.assertAlmostEqual(coverage.area_m2, 600.0, places=1)   # 100 m x 6 m

    def test_adjacent_passes_leave_no_gap(self):
        coverage = CoverageMap(0.5)
        sections = build_sections(6.0, 1)
        for lap in range(3):
            previous = (lap * 6.0, 0.0)
            for i in range(1, 201):
                current = (lap * 6.0, i * 0.5)
                coverage.add_swath(previous, current, 0.0, sections)
                previous = current
        self.assertAlmostEqual(coverage.area_m2, 1800.0, places=1)
        self.assertLess(coverage.overlap_percent, 0.1)

    def test_overlap_is_measured(self):
        coverage = CoverageMap(0.5)
        sections = build_sections(6.0, 1)
        for east in (0.0, 3.0):                       # halbe Breite versetzt
            previous = (east, 0.0)
            for i in range(1, 201):
                current = (east, i * 0.5)
                coverage.add_swath(previous, current, 0.0, sections)
                previous = current
        self.assertAlmostEqual(coverage.area_m2, 900.0, places=1)
        self.assertAlmostEqual(coverage.overlap_m2, 300.0, places=1)
        self.assertAlmostEqual(coverage.overlap_percent, 25.0, places=1)

    def test_sections_split_the_width(self):
        sections = build_sections(6.0, 3)
        self.assertEqual([s.left_m for s in sections], [-3.0, -1.0, 1.0])
        self.assertEqual([s.right_m for s in sections], [-1.0, 1.0, 3.0])

    def test_auto_sections_switch_off_over_worked_ground(self):
        coverage = CoverageMap(0.5)
        sections = build_sections(6.0, 3)
        previous = (0.0, 0.0)
        for i in range(1, 201):
            current = (0.0, i * 0.5)
            coverage.add_swath(previous, current, 0.0, sections)
            previous = current
        # Zweite Fahrt nur 2 m versetzt: die überlappende Sektion muss zugehen
        coverage.update_auto_sections((2.0, 50.0), 0.0, sections, speed_ms=0.0)
        self.assertFalse(sections[0].enabled)         # linke Sektion liegt im Alten
        self.assertTrue(sections[2].enabled)          # rechte auf frischem Boden

    def test_auto_sections_respect_the_boundary(self):
        coverage = CoverageMap(0.5)
        sections = build_sections(6.0, 3)
        boundary = [(0, 0), (4, 0), (4, 100), (0, 100)]
        coverage.update_auto_sections((3.5, 50.0), 0.0, sections,
                                      speed_ms=0.0, boundary=boundary)
        self.assertFalse(sections[2].enabled)         # ragt über die Grenze hinaus

    def test_pack_unpack_and_merge(self):
        first, second = CoverageMap(0.5), CoverageMap(0.5)
        sections = build_sections(4.0, 1)
        first.add_swath((0, 0), (0, 20), 0.0, sections)
        second.add_swath((4, 0), (4, 20), 0.0, sections)
        restored = CoverageMap.unpack(first.pack())
        self.assertEqual(restored.cells, first.cells)
        first.merge(second)
        self.assertAlmostEqual(first.area_m2, 160.0, places=1)

    def test_merge_refuses_different_cell_sizes(self):
        with self.assertRaises(ValueError):
            CoverageMap(0.5).merge(CoverageMap(1.0))


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Storage(os.path.join(self.dir.name, "test.db"))

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_field_and_line_roundtrip(self):
        field = self.store.save_field({
            "name": "Oberes Feld", "datum_lat": 48.1, "datum_lon": 11.5,
            "boundary": [[0, 0], [100, 0], [100, 50], [0, 50]], "area_ha": 0.5,
        })
        self.assertEqual(self.store.get_field(field["id"])["boundary"][2], [100, 50])
        line = self.store.save_line({
            "field_id": field["id"], "name": "AB1", "mode": "ab",
            "points": [[0, 0], [0, 200]], "spacing_m": 6.0,
        })
        self.assertEqual(self.store.list_lines(field["id"])[0]["id"], line["id"])

    def test_soft_delete_hides_but_keeps_the_row(self):
        field = self.store.save_field({"name": "Weg", "datum_lat": 48.0, "datum_lon": 11.0})
        self.store.delete_field(field["id"])
        self.assertEqual(self.store.list_fields(), [])
        # Für den Abgleich muss die Löschung übertragbar bleiben
        self.assertTrue(any(r["id"] == field["id"] and r["deleted"] == 1
                            for r in self.store.changes_since("fields", 0)))

    def test_job_lifecycle(self):
        field = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 11.0})
        job = self.store.start_job(field["id"], "pi-1", "Fendt", "Säen")
        self.store.update_job(job["id"], area_ha=2.5, ended_at=time.time())
        self.assertAlmostEqual(self.store.get_job(job["id"])["area_ha"], 2.5)
        self.store.add_track_points(job["id"], [(1.0, 48.0, 11.0, 500, 3.0, 0.0, 4, 0.01)])
        self.assertEqual(len(self.store.track_points(job["id"])), 1)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.master = Storage(os.path.join(self.dir.name, "master.db"))
        self.client = Storage(os.path.join(self.dir.name, "client.db"))

    def tearDown(self):
        self.master.close()
        self.client.close()
        self.dir.cleanup()

    def test_records_travel_between_machines(self):
        field = self.master.save_field({"name": "Acker", "datum_lat": 48.0, "datum_lon": 11.0})
        self.master.save_line({"field_id": field["id"], "name": "AB", "mode": "ab",
                               "points": [[0, 0], [0, 100]], "spacing_m": 6.0})
        applied = sync.apply_changes(self.client, sync.collect_changes(self.master, 0))
        self.assertEqual(applied["fields"], 1)
        self.assertEqual(self.client.list_fields()[0]["name"], "Acker")

    def test_newest_change_wins(self):
        field = self.master.save_field({"name": "Alt", "datum_lat": 48.0, "datum_lon": 11.0})
        sync.apply_changes(self.client, sync.collect_changes(self.master, 0))
        time.sleep(0.01)
        self.client.save_field({**self.client.get_field(field["id"]), "name": "Neu"})
        sync.apply_changes(self.master, sync.collect_changes(self.client, 0))
        self.assertEqual(self.master.get_field(field["id"])["name"], "Neu")

    def test_coverage_of_two_tractors_is_combined(self):
        field = self.master.save_field({"name": "Gross", "datum_lat": 48.0, "datum_lon": 11.0})
        sections = build_sections(6.0, 1)
        for store, east in ((self.master, 0.0), (self.client, 6.0)):
            job = store.start_job(field["id"], "pi", "T", "Säen")
            coverage = CoverageMap(0.5)
            previous = (east, 0.0)
            for i in range(1, 101):
                current = (east, float(i))
                coverage.add_swath(previous, current, 0.0, sections)
                previous = current
            store.update_job(job["id"], coverage=coverage.pack(), ended_at=time.time())
        sync.apply_changes(self.master, sync.collect_changes(self.client, 0))
        merged = sync.merge_field_coverage(self.master, field["id"])
        self.assertAlmostEqual(merged.area_m2, 1200.0, places=1)


class CorrectionsTest(unittest.IsolatedAsyncioTestCase):
    """Woher die RTK-Korrekturen kommen - Dienst, eigene Basis oder Funkmodem."""

    def _config(self, **werte):
        from agripilot import config as config_module
        config = config_module.load("/kein-solcher-pfad.yaml")
        for schluessel, wert in werte.items():
            setattr(config.corrections, schluessel, wert)
        return config

    def test_source_choice_picks_the_right_client(self):
        from agripilot.ntrip import (NtripClient, SerialRtcmSource, TcpRtcmSource,
                                     build_corrections)
        sink = []
        self.assertIsNone(build_corrections(self._config(source="aus"), sink.append))
        self.assertIsInstance(
            build_corrections(self._config(source="ntrip"), sink.append), NtripClient)
        self.assertIsInstance(
            build_corrections(self._config(source="tcp"), sink.append), TcpRtcmSource)
        self.assertIsInstance(
            build_corrections(self._config(source="serial"), sink.append),
            SerialRtcmSource)

    def test_enabled_follows_the_source(self):
        self.assertFalse(self._config(source="aus").corrections.enabled)
        self.assertTrue(self._config(source="tcp").corrections.enabled)

    def test_old_configuration_files_still_work(self):
        """Dateien mit dem früheren ntrip-Abschnitt dürfen nicht stehen bleiben."""
        from agripilot import config as config_module
        with tempfile.TemporaryDirectory() as ordner:
            pfad = os.path.join(ordner, "alt.yaml")
            with open(pfad, "w", encoding="utf-8") as datei:
                datei.write("ntrip:\n  enabled: true\n  host: 192.168.10.5\n"
                            "  mountpoint: BASIS1\n  username: u\n  password: p\n")
            config = config_module.load(pfad)
            self.assertEqual(config.corrections.source, "ntrip")
            self.assertEqual(config.corrections.host, "192.168.10.5")
            self.assertEqual(config.corrections.mountpoint, "BASIS1")

            with open(pfad, "w", encoding="utf-8") as datei:
                datei.write("ntrip:\n  enabled: false\n  host: alt.example\n")
            self.assertEqual(config_module.load(pfad).corrections.source, "aus")

    def test_password_never_leaves_through_the_interface(self):
        config = self._config(source="ntrip", password="geheim")
        self.assertEqual(config.to_dict()["corrections"]["password"], "***")

    async def test_raw_stream_from_an_own_base_arrives(self):
        """Eine selbst gebaute Basis öffnet oft nur einen Port ohne Anmeldung."""
        from agripilot.ntrip import TcpRtcmSource

        empfangen = []
        async def basis(reader, writer):
            writer.write(b"\xd3\x00\x13RTCM")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(basis, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        quelle = TcpRtcmSource("127.0.0.1", port, empfangen.append)
        aufgabe = asyncio.create_task(quelle.run())
        for _ in range(40):
            await asyncio.sleep(0.05)
            if empfangen:
                break
        await quelle.stop()
        aufgabe.cancel()
        server.close()
        self.assertEqual(empfangen, [b"\xd3\x00\x13RTCM"])
        self.assertGreater(quelle.bytes_received, 0)
        self.assertIn(str(port), quelle.status)

    async def test_error_status_still_names_the_source(self):
        """Bricht die Verbindung ab, muss dranstehen, welche Quelle schweigt."""
        from agripilot.ntrip import TcpRtcmSource

        quelle = TcpRtcmSource("127.0.0.1", 1, lambda _chunk: None, "Hofbasis")
        quelle._stoerung(ConnectionError("Gegenstelle hat beendet"))
        self.assertIn("Hofbasis", quelle.status)
        self.assertIn("127.0.0.1:1", quelle.status)
        self.assertIn("Gegenstelle hat beendet", quelle.status)

    async def test_relay_passes_the_stream_to_the_other_tractors(self):
        from agripilot.ntrip import RtcmRelay, RtcmRelayClient

        empfangen = []
        verteiler = RtcmRelay(0)
        self.assertTrue(await verteiler.start())
        port = verteiler.server.sockets[0].getsockname()[1]
        traktor = RtcmRelayClient("127.0.0.1", port, empfangen.append)
        aufgabe = asyncio.create_task(traktor.run())
        for _ in range(40):
            await asyncio.sleep(0.05)
            if verteiler.client_count:
                break
        verteiler.broadcast(b"\xd3KORREKTUR")
        for _ in range(40):
            await asyncio.sleep(0.05)
            if empfangen:
                break
        self.assertEqual(empfangen, [b"\xd3KORREKTUR"])
        await traktor.stop()
        aufgabe.cancel()
        await verteiler.stop()


class ConfigFileTest(unittest.TestCase):
    """Die Konfigurationsdatei - die einzige Datei, die von Hand geschrieben wird.

    Geprüft wird der eigene Leser aus ``yamlfile``, denn genau der springt ein,
    wenn PyYAML fehlt: auf einem frisch aufgesetzten Rechner, beim Start mit dem
    System-Python statt aus dem venv, oder nach einem Update, das das venv
    ersetzt hat.
    """

    def test_sections_and_values_come_back_as_written(self):
        from agripilot import yamlfile
        gelesen = yamlfile.loads(
            "gnss:\n"
            "  source: serial      # Empfänger am USB-Anschluss\n"
            "  port: COM3\n"
            "  baudrate: 115200\n"
            "\n"
            "# Korrekturdaten von der eigenen Basis\n"
            "corrections:\n"
            "  source: ntrip\n"
            "  host: 192.168.10.5\n"
            "  send_gga: false\n"
            "imu:\n"
            "  uid: ''\n"
            "  roll_sign: -1.0\n"
            "  terrain_compensation: true\n")
        self.assertEqual(gelesen["gnss"], {"source": "serial", "port": "COM3",
                                           "baudrate": 115200})
        self.assertEqual(gelesen["corrections"]["host"], "192.168.10.5")
        self.assertIs(gelesen["corrections"]["send_gga"], False)
        self.assertEqual(gelesen["imu"], {"uid": "", "roll_sign": -1.0,
                                          "terrain_compensation": True})

    def test_quoted_values_stay_text(self):
        """Ein Mountpoint "1234" ist ein Name, keine Zahl - und ein Passwort auch."""
        from agripilot import yamlfile
        gelesen = yamlfile.loads(
            'corrections:\n'
            '  mountpoint: "1234"\n'
            '  password: "geheim # kein Kommentar"\n'
            "  username: 'true'\n")
        self.assertEqual(gelesen["corrections"]["mountpoint"], "1234")
        self.assertEqual(gelesen["corrections"]["password"], "geheim # kein Kommentar")
        self.assertEqual(gelesen["corrections"]["username"], "true")

    def test_written_file_reads_back_unchanged(self):
        from agripilot import yamlfile
        original = {
            "gnss": {"source": "serial", "port": "COM3", "baudrate": 115200},
            "corrections": {"mountpoint": "1234", "password": "ge:heim",
                            "send_gga": False, "port": 2101},
            "imu": {"uid": "", "roll_sign": -1.0},
            "server": {"data_dir": r"C:\ProgramData\AgriPilot"},
        }
        self.assertEqual(yamlfile.loads(yamlfile.dumps(original)), original)

    def test_a_broken_file_says_where_it_hurts(self):
        """Lieber eine Zeilennummer als eine halb verstandene Konfiguration."""
        from agripilot import yamlfile
        with self.assertRaises(yamlfile.ConfigSyntaxError) as gefangen:
            yamlfile.loads("gnss:\n  source serial\n")
        self.assertIn("Zeile 2", str(gefangen.exception))

    def test_the_config_loads_without_pyyaml(self):
        """Ohne PyYAML muss dieselbe Datei gelesen werden - nicht als JSON."""
        from agripilot import config as config_module
        with tempfile.TemporaryDirectory() as ordner:
            pfad = os.path.join(ordner, "config.yaml")
            with open(pfad, "w", encoding="utf-8") as datei:
                datei.write("gnss:\n  source: serial\n  port: COM3\n"
                            "steering:\n  enabled: true\n")
            ohne_yaml = {name: modul for name, modul in sys.modules.items()}
            sys.modules["yaml"] = None  # importiert wie ein fehlendes Paket
            try:
                config = config_module.load(pfad)
            finally:
                sys.modules.clear()
                sys.modules.update(ohne_yaml)
            self.assertEqual(config.gnss.source, "serial")
            self.assertEqual(config.gnss.port, "COM3")
            self.assertTrue(config.steering.enabled)

    def test_a_broken_config_names_the_file(self):
        from agripilot import config as config_module
        with tempfile.TemporaryDirectory() as ordner:
            pfad = os.path.join(ordner, "config.yaml")
            with open(pfad, "w", encoding="utf-8") as datei:
                datei.write("gnss:\n\tport: COM3\n")
            with self.assertRaises(ValueError) as gefangen:
                config_module.load(pfad)
            self.assertIn("config.yaml", str(gefangen.exception))


class SteeringTest(unittest.TestCase):
    def setUp(self):
        from agripilot.actuators import NullOutput
        from agripilot.config import SteeringConfig
        from agripilot.steering import SteeringController
        self.config = SteeringConfig(enabled=True, output="none")
        self.output = NullOutput()
        self.output.ready = True
        self.controller = SteeringController(self.config, self.output)
        self.controller.max_rate_deg_s = 1000.0    # Ratenlimit hier nicht im Weg

    def _fix(self, quality=4, speed=3.0):
        return nmea.Fix(lat=48.0, lon=11.0, fix_quality=quality,
                        speed_ms=speed, received_at=time.time())

    def _guidance(self, cross_track=0.05, steer=-2.0):
        from agripilot.guidance import GuidanceState
        return GuidanceState(active=True, cross_track_m=cross_track, steer_angle_deg=steer)

    def test_does_not_steer_until_armed(self):
        command = self.controller.update(self._guidance(), self._fix())
        self.assertFalse(command.engaged)

    def test_steers_when_everything_is_in_order(self):
        self.controller.arm()
        command = self.controller.update(self._guidance(), self._fix())
        self.assertTrue(command.engaged)
        self.assertAlmostEqual(command.angle_deg, -2.0)

    def test_refuses_without_rtk(self):
        self.controller.arm()
        command = self.controller.update(self._guidance(), self._fix(quality=1))
        self.assertFalse(command.engaged)
        self.assertIn("RTK", command.reason)

    def test_refuses_when_too_far_off_the_line(self):
        self.controller.arm()
        command = self.controller.update(self._guidance(cross_track=3.0), self._fix())
        self.assertFalse(command.engaged)

    def test_refuses_when_standing_still(self):
        self.controller.arm()
        self.assertFalse(self.controller.update(self._guidance(), self._fix(speed=0.0)).engaged)

    def test_refuses_on_stale_position(self):
        self.controller.arm()
        stale = self._fix()
        stale.received_at = time.time() - 5.0
        self.assertFalse(self.controller.update(self._guidance(), stale).engaged)

    def test_driver_override_disarms(self):
        from agripilot.actuators import OutputFeedback
        self.controller.arm()
        self.output.feedback = OutputFeedback(driver_override=True,
                                              received_at=time.time())
        command = self.controller.update(self._guidance(), self._fix())
        self.assertFalse(command.engaged)
        self.assertFalse(self.controller.armed)      # bleibt aus, bis neu geschärft

    def test_config_switch_beats_everything(self):
        self.config.enabled = False
        self.controller.arm()
        self.assertFalse(self.controller.armed)
        self.assertFalse(self.controller.update(self._guidance(), self._fix()).engaged)

    def test_rate_limit_slows_large_jumps(self):
        self.controller.max_rate_deg_s = 25.0
        self.controller.arm()
        now = time.time()
        command = self.controller.update(self._guidance(steer=-30.0), self._fix(), now=now)
        self.assertGreater(command.angle_deg, -10.0)   # nicht sofort voll eingeschlagen

    def test_refuses_when_the_output_is_not_ready(self):
        self.output.ready = False
        self.assertIn("nicht bereit", self.controller.arm())
        self.assertFalse(self.controller.armed)


class ActuatorTest(unittest.TestCase):
    """Die Ausgänge: Telegramm an eine Lenkplatine, Regelgröße beim Motor."""

    def test_udp_frame_is_compact_and_checksummed(self):
        from agripilot.actuators import SteerContext, UdpOutput
        frame = UdpOutput("127.0.0.1", 8888)._frame(
            True, -3.4, SteerContext(speed_ms=2.5, cross_track_m=0.08))
        self.assertEqual(len(frame), 11)
        self.assertEqual(frame[:2], b"AP")
        checksum = 0
        for byte in frame[:-1]:
            checksum ^= byte
        self.assertEqual(frame[-1], checksum)

    def test_wheel_angle_error_when_a_sensor_is_present(self):
        from agripilot.actuators import PhidgetOutput, SteerContext
        from agripilot.config import PhidgetConfig
        output = PhidgetOutput(PhidgetConfig(feedback="was"))
        output._target_angle = 10.0
        self.assertAlmostEqual(output._error(4.0), 6.0)

    def test_yaw_rate_error_uses_the_bicycle_model(self):
        """Ohne Radwinkelsensor wird die Drehrate geregelt, nicht der Winkel."""
        from agripilot.actuators import PhidgetOutput, SteerContext
        from agripilot.config import PhidgetConfig
        output = PhidgetOutput(PhidgetConfig(feedback="yaw_rate"))
        output._target_angle = 10.0
        output._context = SteerContext(speed_ms=3.0, wheelbase_m=2.6,
                                       yaw_rate_deg_s=0.0)
        # 3 m/s durch 2,6 m Radstand mal tan(10°) = 11,66 Grad je Sekunde
        self.assertAlmostEqual(output._error(None), 11.66, places=1)
        # Dreht der Traktor bereits so schnell, ist der Fehler null
        output._context.yaw_rate_deg_s = 11.66
        self.assertAlmostEqual(output._error(None), 0.0, places=1)

    def test_yaw_rate_mode_does_nothing_without_a_rate(self):
        from agripilot.actuators import PhidgetOutput, SteerContext
        from agripilot.config import PhidgetConfig
        output = PhidgetOutput(PhidgetConfig(feedback="yaw_rate"))
        output._target_angle = 20.0
        output._context = SteerContext(speed_ms=3.0, yaw_rate_deg_s=None)
        self.assertEqual(output._error(None), 0.0)

    def test_rescale_factor_turns_counts_into_degrees(self):
        """Der Zählwert je Grad wird zum RescaleFactor der Platine."""
        from agripilot.config import PhidgetConfig
        for counts, erwartet in ((40.0, 0.025), (100.0, 0.01), (12.5, 0.08)):
            config = PhidgetConfig(counts_per_deg=counts)
            self.assertAlmostEqual(1.0 / config.counts_per_deg, erwartet, places=9)

    def test_position_output_clamps_to_the_mechanical_stop(self):
        from agripilot.actuators import PhidgetPositionOutput, SteerContext
        from agripilot.config import PhidgetConfig

        class FakeController:
            """Ersetzt die Platine: merkt sich, was ihr aufgetragen wurde."""

            def __init__(self):
                self.position = 0.0
                self.target = None
                self.engaged = None
                self.failsafe_reset = 0

            def setTargetPosition(self, value): self.target = value
            def setEngaged(self, value): self.engaged = value
            def resetFailsafe(self): self.failsafe_reset += 1
            def getPosition(self): return self.position
            def getDutyCycle(self): return 0.1

        output = PhidgetPositionOutput(PhidgetConfig(max_wheel_angle_deg=35.0))
        output.controller = FakeController()
        output.centred = True

        output.command(True, 12.0, SteerContext())
        self.assertAlmostEqual(output.controller.target, 12.0)
        self.assertTrue(output.controller.engaged)

        output.command(True, 90.0, SteerContext())       # weit über dem Anschlag
        self.assertAlmostEqual(output.controller.target, 35.0)

        output.command(True, -90.0, SteerContext())
        self.assertAlmostEqual(output.controller.target, -35.0)
        self.assertEqual(output.controller.failsafe_reset, 3)

    def test_position_output_releases_the_motor_when_not_engaged(self):
        """Nicht scharf heißt stromlos - sonst hielte der Motor das Lenkrad fest."""
        from agripilot.actuators import PhidgetPositionOutput, SteerContext
        from agripilot.config import PhidgetConfig

        class FakeController:
            def __init__(self): self.engaged = None
            def setTargetPosition(self, value): pass
            def setEngaged(self, value): self.engaged = value
            def resetFailsafe(self): pass
            def getPosition(self): return 0.0
            def getDutyCycle(self): return 0.0

        output = PhidgetPositionOutput(PhidgetConfig())
        output.controller = FakeController()
        output.centred = True
        output.command(False, 10.0, SteerContext())
        self.assertFalse(output.controller.engaged)

    def test_position_output_does_not_drive_before_the_centre_is_known(self):
        from agripilot.actuators import PhidgetPositionOutput, SteerContext
        from agripilot.config import PhidgetConfig

        class FakeController:
            def __init__(self): self.engaged = None
            def setTargetPosition(self, value): pass
            def setEngaged(self, value): self.engaged = value
            def resetFailsafe(self): pass
            def getPosition(self): return 0.0
            def getDutyCycle(self): return 0.0

        output = PhidgetPositionOutput(PhidgetConfig())
        output.controller = FakeController()
        output.centred = False                     # Mitte noch nicht gelernt
        output.command(True, 10.0, SteerContext())
        self.assertFalse(output.controller.engaged)

    def test_position_output_reports_a_blocked_wheel(self):
        """Motor drückt nahe der Grenze, das Rad folgt nicht: abgeben."""
        from agripilot.actuators import PhidgetPositionOutput
        from agripilot.config import PhidgetConfig

        output = PhidgetPositionOutput(
            PhidgetConfig(override_deg=4.0, velocity_limit=0.5))
        output.target_deg = 10.0

        # Kleine Abweichung: das ist normales Nachführen
        self.assertFalse(output._detect_override(9.0, 0.5, True))
        # Große Abweichung, aber der Motor drückt kaum - kein Eingriff
        self.assertFalse(output._detect_override(0.0, 0.05, True))
        # Große Abweichung bei voller Leistung, aber noch nicht lange genug
        self.assertFalse(output._detect_override(0.0, 0.5, True))
        output._error_since -= 1.0                 # eine Sekunde später
        self.assertTrue(output._detect_override(0.0, 0.5, True))
        # Nicht scharf: nie ein Eingriff
        self.assertFalse(output._detect_override(0.0, 0.5, False))

    def test_learning_the_centre_needs_a_motor(self):
        from agripilot.actuators import PhidgetPositionOutput
        from agripilot.config import PhidgetConfig
        with self.assertRaises(RuntimeError):
            PhidgetPositionOutput(PhidgetConfig()).learn_centre()

    def test_learning_the_centre_zeroes_the_encoder(self):
        from agripilot.actuators import PhidgetPositionOutput
        from agripilot.config import PhidgetConfig

        class FakeController:
            def __init__(self): self.position = 137.5
            def addPositionOffset(self, offset): self.position += offset
            def setTargetPosition(self, value): self.target = value
            def getPosition(self): return self.position

        output = PhidgetPositionOutput(PhidgetConfig())
        output.controller = FakeController()
        result = output.learn_centre()
        self.assertAlmostEqual(result["centre_deg"], 0.0)
        self.assertTrue(output.centred)

    def test_output_choice_follows_the_configuration(self):
        from agripilot import config as config_module
        from agripilot.actuators import build_output
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.steering.output = "phidget"
        # Voreinstellung: der Positionsregler der Platine
        self.assertEqual(build_output(config).name, "phidget-position")
        config.phidget.control = "velocity"
        self.assertEqual(build_output(config).name, "phidget")
        config.steering.output = "udp"
        self.assertEqual(build_output(config).name, "udp")
        config.steering.output = "none"
        self.assertEqual(build_output(config).name, "none")


class ImuTest(unittest.TestCase):
    """Hangausgleich - der Grund, warum überhaupt ein Neigungssensor dranhängt."""

    def test_terrain_offset_grows_with_slope_and_height(self):
        from agripilot.imu import Attitude, terrain_offset
        right, forward = terrain_offset(Attitude(roll_deg=6.0), 3.0)
        self.assertAlmostEqual(right, 3.0 * math.sin(math.radians(6.0)), places=6)
        self.assertAlmostEqual(right, 0.3136, places=3)      # gut 31 cm
        self.assertAlmostEqual(forward, 0.0)
        # Halbe Antennenhöhe, halber Versatz
        half, _ = terrain_offset(Attitude(roll_deg=6.0), 1.5)
        self.assertAlmostEqual(half, right / 2, places=6)

    def test_pitch_shifts_along_the_direction_of_travel(self):
        from agripilot.imu import Attitude, terrain_offset
        _, forward = terrain_offset(Attitude(pitch_deg=5.0), 3.0)
        self.assertAlmostEqual(forward, 3.0 * math.sin(math.radians(5.0)), places=6)

    def test_roll_sign_can_be_flipped_for_the_mounting(self):
        from agripilot.imu import Attitude, terrain_offset
        normal, _ = terrain_offset(Attitude(roll_deg=6.0), 3.0, roll_sign=1.0)
        flipped, _ = terrain_offset(Attitude(roll_deg=6.0), 3.0, roll_sign=-1.0)
        self.assertAlmostEqual(normal, -flipped, places=6)

    def test_levelling_removes_a_mounting_error(self):
        from agripilot.imu import SimulatedImu
        source = SimulatedImu()
        source._publish(2.5, -1.0, None, 0.0)
        self.assertAlmostEqual(source.attitude.roll_deg, 2.5)
        source.level_here()                       # "hier ist eben"
        source._publish(2.5, -1.0, None, 0.0)
        self.assertAlmostEqual(source.attitude.roll_deg, 0.0, places=6)
        source._publish(8.5, -1.0, None, 0.0)     # echte 6 Grad Hang
        self.assertAlmostEqual(source.attitude.roll_deg, 6.0, places=6)

    def test_axis_mapping_covers_the_usual_mountings(self):
        from agripilot.imu import TinkerforgeImu
        standard = TinkerforgeImu(axis_map="standard")
        self.assertEqual(standard._map_axes(3.0, 1.0), (3.0, 1.0))
        swapped = TinkerforgeImu(axis_map="swapped")
        self.assertEqual(swapped._map_axes(3.0, 1.0), (1.0, 3.0))
        inverted = TinkerforgeImu(axis_map="inverted")
        self.assertEqual(inverted._map_axes(3.0, 1.0), (-3.0, -1.0))

    def test_attitude_goes_stale(self):
        from agripilot.imu import Attitude
        self.assertFalse(Attitude().fresh)
        self.assertTrue(Attitude(received_at=time.time()).fresh)
        self.assertFalse(Attitude(received_at=time.time() - 5).fresh)


class ExportTest(unittest.TestCase):
    def setUp(self):
        from agripilot import export
        self.export = export
        self.dir = tempfile.TemporaryDirectory()
        self.store = Storage(os.path.join(self.dir.name, "e.db"))
        self.field = self.store.save_field({
            "name": "Testfeld", "datum_lat": 48.0, "datum_lon": 11.0,
            "boundary": [[0, 0], [100, 0], [100, 50], [0, 50]], "area_ha": 0.5,
        })
        self.job = self.store.start_job(self.field["id"], "pi", "Fendt", "Grubbern")
        self.store.add_track_points(self.job["id"], [
            (1_700_000_000.0 + i, 48.0 + i * 1e-5, 11.0, 500.0, 3.0, 0.0, 4, 0.02)
            for i in range(10)
        ])
        self.store.update_job(self.job["id"], ended_at=1_700_000_600.0,
                              area_ha=1.5, distance_m=1200.0)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_gpx_is_well_formed(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.export.job_gpx(self.store, self.job["id"]))
        points = root.findall(".//{http://www.topografix.com/GPX/1/1}trkpt")
        self.assertEqual(len(points), 10)

    def test_geojson_has_track_and_boundary(self):
        data = self.export.job_geojson(self.store, self.job["id"])
        kinds = {f["properties"]["typ"] for f in data["features"]}
        self.assertIn("Fahrspur", kinds)
        self.assertIn("Feldgrenze", kinds)
        for feature in data["features"]:
            if feature["properties"]["typ"] == "Feldgrenze":
                ring = feature["geometry"]["coordinates"][0]
                self.assertEqual(ring[0], ring[-1])       # geschlossener Ring
                self.assertAlmostEqual(ring[0][0], 11.0, places=5)   # lon zuerst

    def test_csv_uses_semicolons_for_german_excel(self):
        rows = self.export.job_csv(self.store, self.job["id"]).splitlines()
        self.assertIn("zeit_utc;breite;laenge", rows[0])
        self.assertEqual(len(rows), 11)

    def test_summary_lists_the_job(self):
        summary = self.export.jobs_summary_csv(self.store).splitlines()
        self.assertEqual(len(summary), 2)
        self.assertIn("Grubbern", summary[1])


class EngineTest(unittest.TestCase):
    """Der Ablauf pro Position: Werkzeugpunkt, Führung, Fläche, Aufzeichnung."""

    def setUp(self):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        self.dir = tempfile.TemporaryDirectory()
        self.store = Storage(os.path.join(self.dir.name, "engine.db"))
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.network.device_id = "pi-test"
        self.engine = Engine(config, self.store)
        self.plane = geo.LocalPlane(48.0, 11.0)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def _fix(self, east, north, heading=0.0, speed=3.0, when=None):
        lat, lon = self.plane.to_wgs(east, north)
        fix = nmea.Fix(lat=lat, lon=lon, fix_quality=4, speed_ms=speed,
                       course_deg=heading, altitude=500.0)
        fix.received_at = when if when is not None else time.time()
        return fix

    def _drive(self, east, from_north, to_north, step=1.0, start_time=1000.0):
        """Fährt eine Bahn und liefert die Zeit am Ende."""
        moment = start_time
        north = from_north
        direction = 1.0 if to_north > from_north else -1.0
        heading = 0.0 if direction > 0 else 180.0
        while (north - to_north) * direction < 0:
            self.engine.on_fix(self._fix(east, north, heading, 3.0, moment))
            north += step * direction
            moment += step / 3.0
        return moment

    def test_records_area_and_distance(self):
        field = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 11.0})
        self.engine.load_field(field["id"])
        self.engine.update_profile({"width_m": 6.0, "antenna_forward_m": 0.0,
                                    "tool_trailing_m": 0.0})
        self.engine.on_fix(self._fix(0.0, 0.0, 0.0, 3.0, 1000.0))
        self.engine.start_job("Grubbern")
        self._drive(0.0, 0.0, 100.0)
        self.assertAlmostEqual(self.engine.coverage.area_m2, 600.0, delta=15.0)
        self.assertAlmostEqual(self.engine.distance_m, 100.0, delta=2.0)

    def test_gps_jump_paints_no_phantom_swath(self):
        """Nach einem Empfangsausfall darf kein Streifen quer übers Feld entstehen."""
        field = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 11.0})
        self.engine.load_field(field["id"])
        self.engine.update_profile({"width_m": 6.0, "antenna_forward_m": 0.0,
                                    "tool_trailing_m": 0.0})
        self.engine.on_fix(self._fix(0.0, 0.0, 0.0, 3.0, 1000.0))
        self.engine.start_job("Grubbern")
        end = self._drive(0.0, 0.0, 50.0)
        area_before = self.engine.coverage.area_m2
        distance_before = self.engine.distance_m

        # Sprung um 40 m zur Seite in einem Zehntel einer Sekunde
        self.engine.on_fix(self._fix(40.0, 50.0, 0.0, 3.0, end + 0.1))
        self.assertAlmostEqual(self.engine.coverage.area_m2, area_before, delta=1.0)
        self.assertAlmostEqual(self.engine.distance_m, distance_before, delta=0.1)

        # Danach geht es an der neuen Stelle ganz normal weiter
        self._drive(40.0, 50.0, 70.0, start_time=end + 0.2)
        self.assertGreater(self.engine.coverage.area_m2, area_before + 100.0)

    def test_terrain_compensation_moves_the_position_off_the_antenna(self):
        """Am Hang steht die Antenne neben dem Punkt, der bearbeitet wird."""
        from agripilot.imu import SimulatedImu

        field = self.store.save_field({"name": "Hang", "datum_lat": 48.0, "datum_lon": 11.0})
        self.engine.load_field(field["id"])
        self.engine.update_profile({"antenna_forward_m": 0.0, "tool_trailing_m": 0.0,
                                    "antenna_height_m": 3.0})
        imu = SimulatedImu()
        self.engine.imu = imu

        # Eben: die Position bleibt, wo der Empfänger sie meldet
        imu._publish(0.0, 0.0, None, 0.0)
        self.engine.on_fix(self._fix(0.0, 0.0, 0.0, 3.0, 1000.0))
        self.engine.on_fix(self._fix(0.0, 10.0, 0.0, 3.0, 1003.0))
        self.assertAlmostEqual(self.engine.tool_position[0], 0.0, places=3)

        # Sechs Grad Seitenhang, Fahrt nach Norden: gut 31 cm nach links
        imu._publish(6.0, 0.0, None, 0.0)
        self.engine.on_fix(self._fix(0.0, 20.0, 0.0, 3.0, 1006.0))
        self.assertAlmostEqual(self.engine.tool_position[0], -0.3136, places=3)
        self.assertAlmostEqual(self.engine.terrain_offset_m[0], 0.3136, places=3)

        # Abschalten lässt die Position unverändert stehen
        self.engine.config.imu.terrain_compensation = False
        self.engine.on_fix(self._fix(0.0, 30.0, 0.0, 3.0, 1009.0))
        self.assertAlmostEqual(self.engine.tool_position[0], 0.0, places=3)

    def test_watchdog_disarms_when_the_receiver_falls_silent(self):
        """Kommen keine Positionen mehr, darf die Lenkung nicht scharf bleiben."""
        from agripilot.actuators import NullOutput
        from agripilot.config import SteeringConfig
        from agripilot.steering import SteeringController

        output = NullOutput()
        output.ready = True
        steering = SteeringController(SteeringConfig(enabled=True, output="none"), output)
        self.engine.steering = steering
        steering.arm()
        self.assertTrue(steering.armed)

        self.engine.on_fix(self._fix(0.0, 0.0, 0.0, 3.0, time.time()))
        self.engine.tick()                      # frische Daten: bleibt scharf
        self.assertTrue(steering.armed)

        self.engine.fix.received_at = time.time() - 5.0
        self.engine.tick()
        self.assertFalse(steering.armed)
        self.assertIn("keine GPS-Daten", steering.command.reason)

    def test_ab_line_needs_two_separated_points(self):
        field = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 11.0})
        self.engine.load_field(field["id"])
        self.engine.on_fix(self._fix(0.0, 0.0))
        self.engine.set_a()
        with self.assertRaises(RuntimeError):
            self.engine.set_b()                    # A und B fallen zusammen

    def test_boundary_recording_gives_the_area(self):
        field = self.store.save_field({"name": "F", "datum_lat": 48.0, "datum_lon": 11.0})
        self.engine.load_field(field["id"])
        self.engine.update_profile({"antenna_forward_m": 0.0, "tool_trailing_m": 0.0})
        self.engine.on_fix(self._fix(0.0, 0.0))
        self.engine.start_recording("boundary")
        corners = [(100.0, 0.0), (100.0, 50.0), (0.0, 50.0), (0.0, 0.0)]
        moment = 1000.0
        for east, north in corners:
            self.engine.on_fix(self._fix(east, north, 0.0, 3.0, moment))
            moment += 20.0
        result = self.engine.stop_recording()
        self.assertAlmostEqual(result["area_ha"], 0.5, places=2)


class SettingsTest(unittest.TestCase):
    """Die Einstellungen - alles, was bisher nur in der Datei stand.

    Geprüft wird vor allem, was ein Formular gefährlich macht: dass ein
    unsinniger Wert nicht durchgeht, dass ein halb übernommener Satz gar nicht
    erst entsteht, und dass die eine Einstellung, die eine Maschine bewegt,
    nicht nebenbei umgelegt werden kann.
    """

    def setUp(self):
        from agripilot import config as config_module
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.pfad = os.path.join(self.ordner.name, "config.yaml")
        self.config = config_module.load(self.pfad)

    def test_every_field_matches_the_data_model(self):
        """Ein Tippfehler im Schema wäre eine Einstellung, die ins Leere greift."""
        from agripilot import settings
        for schluessel, feld in settings.ALLE_FELDER.items():
            abschnitt = getattr(self.config, feld.abschnitt, None)
            self.assertIsNotNone(abschnitt, f"{schluessel}: Abschnitt fehlt")
            self.assertTrue(hasattr(abschnitt, feld.name), f"{schluessel}: Feld fehlt")

    def test_values_and_choices_line_up(self):
        """Was die Oberfläche anzeigt, muss unter den Auswahlmöglichkeiten sein."""
        from agripilot import settings
        werte = settings.werte(self.config)
        for schluessel, feld in settings.ALLE_FELDER.items():
            if feld.typ == "auswahl":
                erlaubt = [wert for wert, _ in feld.auswahl]
                self.assertIn(werte[schluessel], erlaubt, schluessel)

    def test_a_value_out_of_range_is_refused(self):
        from agripilot import settings
        with self.assertRaises(settings.EinstellungsFehler) as gefangen:
            settings.uebernehmen(self.config, {"phidget.current_limit_a": 40.0})
        self.assertIn("Stromgrenze", str(gefangen.exception))
        self.assertEqual(self.config.phidget.current_limit_a, 2.0)

    def test_nothing_is_applied_when_one_value_is_wrong(self):
        """Erst prüfen, dann setzen - halb übernommen wäre schlimmer als abgelehnt."""
        from agripilot import settings
        with self.assertRaises(settings.EinstellungsFehler):
            settings.uebernehmen(self.config, {
                "gnss.port": "COM7",                    # gültig
                "phidget.max_wheel_angle_deg": 900.0,   # weit über dem Anschlag
            })
        self.assertEqual(self.config.gnss.port, "/dev/ttyACM0")

    def test_an_unknown_setting_is_refused(self):
        from agripilot import settings
        with self.assertRaises(settings.EinstellungsFehler):
            settings.uebernehmen(self.config, {"gnss.gibtsnicht": 1})

    def test_saving_writes_the_file_and_reads_back(self):
        from agripilot import config as config_module, settings
        ergebnis = settings.uebernehmen(self.config, {
            "gnss.source": "serial", "gnss.port": "COM3", "gnss.baudrate": 115200,
            "imu.roll_sign": "-1.0",
        })
        self.assertEqual(ergebnis["gespeichert"], 4)
        wieder = config_module.load(self.pfad)
        self.assertEqual(wieder.gnss.port, "COM3")
        self.assertEqual(wieder.imu.roll_sign, -1.0)

    def test_the_password_never_leaves_and_survives_a_save(self):
        from agripilot import settings
        self.config.corrections.password = "geheim"
        self.assertEqual(settings.werte(self.config)["corrections.password"],
                         settings.PASSWORT_PLATZHALTER)
        # Die Oberfläche schickt den Platzhalter zurück: das Passwort bleibt stehen.
        settings.uebernehmen(self.config, {
            "corrections.password": settings.PASSWORT_PLATZHALTER,
            "corrections.mountpoint": "BASIS1"})
        self.assertEqual(self.config.corrections.password, "geheim")

    def test_restart_is_named_only_where_it_is_needed(self):
        from agripilot import settings
        sofort = settings.uebernehmen(self.config, {"steering.max_cross_track_m": 1.2})
        self.assertEqual(sofort["neustart_noetig"], [])
        spaeter = settings.uebernehmen(self.config, {"server.port": 8090})
        self.assertIn("Port", spaeter["neustart_noetig"])
        # Empfänger und Sensor werden im Betrieb neu verbunden - kein Neustart.
        quelle = settings.uebernehmen(self.config, {"gnss.baudrate": 57600})
        self.assertEqual(quelle["neustart_noetig"], [])

    def test_steering_cannot_be_enabled_before_the_motor_step_is_done(self):
        """Die eine Einstellung, die eine Maschine in Bewegung setzt."""
        from agripilot import settings
        with self.assertRaises(settings.EinstellungsFehler) as gefangen:
            settings.uebernehmen(self.config, {"steering.enabled": True},
                                 lenkung_freigabe_erlaubt=False)
        self.assertIn("Einbau", str(gefangen.exception))
        self.assertFalse(self.config.steering.enabled)

        settings.uebernehmen(self.config, {"steering.enabled": True},
                             lenkung_freigabe_erlaubt=True)
        self.assertTrue(self.config.steering.enabled)

    def test_switching_steering_off_is_never_blocked(self):
        """Abschalten muss immer gehen, auch mit unfertigem Einbau."""
        from agripilot import settings
        self.config.steering.enabled = True
        settings.uebernehmen(self.config, {"steering.enabled": False},
                             lenkung_freigabe_erlaubt=False)
        self.assertFalse(self.config.steering.enabled)


class ChecklistTest(unittest.TestCase):
    """Inbetriebnahme - die Liste, die mitliest.

    Wichtig ist hier weniger, dass Haken gesetzt werden, als dass sie sich
    *nicht* setzen lassen, solange die Anlage etwas anderes sagt.
    """

    def setUp(self):
        from agripilot.checklist import Checkliste
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.store = Storage(os.path.join(self.ordner.name, "test.sqlite"))
        self.addCleanup(self.store.close)
        self.liste = Checkliste(self.store)

    def _live(self, **werte):
        """Ein Zustandsbild wie es die Anzeige bekommt - alles gesund."""
        live = {
            "fix": {"fix_quality": 4, "fix_label": "RTK fix", "satellites": 24,
                    "accuracy_m": 0.02, "age_of_corrections": 1.0},
            "profile": {"width_m": 6.0, "antenna_forward_m": 1.2,
                        "antenna_right_m": 0.0, "antenna_height_m": 3.0},
            "imu": {"healthy": True, "roll_deg": 0.5, "terrain_offset_cm": [3.0, 0.0]},
            "fahrzeit_s": 0.0,
            "system": {
                "gnss": {"status": "COM3", "healthy": True, "lines": 4200},
                "imu": {"source": "tinkerforge", "status": "IMU", "healthy": True},
                "steering_output": {"typ": "phidget", "status": "bereit",
                                    "bereit": True, "mitte_gelernt": True,
                                    "zaehlwerte_je_grad": 40.0},
            },
        }
        live.update(werte)
        return live

    def _schritt(self, live, schritt_id):
        return next(s for s in self.liste.schritte(live) if s["id"] == schritt_id)

    def test_the_first_open_step_is_the_one_on_deck(self):
        schritte = self.liste.schritte(self._live())
        dran = [s for s in schritte if s["dran"]]
        self.assertEqual(len(dran), 1)
        self.assertEqual(dran[0]["id"], "simulator")

    def test_a_receiver_without_gst_is_not_ready(self):
        """Ohne GST-Satz gibt es keine Genauigkeitsangabe - und keinen Beleg."""
        live = self._live()
        live["fix"] = {**live["fix"], "accuracy_m": None}
        self.assertIs(self._schritt(live, "empfaenger")["erfuellt"], False)
        self.assertIn("GST", self._schritt(live, "empfaenger")["pruefung"])

    def test_rtk_must_stand_for_a_while_not_just_now(self):
        """Ein Fix, der gerade eben eingerastet ist, ist kein dauerhafter Fix."""
        from agripilot import checklist
        live = self._live()
        self.liste.beobachten(live)          # ab jetzt steht RTK fix
        self.assertIs(self._schritt(live, "korrekturen")["erfuellt"], False)

        self.liste.beobachtung.rtk_seit = time.time() - checklist.RTK_DAUER_S - 1
        self.assertIs(self._schritt(live, "korrekturen")["erfuellt"], True)

    def test_a_lost_fix_starts_the_clock_again(self):
        from agripilot import checklist
        live = self._live()
        self.liste.beobachten(live)
        self.liste.beobachtung.rtk_seit = time.time() - checklist.RTK_DAUER_S - 1

        verloren = self._live()
        verloren["fix"] = {**verloren["fix"], "fix_quality": 5, "fix_label": "RTK float"}
        self.liste.beobachten(verloren)
        self.assertEqual(self.liste.beobachtung.rtk_verloren, 1)

        self.liste.beobachten(live)          # rastet wieder ein
        self.assertIs(self._schritt(live, "korrekturen")["erfuellt"], False)

    def test_a_step_the_machine_contradicts_cannot_be_ticked(self):
        live = self._live()
        live["fix"] = {**live["fix"], "fix_quality": 5, "fix_label": "RTK float"}
        with self.assertRaises(ValueError) as gefangen:
            self.liste.abhaken("korrekturen", live)
        self.assertIn("RTK float", str(gefangen.exception))
        self.assertFalse(self._schritt(live, "korrekturen")["fertig"])

    def test_a_tick_survives_a_restart(self):
        from agripilot.checklist import Checkliste
        self.liste.abhaken("simulator", self._live(), geraet="Werkstatt-Tablet")
        spaeter = Checkliste(self.store)
        schritt = next(s for s in spaeter.schritte(self._live()) if s["id"] == "simulator")
        self.assertTrue(schritt["fertig"])
        self.assertEqual(schritt["bestaetigt_von"], "Werkstatt-Tablet")

    def test_the_sign_test_needs_a_slope_to_prove_anything(self):
        """Auf ebenem Boden sieht auch ein verkehrtes Vorzeichen sauber aus."""
        live = self._live()
        self.liste.beobachten(live)          # 0,5° - viel zu wenig
        self.assertIs(self._schritt(live, "neigungssensor")["erfuellt"], False)

        gekippt = self._live()
        gekippt["imu"] = {**gekippt["imu"], "roll_deg": -4.2}
        self.liste.beobachten(gekippt)
        self.assertIsNone(self._schritt(live, "neigungssensor")["erfuellt"])

    def test_the_motor_step_is_absent_without_a_steering_output(self):
        live = self._live()
        live["system"] = {**live["system"],
                          "steering_output": {"typ": "none", "status": "nur Anzeige",
                                              "bereit": True}}
        self.assertNotIn("lenkmotor", [s["id"] for s in self.liste.schritte(live)])

    def test_hours_as_a_guide_come_from_the_recorded_jobs(self):
        from agripilot import checklist
        live = self._live(fahrzeit_s=checklist.LENKHILFE_S - 60)
        self.assertIs(self._schritt(live, "lenkhilfe")["erfuellt"], False)
        live = self._live(fahrzeit_s=checklist.LENKHILFE_S + 60)
        self.assertIs(self._schritt(live, "lenkhilfe")["erfuellt"], True)
        # Ohne Bestätigungstext gilt die Messung selbst als Erledigung.
        self.assertTrue(self._schritt(live, "lenkhilfe")["fertig"])

    def test_recorded_working_time_adds_up(self):
        from agripilot.checklist import fahrzeit_s
        feld = self.store.save_field({"name": "Testfeld", "datum_lat": 48.1,
                                      "datum_lon": 11.5})
        for sekunden in (1800.0, 2400.0):
            job = self.store.start_job(feld["id"], "pi", "Traktor", "Grubbern")
            self.store.update_job(job["id"], working_time_s=sekunden)
        self.assertAlmostEqual(fahrzeit_s(self.store), 4200.0)

    def test_reset_clears_every_tick(self):
        live = self._live()
        self.liste.abhaken("simulator", live)
        self.assertTrue(self._schritt(live, "simulator")["fertig"])
        self.liste.zuruecksetzen(live)
        # Was gemessen wird, bleibt gemessen; nur die Bestätigungen sind weg.
        self.assertFalse(self._schritt(live, "simulator")["fertig"])
        self.assertTrue(self._schritt(live, "empfaenger")["fertig"])


class ServerTest(unittest.TestCase):
    """Ende-zu-Ende: Simulator an, Feld anlegen, Spur setzen, Arbeit erfassen."""

    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("fastapi/httpx nicht installiert")

    def test_full_run(self):
        import warnings
        from fastapi.testclient import TestClient
        from agripilot import config as config_module
        from agripilot.server import create_app
        warnings.filterwarnings("ignore")

        with tempfile.TemporaryDirectory() as directory:
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = directory
            config.network.device_id = "pi-test"
            with TestClient(create_app(config)) as client:
                time.sleep(0.7)                       # Simulator liefert Positionen
                state = client.get("/api/state").json()
                self.assertEqual(state["fix"]["fix_label"], "RTK fix")

                field = client.post("/api/fields", json={"name": "Testacker"})
                self.assertEqual(field.status_code, 200)
                client.post("/api/profile", json={"width_m": 6.0, "sections": 3})

                client.post("/api/guidance/a")
                line = client.post("/api/guidance/a-plus",
                                   json={"heading": 0, "name": "Nord"})
                self.assertEqual(line.status_code, 200)

                client.post("/api/job/start", json={"operation": "Grubbern"})
                time.sleep(1.5)
                state = client.get("/api/state").json()
                self.assertTrue(state["guidance"]["active"])
                self.assertGreater(state["job"]["area_ha"], 0.0)

                # Sektionen: 'auto' darf nicht als Sektionsnummer gelesen werden
                self.assertEqual(
                    client.post("/api/sections/auto", json={"enabled": True}).status_code, 200)

                job = client.post("/api/job/stop").json()["data"]
                self.assertGreater(job["distance_m"], 0.0)
                self.assertEqual(client.get(f"/api/jobs/{job['id']}/gpx").status_code, 200)

                # Lenkung ist ohne Freigabe in der Konfiguration nicht scharf zu bekommen
                armed = client.post("/api/steering/arm").json()["data"]
                self.assertFalse(armed["armed"])


QUADRAT = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]


class ContourTest(unittest.TestCase):
    """Kontur: die Feldgrenze selbst ist das Muster, Ring 0 ist die Grenze."""

    def _ring(self, punkte=None, abstand=6.0):
        return GuidanceLine("contour", punkte or QUADRAT, abstand)

    def test_ring_zero_is_the_boundary(self):
        zustand = self._ring().solve((0.4, 50.0), 180.0, 2.0, VehicleProfile())
        self.assertEqual(zustand.pass_number, 0)
        self.assertAlmostEqual(abs(zustand.cross_track_m), 0.4, places=6)

    def test_each_ring_is_one_working_width_further_in(self):
        for hinein, ring in ((6.0, 1), (12.0, 2), (18.0, 3)):
            zustand = self._ring().solve((hinein, 50.0), 180.0, 2.0, VehicleProfile())
            self.assertEqual(zustand.pass_number, ring)
            self.assertAlmostEqual(zustand.cross_track_m, 0.0, places=6)

    def test_ring_number_ignores_the_winding_direction(self):
        """Ob die Grenze im oder gegen den Uhrzeigersinn abgefahren wurde,
        darf die Ringnummer nicht ändern - sonst liegt Ring 1 mal drinnen
        und mal draußen."""
        rechtsherum = self._ring(list(reversed(QUADRAT)))
        linksherum = self._ring(QUADRAT)
        for punkt in ((6.0, 50.0), (50.0, 12.0), (94.0, 50.0)):
            self.assertEqual(
                rechtsherum.solve(punkt, 0.0, 2.0, VehicleProfile()).pass_number,
                linksherum.solve(punkt, 0.0, 2.0, VehicleProfile()).pass_number,
            )

    def test_lightbar_side_follows_the_direction_of_travel(self):
        """Gemessen wird nach innen, angezeigt wird links/rechts. Derselbe
        Punkt muss deshalb je nach Fahrtrichtung die Seite wechseln."""
        ring = self._ring()
        nach_norden = ring.solve((6.5, 50.0), 0.0, 2.0, VehicleProfile())
        nach_sueden = ring.solve((6.5, 50.0), 180.0, 2.0, VehicleProfile())
        self.assertAlmostEqual(nach_norden.cross_track_m, 0.5, places=6)
        self.assertAlmostEqual(nach_sueden.cross_track_m, -0.5, places=6)

    def test_the_closing_edge_counts_too(self):
        """Der Ring ist geschlossen. Sonst hätte er eine Lücke genau dort, wo
        die Aufzeichnung zufällig endete."""
        # Zwischen letztem und erstem Eckpunkt liegt die Westkante.
        zustand = self._ring().solve((2.0, 50.0), 0.0, 2.0, VehicleProfile())
        self.assertEqual(zustand.pass_number, 0)
        self.assertAlmostEqual(abs(zustand.cross_track_m), 2.0, places=6)

    def test_a_contour_needs_a_boundary(self):
        with self.assertRaises(ValueError):
            GuidanceLine("contour", [(0.0, 0.0), (10.0, 0.0)], 6.0)

    def test_drawing_skips_rings_outside_the_field(self):
        ringe = self._ring().pass_geometry(0, count=2)
        self.assertTrue(all(r["pass"] >= 0 for r in ringe))
        self.assertTrue(all(r["closed"] for r in ringe))

    def test_the_contour_is_never_stored_as_a_line(self):
        self.assertTrue(self._ring().derived)


class HeadlandGeometryTest(unittest.TestCase):
    """Vorgewende: Restdistanz, Ring und Annäherungsalarm."""

    def setUp(self):
        from agripilot import headland
        self.headland = headland

    def test_distance_ahead_to_the_boundary(self):
        self.assertAlmostEqual(
            self.headland.ray_to_boundary((50.0, 50.0), 0.0, QUADRAT), 50.0, places=6)
        self.assertAlmostEqual(
            self.headland.ray_to_boundary((50.0, 50.0), 90.0, QUADRAT), 50.0, places=6)
        self.assertAlmostEqual(
            self.headland.ray_to_boundary((10.0, 50.0), 270.0, QUADRAT), 10.0, places=6)

    def test_a_ray_that_never_meets_the_boundary(self):
        self.assertIsNone(
            self.headland.ray_to_boundary((150.0, 50.0), 90.0, QUADRAT))

    def test_remaining_distance_is_measured_to_the_headland_not_the_edge(self):
        """Die Arbeit endet am Vorgewende, nicht an der Grenze - sonst fährt
        man die Wende in den Zaun."""
        einstellung = self.headland.HeadlandSettings(spuren=2, alarm_abstand_m=20.0)
        stand = self.headland.status((50.0, 50.0), 0.0, QUADRAT, einstellung, 6.0)
        self.assertAlmostEqual(stand.tiefe_m, 12.0, places=6)
        self.assertAlmostEqual(stand.rest_m, 38.0, places=6)
        self.assertFalse(stand.alarm)

    def test_the_alarm_comes_before_the_headland(self):
        einstellung = self.headland.HeadlandSettings(spuren=2, alarm_abstand_m=20.0)
        stand = self.headland.status((50.0, 75.0), 0.0, QUADRAT, einstellung, 6.0)
        self.assertAlmostEqual(stand.rest_m, 13.0, places=6)
        self.assertTrue(stand.alarm)

    def test_inside_the_headland_the_remaining_distance_goes_negative(self):
        einstellung = self.headland.HeadlandSettings(spuren=2)
        stand = self.headland.status((50.0, 95.0), 0.0, QUADRAT, einstellung, 6.0)
        self.assertTrue(stand.im_vorgewende)
        self.assertLess(stand.rest_m, 0.0)
        self.assertAlmostEqual(stand.bis_grenze_m, 5.0, places=6)

    def test_depth_follows_the_working_width(self):
        einstellung = self.headland.HeadlandSettings(spuren=3)
        self.assertAlmostEqual(einstellung.tiefe_m(6.0), 18.0, places=6)
        self.assertAlmostEqual(einstellung.tiefe_m(4.0), 12.0, places=6)

    def test_an_explicit_width_overrides_the_implement(self):
        einstellung = self.headland.HeadlandSettings(spuren=2, breite_m=10.0)
        self.assertAlmostEqual(einstellung.tiefe_m(6.0), 20.0, places=6)

    def test_the_ring_shrinks_the_field_by_the_depth(self):
        ring = self.headland.ring(QUADRAT, 10.0)
        self.assertEqual(len(ring), 4)
        self.assertAlmostEqual(geo.polygon_area(ring), 80.0 * 80.0, places=3)

    def test_no_ring_without_a_depth(self):
        self.assertEqual(self.headland.ring(QUADRAT, 0.0), QUADRAT)


class ArbeitsreihenfolgeTest(unittest.TestCase):
    """Vorgewende zuerst oder zuletzt: das Programm erzwingt nichts, es sieht hin."""

    def setUp(self):
        from agripilot import headland
        self.headland = headland

    def test_coverage_of_the_headland_is_read_from_the_worked_map(self):
        """Abgetastet wird die Mittellinie des Vorgewendes - ein bearbeiteter
        Rand zählt, ein leeres Feld nicht."""
        karte = CoverageMap(cell_size=0.5)
        self.assertAlmostEqual(self.headland.abdeckung(karte.is_covered, QUADRAT, 12.0), 0.0)
        # Der ganze Rand wird als 12 m breiter Streifen bearbeitet.
        abschnitte = build_sections(12.0, 1)
        for a, b, kurs in (((6.0, 0.0), (6.0, 100.0), 0.0), ((0.0, 94.0), (100.0, 94.0), 90.0),
                           ((94.0, 100.0), (94.0, 0.0), 180.0), ((100.0, 6.0), (0.0, 6.0), 270.0)):
            karte.add_swath(a, b, kurs, abschnitte)
        self.assertGreater(self.headland.abdeckung(karte.is_covered, QUADRAT, 12.0), 0.9)

    def test_no_headland_no_answer(self):
        karte = CoverageMap(cell_size=0.5)
        self.assertIsNone(self.headland.abdeckung(karte.is_covered, QUADRAT, 0.0))
        self.assertIsNone(self.headland.abdeckung(karte.is_covered, [], 12.0))

    def test_first_means_a_hint_on_the_ab_line_while_the_headland_is_untouched(self):
        s = self.headland.HeadlandSettings(reihenfolge="zuerst", spuren=2)
        self.assertIn("erst die Kontur", self.headland.reihenfolge_hinweis(s, 0.1, "ab", 3))
        self.assertEqual(self.headland.reihenfolge_hinweis(s, 0.95, "ab", 3), "")

    def test_first_shows_the_ring_progress_while_on_the_headland(self):
        s = self.headland.HeadlandSettings(reihenfolge="zuerst", spuren=2)
        self.assertIn("Ring 1 von 2", self.headland.reihenfolge_hinweis(s, 0.2, "contour", 0))
        # Ring 2 (Index 2) liegt schon innerhalb des Vorgewendes: keine Vorgewende-Meldung
        self.assertEqual(self.headland.reihenfolge_hinweis(s, 0.9, "contour", 2), "")

    def test_no_preference_means_silence(self):
        s = self.headland.HeadlandSettings(reihenfolge="egal")
        self.assertEqual(self.headland.reihenfolge_hinweis(s, 0.0, "ab", 1), "")

    def test_an_unknown_order_falls_back_to_no_preference(self):
        s = self.headland.HeadlandSettings.from_dict({"reihenfolge": "irgendwie"})
        self.assertEqual(s.reihenfolge, "egal")


class TurnPatternTest(unittest.TestCase):
    """Die beiden Wendemuster - und die Prüfung, ob sie ins Feld passen."""

    def setUp(self):
        from agripilot import headland
        self.headland = headland

    def test_u_turn_ends_one_offset_across_and_facing_back(self):
        pfad = self.headland.plan_u_turn((50.0, 50.0), 0.0, 12.0, 4.0, turn_left=True)
        ende = pfad[-1]
        self.assertAlmostEqual(ende[0], 38.0, places=3)   # 12 m nach links
        self.assertAlmostEqual(ende[1], 50.0, places=3)
        # Fahrtrichtung am Ende: entgegengesetzt zum Start.
        kurs = geo.heading_deg(pfad[-2], pfad[-1])
        self.assertAlmostEqual(abs(geo.angle_difference(kurs, 180.0)), 0.0, delta=6.0)

    def test_u_turn_to_the_right_mirrors(self):
        pfad = self.headland.plan_u_turn((50.0, 50.0), 0.0, 12.0, 4.0, turn_left=False)
        self.assertAlmostEqual(pfad[-1][0], 62.0, places=3)

    def test_a_tight_u_turn_needs_no_straight_between_the_arcs(self):
        """Ist der Spurversatz kleiner als zwei Wendekreise, entfällt die
        Zwischengerade - die Maschine kommt enger heraus."""
        eng = self.headland.plan_u_turn((0.0, 0.0), 0.0, 6.0, 4.0, turn_left=True)
        weit = self.headland.plan_u_turn((0.0, 0.0), 0.0, 20.0, 4.0, turn_left=True)
        self.assertLess(len(eng), len(weit))

    def test_omega_reverses_the_direction_of_travel(self):
        pfad = self.headland.plan_omega((50.0, 50.0), 0.0, versatz_m=12.0,
                                        radius_m=6.0, tiefe_m=14.0, turn_left=True)
        kurs = geo.heading_deg(pfad[-2], pfad[-1])
        self.assertGreater(abs(geo.angle_difference(kurs, 0.0)), 120.0)

    def test_omega_reaches_the_neighbouring_pass(self):
        pfad = self.headland.plan_omega((50.0, 50.0), 0.0, versatz_m=12.0,
                                        radius_m=6.0, tiefe_m=14.0, turn_left=True)
        self.assertAlmostEqual(pfad[-1][0], 38.0, places=3)

    def test_omega_reaches_further_forward_than_the_u_turn(self):
        """Genau deshalb gibt es beide: die Ω-Wende holt aus, die U-Wende nicht."""
        omega = self.headland.plan_omega((0.0, 0.0), 0.0, 12.0, 4.0, 14.0, True)
        u = self.headland.plan_u_turn((0.0, 0.0), 0.0, 12.0, 4.0, True)
        self.assertGreater(max(p[1] for p in omega), max(p[1] for p in u))

    def test_a_route_inside_the_field_passes(self):
        pfad = self.headland.plan_u_turn((50.0, 50.0), 0.0, 12.0, 4.0, turn_left=True)
        self.assertTrue(self.headland.route_im_feld(pfad, QUADRAT))

    def test_a_route_that_leaves_the_field_is_rejected(self):
        """Auch ein einziger Punkt draußen reicht. Der Rest im Feld hilft
        nichts, wenn das Vorderrad im Graben steht."""
        pfad = self.headland.plan_u_turn((2.0, 50.0), 0.0, 12.0, 4.0, turn_left=True)
        self.assertFalse(self.headland.route_im_feld(pfad, QUADRAT))

    def test_without_a_boundary_nothing_is_confirmed(self):
        pfad = self.headland.plan_u_turn((50.0, 50.0), 0.0, 12.0, 4.0, turn_left=True)
        self.assertFalse(self.headland.route_im_feld(pfad, []))


class TurnFollowerTest(unittest.TestCase):
    """Das Nachfahren der Route - mit derselben Ehrlichkeit wie die Spur."""

    def setUp(self):
        from agripilot import headland
        self.headland = headland
        self.pfad = self.headland.plan_u_turn((0.0, 0.0), 0.0, 12.0, 4.0, turn_left=True)

    def _follower(self):
        return self.headland.TurnFollower(pfad=list(self.pfad))

    def test_on_the_route_the_deviation_is_zero(self):
        folger = self._follower()
        zustand = folger.solve(self.pfad[3], 0.0, 3.0, VehicleProfile())
        self.assertTrue(zustand.active)
        self.assertEqual(zustand.mode, "turn")
        self.assertLess(abs(zustand.cross_track_m), 0.05)

    def test_the_reported_deviation_is_from_the_route_not_the_old_pass(self):
        """Sonst reißt die Sicherheitsgrenze der Lenkung in jeder Wende
        sofort, und sie wäre keine Grenze mehr."""
        folger = self._follower()
        neben = (self.pfad[3][0] + 0.6, self.pfad[3][1])
        zustand = folger.solve(neben, 0.0, 3.0, VehicleProfile())
        self.assertAlmostEqual(abs(zustand.cross_track_m), 0.6, delta=0.1)

    def test_it_steers_back_towards_the_route(self):
        """Auf gerader Route ist das Vorzeichen eindeutig. Im Bogen ist es das
        nicht - dort bestimmt die Krümmung den Einschlag, und ein Meter Versatz
        nach innen kehrt ihn nicht um."""
        gerade = self.headland.TurnFollower(
            pfad=[(0.0, float(n)) for n in range(0, 40, 2)])
        links = gerade.solve((-1.0, 10.0), 0.0, 3.0, VehicleProfile())
        rechts = gerade.solve((1.0, 10.0), 0.0, 3.0, VehicleProfile())
        self.assertGreater(links.steer_angle_deg, 0.0)    # nach rechts zurück
        self.assertLess(rechts.steer_angle_deg, 0.0)      # nach links zurück

    def test_in_a_bend_it_steers_into_the_bend(self):
        folger = self._follower()
        zustand = folger.solve(self.pfad[2], 0.0, 2.0, VehicleProfile())
        self.assertLess(zustand.steer_angle_deg, 0.0)     # Linkswende: links

    def test_the_steer_angle_stays_within_the_mechanical_stop(self):
        folger = self._follower()
        profil = VehicleProfile(max_steer_deg=30.0)
        zustand = folger.solve(self.pfad[0], 170.0, 3.0, profil)
        self.assertLessEqual(abs(zustand.steer_angle_deg), 30.0)

    def test_leaving_the_route_ends_the_turn(self):
        folger = self._follower()
        folger.solve((self.pfad[3][0] + 8.0, self.pfad[3][1]), 0.0, 3.0,
                     VehicleProfile())
        self.assertTrue(folger.fertig)
        self.assertIn("verlassen", folger.grund)

    def test_reaching_the_end_finishes_the_turn(self):
        folger = self._follower()
        folger.solve(self.pfad[-1], 180.0, 3.0, VehicleProfile())
        self.assertTrue(folger.fertig)
        self.assertFalse(folger.aktiv)


class TrailerTest(unittest.TestCase):
    """Gezogene Geräte: die Ausrichtung läuft nach, statt starr zu folgen."""

    def _profil(self, **werte):
        grund = {"trailed": True, "hitch_length_m": 4.0, "antenna_forward_m": 0.0,
                 "antenna_right_m": 0.0, "tool_trailing_m": 0.0}
        grund.update(werte)
        return VehicleProfile(**grund)

    def test_a_rigid_implement_sits_where_the_tool_sits(self):
        profil = VehicleProfile(trailed=False, antenna_forward_m=1.2,
                                tool_trailing_m=3.0)
        self.assertEqual(profil.implement_position((10.0, 20.0), 42.0),
                         profil.tool_position((10.0, 20.0), 42.0))

    def test_a_trailed_implement_hangs_behind_the_hitch(self):
        from agripilot.guidance import ImplementHeading
        profil = self._profil()
        nachlauf = ImplementHeading(value=0.0)
        # Fahrzeug nach Norden, Gerät in derselben Ausrichtung: 4 m dahinter.
        lage = profil.implement_position((0.0, 0.0), 0.0, nachlauf.value)
        self.assertAlmostEqual(lage[0], 0.0, places=6)
        self.assertAlmostEqual(lage[1], -4.0, places=6)

    def test_standing_still_the_implement_does_not_swing(self):
        """Die Drehrate hängt an der Geschwindigkeit. Am Lenkrad zu drehen,
        während die Maschine steht, bewegt kein Anhängegerät."""
        from agripilot.guidance import ImplementHeading
        nachlauf = ImplementHeading(value=0.0)
        for _ in range(50):
            nachlauf.update(90.0, 0.0, 0.1, self._profil())
        self.assertAlmostEqual(nachlauf.value, 0.0, places=6)

    def test_the_implement_swings_in_behind_while_driving(self):
        from agripilot.guidance import ImplementHeading
        nachlauf = ImplementHeading(value=0.0)
        for _ in range(200):
            nachlauf.update(90.0, 3.0, 0.1, self._profil())
        self.assertAlmostEqual(nachlauf.value, 90.0, delta=1.0)

    def test_a_short_drawbar_follows_faster(self):
        from agripilot.guidance import ImplementHeading
        kurz, lang = ImplementHeading(value=0.0), ImplementHeading(value=0.0)
        for _ in range(10):
            kurz.update(90.0, 3.0, 0.1, self._profil(hitch_length_m=2.0))
            lang.update(90.0, 3.0, 0.1, self._profil(hitch_length_m=8.0))
        self.assertGreater(kurz.value, lang.value)

    def test_in_a_curve_the_implement_lags_behind(self):
        """Der ganze Punkt der Übung: in der Kurve steht das Gerät innerhalb
        der Fahrspur, nicht starr hinter dem Fahrzeug."""
        from agripilot.guidance import ImplementHeading
        profil = self._profil()
        nachlauf = ImplementHeading(value=0.0)
        nachlauf.update(45.0, 3.0, 0.2, profil)
        self.assertLess(nachlauf.value, 45.0)
        self.assertGreater(nachlauf.value, 0.0)
        gezogen = profil.implement_position((0.0, 0.0), 45.0, nachlauf.value)
        starr = profil.implement_position((0.0, 0.0), 45.0, 45.0)
        self.assertGreater(geo.distance(gezogen, starr), 0.1)

    def test_switching_the_option_off_restores_the_rigid_behaviour(self):
        from agripilot.guidance import ImplementHeading
        nachlauf = ImplementHeading(value=0.0)
        nachlauf.update(90.0, 3.0, 0.1, self._profil(trailed=False))
        self.assertAlmostEqual(nachlauf.value, 90.0, places=6)


class EngineHeadlandTest(unittest.TestCase):
    """Was der Motor daraus macht - Kontur, Wende und Markieren am Gerät."""

    def _motor(self, **profil):
        import tempfile as tf
        from agripilot import config as config_module
        from agripilot.engine import Engine
        ordner = tf.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "e.db"))
        self.addCleanup(store.close)
        motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
        motor.update_profile({"width_m": 6.0, "antenna_forward_m": 0.0,
                              "tool_trailing_m": 0.0, **profil})
        feld = store.save_field({
            "name": "Testfeld", "datum_lat": 48.0, "datum_lon": 11.0,
            "boundary": [list(p) for p in QUADRAT],
            "area_ha": 1.0,
        })
        motor.load_field(feld["id"])
        return motor

    def test_the_contour_comes_from_the_boundary(self):
        motor = self._motor()
        motor.use_contour()
        self.assertEqual(motor.line.mode, "contour")
        self.assertEqual(len(motor.line.points), 4)

    def test_no_contour_without_a_boundary(self):
        motor = self._motor()
        motor.field = {**motor.field, "boundary": []}
        with self.assertRaises(RuntimeError):
            motor.use_contour()

    def test_the_contour_follows_a_re_recorded_boundary(self):
        """Eine gespeicherte Kopie liefe der Grenze davon, ohne dass es
        jemand merkt."""
        motor = self._motor()
        motor.use_contour()
        motor.save_boundary([(0.0, 0.0), (50.0, 0.0), (50.0, 50.0), (0.0, 50.0)])
        self.assertEqual(motor.line.mode, "contour")
        self.assertAlmostEqual(max(p[0] for p in motor.line.points), 50.0, places=3)

    def test_nudging_the_contour_creates_no_stored_line(self):
        motor = self._motor()
        motor.use_contour()
        motor.tool_position = (6.0, 50.0)
        motor.nudge(0.01)
        self.assertEqual(motor.store.list_lines(motor.field["id"]), [])

    def test_a_planned_turn_is_checked_against_the_boundary(self):
        motor = self._motor()
        motor.tool_position = (50.0, 50.0)
        motor.heading = 0.0
        motor.update_headland({"muster": "u", "richtung": "links", "radius_m": 4.0})
        plan = motor.plan_turn()
        self.assertTrue(plan["im_feld"])

    def test_a_turn_at_the_edge_is_refused(self):
        motor = self._motor()
        motor.tool_position = (2.0, 50.0)
        motor.heading = 0.0
        motor.update_headland({"muster": "u", "richtung": "links", "radius_m": 4.0})
        plan = motor.plan_turn()
        self.assertFalse(plan["im_feld"])
        with self.assertRaises(RuntimeError):
            motor.start_turn()

    def test_a_stale_plan_is_not_started(self):
        """Die Route beginnt dort, wo sie geplant wurde. Von woanders aus wäre
        ihr erster Bogen ein Sprung quer über das Feld."""
        motor = self._motor()
        motor.tool_position = (50.0, 50.0)
        motor.heading = 0.0
        motor.update_headland({"muster": "u", "radius_m": 4.0})
        motor.plan_turn()
        motor.tool_position = (50.0, 80.0)
        with self.assertRaises(RuntimeError):
            motor.start_turn()

    def test_during_a_turn_the_route_guides_not_the_line(self):
        motor = self._motor()
        motor.line = GuidanceLine("ab", [(0.0, 0.0), (0.0, 100.0)],
                                  motor.profile.spacing_m)
        motor.tool_position = (50.0, 50.0)
        motor.heading = 0.0
        motor.update_headland({"muster": "u", "radius_m": 4.0})
        motor.plan_turn()
        motor.start_turn()
        motor._update_guidance(_fix(speed_ms=2.0))
        self.assertEqual(motor.guidance.mode, "turn")
        # An der AB-Linie wären es 50 m Abweichung gewesen.
        self.assertLess(abs(motor.guidance.cross_track_m), 1.0)

    def test_losing_gps_ends_a_running_turn(self):
        motor = self._motor()
        motor.tool_position = (50.0, 50.0)
        motor.heading = 0.0
        motor.update_headland({"muster": "u", "radius_m": 4.0})
        motor.plan_turn()
        motor.start_turn()
        motor.fix = _fix(received_at=time.time() - 10.0)
        motor.tick()
        self.assertIsNone(motor.turn)

    def test_the_headland_ring_is_only_recomputed_when_something_changed(self):
        motor = self._motor()
        motor.update_headland({"spuren": 2})
        erst = motor.headland_ring()
        self.assertIs(motor.headland_ring(), erst)
        motor.update_headland({"spuren": 3})
        self.assertIsNot(motor.headland_ring(), erst)

    def test_a_trailed_rig_marks_at_the_implement(self):
        """Markiert wird, wo das Gerät steht - beim gezogenen Gerät ist das
        nicht dort, wo das Fahrzeug steht."""
        motor = self._motor(trailed=True, hitch_length_m=5.0)
        motor.heading = 0.0
        motor.implement_heading.reset(0.0)
        motor.implement_position = motor.profile.implement_position(
            (50.0, 50.0), 0.0, 0.0)
        self.assertAlmostEqual(motor.implement_position[1], 45.0, places=6)


class TurnDrivenTest(unittest.TestCase):
    """Die Wende einmal wirklich fahren - Einspurmodell gegen Wendeführung.

    Vorzeichen lassen sich in Einzelteilen prüfen und trotzdem im Zusammenspiel
    falsch haben: ein verdrehtes Vorzeichen sähe in jedem einzelnen Test richtig
    aus und schickte die Maschine auf dem Feld in die andere Richtung. Deshalb
    hier einmal die geschlossene Schleife - Position hinein, Einschlag heraus,
    Maschine bewegt, von vorn -, und am Ende die eine Frage: steht sie auf der
    Nachbarspur und schaut sie zurück?
    """

    RASTER = [(0.0, 0.0), (300.0, 0.0), (300.0, 300.0), (0.0, 300.0)]

    def _motor(self):
        import tempfile as tf
        from agripilot import config as config_module
        from agripilot.engine import Engine
        ordner = tf.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "e.db"))
        self.addCleanup(store.close)
        motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
        motor.update_profile({"width_m": 12.0, "overlap_m": 0.0,
                              "antenna_forward_m": 0.0, "tool_trailing_m": 0.0,
                              "wheelbase_m": 2.6, "max_steer_deg": 35.0})
        feld = store.save_field({
            "name": "Wendefeld", "datum_lat": 48.0, "datum_lon": 11.0,
            "boundary": [list(p) for p in self.RASTER], "area_ha": 9.0,
        })
        motor.load_field(feld["id"])
        return motor

    def _fahren(self, motor, start, kurs, schritte, dt=0.1, tempo=1.5):
        """Einspurmodell: der Einschlag aus der Führung bewegt die Maschine."""
        from agripilot.nmea import Fix
        ost, nord, heading = start[0], start[1], kurs
        uhr = 1_000_000.0
        for _ in range(schritte):
            lat, lon = motor.plane.to_wgs(ost, nord)
            motor.on_fix(Fix(lat=lat, lon=lon, fix_quality=4, speed_ms=tempo,
                             course_deg=heading, received_at=uhr))
            einschlag = motor.guidance.steer_angle_deg if motor.guidance.active else 0.0
            drehrate = math.degrees(tempo / 2.6 * math.tan(math.radians(einschlag)))
            heading = (heading + drehrate * dt) % 360.0
            h = math.radians(heading)
            ost += math.sin(h) * tempo * dt
            nord += math.cos(h) * tempo * dt
            uhr += dt
            if motor.turn is None:
                break
        return (ost, nord), heading

    def _wende_fahren(self, muster):
        motor = self._motor()
        motor.line = GuidanceLine("ab", [(150.0, 0.0), (150.0, 300.0)],
                                  motor.profile.spacing_m)
        start, kurs = (150.0, 150.0), 0.0
        motor.tool_position, motor.heading = start, kurs
        motor.update_headland({"muster": muster, "richtung": "links",
                               "radius_m": 6.0, "ueberspringen": 1})
        plan = motor.plan_turn()
        self.assertTrue(plan["im_feld"], "Route liegt nicht im Feld")
        motor.start_turn()
        return motor, self._fahren(motor, start, kurs, schritte=900)

    def test_a_u_turn_ends_on_the_neighbouring_pass_facing_back(self):
        motor, (ende, kurs) = self._wende_fahren("u")
        self.assertIsNone(motor.turn, "Die Wende ist nicht zu Ende gefahren")
        self.assertAlmostEqual(abs(geo.angle_difference(kurs, 180.0)), 0.0, delta=25.0)
        # Eine Arbeitsbreite nach links, also nach Westen.
        self.assertAlmostEqual(ende[0], 138.0, delta=3.0)

    def test_an_omega_turn_ends_on_the_neighbouring_pass_facing_back(self):
        motor, (ende, kurs) = self._wende_fahren("omega")
        self.assertIsNone(motor.turn)
        self.assertAlmostEqual(abs(geo.angle_difference(kurs, 180.0)), 0.0, delta=30.0)
        self.assertAlmostEqual(ende[0], 138.0, delta=4.0)

    def test_after_the_turn_the_line_guides_again(self):
        """Die Wende gibt ab, die Spur übernimmt - sonst stünde die Maschine
        auf der neuen Spur ohne Führung."""
        motor, (ende, kurs) = self._wende_fahren("u")
        motor.tool_position, motor.heading = ende, kurs
        motor._update_guidance(_fix(speed_ms=1.5))
        self.assertEqual(motor.guidance.mode, "ab")
        self.assertEqual(abs(motor.guidance.pass_number), 1)

    def test_the_machine_stays_inside_the_field(self):
        motor, (ende, _) = self._wende_fahren("omega")
        self.assertTrue(geo.point_in_polygon(ende, self.RASTER))


class SimulatorSteeringTest(unittest.TestCase):
    """Wem das virtuelle Lenkrad gehört, solange die Automatik nicht greift."""

    class _Simulator:
        def __init__(self):
            self.steer_deg = 0.0

        def set_steer(self, grad):
            self.steer_deg = grad

    class _Steuerung:
        """Ein Lenkregler, dessen Antwort der Test vorgibt."""

        def __init__(self):
            from agripilot.steering import SteerCommand
            self.armed = False
            self.command = SteerCommand()

        def update(self, guidance, fix, context=None, now=None):
            return self.command

    def _motor(self):
        import tempfile as tf
        from agripilot import config as config_module
        from agripilot.engine import Engine
        ordner = tf.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "e.db"))
        self.addCleanup(store.close)
        motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
        motor.simulator = self._Simulator()
        motor.steering = self._Steuerung()
        return motor

    def test_a_manual_steer_angle_survives_the_next_position(self):
        """Vorher wurde der Regler unter Menü → System zehnmal je Sekunde
        wieder auf gerade gesetzt und war damit wirkungslos."""
        motor = self._motor()
        motor.simulator.set_steer(20.0)
        motor._update_steering(_fix(speed_ms=2.0))
        motor._update_steering(_fix(speed_ms=2.0))
        self.assertAlmostEqual(motor.simulator.steer_deg, 20.0, places=6)

    def test_the_autosteer_angle_wins_while_it_is_engaged(self):
        from agripilot.steering import SteerCommand
        motor = self._motor()
        motor.simulator.set_steer(20.0)
        motor.steering.command = SteerCommand(engaged=True, angle_deg=-7.0)
        motor._update_steering(_fix(speed_ms=2.0))
        self.assertAlmostEqual(motor.simulator.steer_deg, -7.0, places=6)

    def test_disengaging_straightens_the_wheels_once(self):
        """Beim Abschalten geht der Einschlag auf null - danach hat wieder der
        Fahrer das Lenkrad."""
        from agripilot.steering import SteerCommand
        motor = self._motor()
        motor.steering.command = SteerCommand(engaged=True, angle_deg=-7.0)
        motor._update_steering(_fix(speed_ms=2.0))
        motor.steering.command = SteerCommand(engaged=False)
        motor._update_steering(_fix(speed_ms=2.0))
        self.assertAlmostEqual(motor.simulator.steer_deg, 0.0, places=6)
        motor.simulator.set_steer(15.0)
        motor._update_steering(_fix(speed_ms=2.0))
        self.assertAlmostEqual(motor.simulator.steer_deg, 15.0, places=6)


class RohdatenTest(unittest.TestCase):
    """Mitschreiben, was hereinkommt - roh, vor jeder Auswertung."""

    def setUp(self):
        from agripilot import recorder
        self.recorder = recorder
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.aufzeichnung = recorder.Aufzeichnung(self.ordner.name)
        self.addCleanup(self.aufzeichnung.stop)

    def _zeilen(self):
        from pathlib import Path
        return Path(self.aufzeichnung.pfad).read_text(encoding="ascii").splitlines()

    def test_the_file_starts_with_a_readable_header(self):
        self.aufzeichnung.start(jetzt=1000.0)
        self.aufzeichnung.stop()
        zeilen = self._zeilen()
        self.assertEqual(zeilen[0], self.recorder.KOPFZEILE)
        self.assertTrue(zeilen[1].startswith("# begonnen "))

    def test_lines_carry_the_offset_from_the_start(self):
        self.aufzeichnung.start(jetzt=1000.0)
        self.aufzeichnung.nmea("$GNGGA,eins*00", jetzt=1000.0)
        self.aufzeichnung.nmea("$GNGGA,zwei*00", jetzt=1000.25)
        self.aufzeichnung.stop()
        daten = [z for z in self._zeilen() if not z.startswith("#")]
        self.assertEqual(daten[0], "0.000\tN\t$GNGGA,eins*00")
        self.assertEqual(daten[1], "0.250\tN\t$GNGGA,zwei*00")

    def test_the_tilt_is_recorded_alongside(self):
        from agripilot.imu import Attitude
        self.aufzeichnung.start(jetzt=1000.0)
        self.aufzeichnung.imu(Attitude(roll_deg=-1.5, pitch_deg=0.25,
                                       yaw_rate_deg_s=2.0, calibration=3),
                              jetzt=1000.1)
        self.aufzeichnung.stop()
        daten = [z for z in self._zeilen() if not z.startswith("#")]
        self.assertEqual(daten[0], "0.100\tI\t-1.500,0.250,,2.000,3")

    def test_nothing_is_written_before_the_start(self):
        self.aufzeichnung.nmea("$GNGGA,verloren*00")
        self.assertFalse(self.aufzeichnung.laeuft)
        self.assertEqual(self.aufzeichnung.zeilen, 0)

    def test_stopping_keeps_the_last_block(self):
        """Gesammelt wird in Blöcken - beim Beenden darf nichts liegen bleiben."""
        self.aufzeichnung.start(jetzt=1000.0)
        self.aufzeichnung.nmea("$GNGGA,letzter*00", jetzt=1000.0)
        self.aufzeichnung.stop()
        self.assertIn("letzter", "\n".join(self._zeilen()))

    def test_only_our_own_names_are_valid(self):
        """Der Name wird zu einem Pfad. Ohne Prüfung wäre '../../etc/passwd'
        ein gültiger Name einer Aufzeichnung."""
        self.assertTrue(self.recorder.ist_gueltiger_name("rohdaten-20260910-211503.txt"))
        for boese in ("../../etc/passwd", "rohdaten-x.txt", "", "rohdaten-20260910-211503.txt.bak",
                      "unter/rohdaten-20260910-211503.txt"):
            self.assertFalse(self.recorder.ist_gueltiger_name(boese), boese)

    def test_the_listing_finds_recordings_and_their_length(self):
        self.aufzeichnung.start(jetzt=1000.0)
        self.aufzeichnung.nmea("$GNGGA,eins*00", jetzt=1000.0)
        self.aufzeichnung.nmea("$GNGGA,zwei*00", jetzt=1007.5)
        self.aufzeichnung.stop()
        from pathlib import Path
        eintraege = self.recorder.liste(Path(self.ordner.name))
        self.assertEqual(len(eintraege), 1)
        self.assertAlmostEqual(eintraege[0]["dauer_s"], 7.5, places=3)
        self.assertGreater(eintraege[0]["groesse_b"], 0)

    def test_foreign_files_are_not_listed(self):
        from pathlib import Path
        (Path(self.ordner.name) / "notizen.txt").write_text("nichts", encoding="ascii")
        self.assertEqual(self.recorder.liste(Path(self.ordner.name)), [])


class ReplayTest(unittest.IsolatedAsyncioTestCase):
    """Eine aufgezeichnete Fahrt noch einmal - durch dieselbe Rechenkette."""

    def setUp(self):
        from agripilot import recorder
        self.recorder = recorder
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)

    def _satz(self, schritt: int) -> str:
        """Ein GGA-Satz derselben Bauart, die auch der Simulator erzeugt."""
        return nmea.build_gga(48.1370 + schritt * 0.00002, 11.5756,
                              520.0, 4, 22, 0.6)

    def _aufzeichnen(self, schritte=6):
        """Eine kleine Fahrt mitschreiben - Position und Lage im Wechsel."""
        from agripilot.imu import Attitude
        auf = self.recorder.Aufzeichnung(self.ordner.name)
        auf.start(jetzt=1000.0)
        for i in range(schritte):
            t = 1000.0 + i * 0.1
            # Erst die Lage, dann die Position - so herum kommt es auch im Feld:
            # der Neigungssensor liefert deutlich häufiger als der Empfänger,
            # zu jedem Fix liegt also eine frische Lage schon vor.
            auf.imu(Attitude(roll_deg=-2.0 - i, pitch_deg=0.5,
                             yaw_rate_deg_s=1.0, calibration=3), jetzt=t)
            auf.nmea(self._satz(i), jetzt=t)
        auf.stop()
        return auf.pfad

    async def test_a_recorded_drive_comes_back_through_the_whole_chain(self):
        pfad = self._aufzeichnen()
        # Werte merken, nicht das Fix-Objekt: der Parser reicht bei jedem Satz
        # dasselbe Objekt weiter und schreibt es fort. Eine Liste von Fixes wäre
        # sechsmal derselbe letzte Stand.
        gesehen = []
        quelle = self.recorder.ReplaySource(
            lambda f: gesehen.append((f.lat, f.valid)), pfad, tempo=20.0)
        await quelle.abspielen()
        self.assertEqual(len(gesehen), 6)
        self.assertTrue(all(gueltig for _, gueltig in gesehen))
        # Die Fahrt ging nach Norden - die Breitengrade müssen wachsen.
        self.assertLess(gesehen[0][0], gesehen[-1][0])

    async def test_position_and_tilt_stay_in_step(self):
        """Zwei getrennte Leser derselben Datei würden auseinanderlaufen - und
        dann läge die Schräglage neben der Position, also genau der Fehler, den
        man mit der Aufzeichnung untersuchen wollte."""
        pfad = self._aufzeichnen()
        gesehen = []

        def merken(fix):
            gesehen.append((fix.lat, quelle.imu.attitude.roll_deg))

        quelle = self.recorder.ReplaySource(merken, pfad, tempo=20.0)
        await quelle.abspielen()
        # Zu jedem Fix gehört die Lage, die im selben Augenblick aufgezeichnet
        # wurde: Neigung -2, -3, -4 ... in derselben Reihenfolge.
        self.assertEqual([round(r, 1) for _, r in gesehen],
                         [-2.0, -3.0, -4.0, -5.0, -6.0, -7.0])

    async def test_the_recorded_zeroing_is_not_subtracted_twice(self):
        """Aufgezeichnet wird die bereits genullte Lage. Sie beim Abspielen
        noch einmal zu nullen ergäbe den doppelten Versatz."""
        pfad = self._aufzeichnen()
        quelle = self.recorder.ReplaySource(lambda f: None, pfad, tempo=20.0)
        quelle.imu.roll_offset = 5.0        # als hätte jemand genullt
        await quelle.abspielen()
        self.assertAlmostEqual(quelle.imu.attitude.roll_deg, -7.0, places=3)

    async def test_a_missing_recording_does_not_fall_back_to_the_simulator(self):
        """Sonst stünde eine erfundene Fahrt auf dem Bildschirm, während der
        Fahrer glaubt, seine eigene zu sehen."""
        import asyncio as aio
        from pathlib import Path
        quelle = self.recorder.ReplaySource(
            lambda f: None, Path(self.ordner.name) / "gibt-es-nicht.txt")
        aufgabe = aio.create_task(quelle.run())
        await aio.wait_for(quelle.fertig.wait(), 2.0)
        self.assertIn("nicht gefunden", quelle.status)
        self.assertEqual(quelle.lines_received, 0)
        await quelle.stop()
        aufgabe.cancel()

    async def test_it_stops_at_the_end_and_says_so(self):
        import asyncio as aio
        pfad = self._aufzeichnen()
        quelle = self.recorder.ReplaySource(lambda f: None, pfad, tempo=20.0)
        aufgabe = aio.create_task(quelle.run())
        await aio.wait_for(quelle.fertig.wait(), 3.0)
        self.assertIn("zu Ende", quelle.status)
        self.assertEqual(quelle.durchlaeufe, 1)
        await quelle.stop()
        aufgabe.cancel()

    async def test_a_faster_replay_takes_less_time(self):
        pfad = self._aufzeichnen(schritte=10)
        beginn = time.monotonic()
        quelle = self.recorder.ReplaySource(lambda f: None, pfad, tempo=10.0)
        await quelle.abspielen()
        self.assertLess(time.monotonic() - beginn, 0.5)   # roh wären es 0,9 s

    async def test_comment_and_broken_lines_are_skipped(self):
        """Die Datei ist Text und darf von Hand angesehen worden sein."""
        from pathlib import Path
        pfad = Path(self.ordner.name) / "rohdaten-20260910-120000.txt"
        pfad.write_text(
            self.recorder.KOPFZEILE + "\n"
            "# von Hand kommentiert\n"
            "\n"
            "kaputt\n"
            "nichtszahl\tN\t$GPGGA,x*00\n"
            "0.000\tN\t" + self._satz(0) + "\n",
            encoding="ascii")
        fixes = []
        quelle = self.recorder.ReplaySource(fixes.append, pfad, tempo=20.0)
        await quelle.abspielen()
        self.assertEqual(len(fixes), 1)


class RohdatenApiTest(unittest.TestCase):
    """Aufzeichnen über die Schnittstelle - so, wie die Kabine es aufruft."""

    def test_record_list_download_and_delete(self):
        from fastapi.testclient import TestClient
        from agripilot import config as config_module
        from agripilot.server import create_app

        with tempfile.TemporaryDirectory() as ordner:
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = ordner
            config.gnss.source = "simulator"
            with TestClient(create_app(config)) as client:
                self.assertEqual(client.post("/api/rohdaten/start").status_code, 200)
                time.sleep(1.2)
                stand = client.post("/api/rohdaten/stop").json()["data"]
                self.assertFalse(stand["laeuft"])
                self.assertGreater(stand["zeilen"], 0)

                uebersicht = client.get("/api/rohdaten").json()
                self.assertEqual(len(uebersicht["dateien"]), 1)
                name = uebersicht["dateien"][0]["datei"]

                inhalt = client.get(f"/api/rohdaten/{name}")
                self.assertEqual(inhalt.status_code, 200)
                self.assertIn("agripilot-rohdaten", inhalt.text)
                self.assertIn("GGA", inhalt.text)

                # Pfadausflüge werden abgelehnt, nicht befolgt.
                self.assertIn(client.get("/api/rohdaten/..%2F..%2Fconfig.yaml")
                              .status_code, (400, 404))

                self.assertEqual(client.delete(f"/api/rohdaten/{name}").status_code, 200)
                self.assertEqual(len(client.get("/api/rohdaten").json()["dateien"]), 0)

    def test_no_recording_while_a_replay_is_running(self):
        """Gefragt wird die laufende Quelle, nicht die Einstellung: die wechselt
        erst beim Neustart. Sonst zeichnete man die Kopie noch einmal auf."""
        from fastapi.testclient import TestClient
        from agripilot import config as config_module, recorder
        from agripilot.server import create_app

        with tempfile.TemporaryDirectory() as ordner:
            from pathlib import Path
            rohdaten = Path(ordner) / "rohdaten"
            rohdaten.mkdir()
            aufzeichnung = rohdaten / "rohdaten-20260910-120000.txt"
            aufzeichnung.write_text(
                recorder.KOPFZEILE + "\n0.000\tN\t"
                + nmea.build_gga(48.0, 11.0, 500.0, 4, 22, 0.6) + "\n",
                encoding="ascii")

            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = ordner
            config.gnss.source = "replay"
            config.gnss.replay_file = aufzeichnung.name
            with TestClient(create_app(config)) as client:
                self.assertEqual(client.post("/api/rohdaten/start").status_code, 400)
                # Auch wenn jemand die Einstellung schon zurückgestellt hat:
                # gelaufen wird bis zum Neustart weiter auf der Aufzeichnung.
                config.gnss.source = "simulator"
                antwort = client.post("/api/rohdaten/start")
                self.assertEqual(antwort.status_code, 400)
                self.assertIn("Kopie", antwort.json()["detail"])


class QuellenwechselTest(unittest.TestCase):
    """Empfänger und Sensor im Betrieb tauschen - ohne den Dienst durchzustarten."""

    def test_switching_to_a_recording_and_back_without_a_restart(self):
        from fastapi.testclient import TestClient
        from agripilot import config as config_module, recorder
        from agripilot.server import create_app

        with tempfile.TemporaryDirectory() as ordner:
            from pathlib import Path
            rohdaten = Path(ordner) / "rohdaten"
            rohdaten.mkdir()
            datei = rohdaten / "rohdaten-20260911-120000.txt"
            zeilen = [recorder.KOPFZEILE]
            for i in range(30):
                zeilen.append(f"{i * 0.1:.3f}\tI\t-4.000,0.000,,0.000,3")
                zeilen.append(f"{i * 0.1:.3f}\tN\t" + nmea.build_gga(48.2 + i * 0.00002, 11.4, 500.0, 4, 22, 0.6))
            datei.write_text("\n".join(zeilen) + "\n", encoding="ascii")

            # Eine eigene Datei: die Einstellungen werden gespeichert, und das
            # darf nicht in den Sentinel-Pfad der anderen Tests schreiben.
            config = config_module.load(Path(ordner) / "config.yaml")
            config.server.data_dir = ordner
            config.gnss.source = "simulator"
            config.imu.source = "simulator"
            with TestClient(create_app(config)) as client:
                app = client.app.state.app
                app.steering.armed = True          # als wäre der Fahrer scharf
                app.aufzeichnung.start()           # und eine Aufzeichnung liefe
                alte_quelle = app.source

                antwort = client.post("/api/settings", json={"aenderungen": {
                    "gnss.source": "replay", "gnss.replay_file": datei.name,
                    "gnss.replay_speed": 20.0, "gnss.replay_loop": True}})
                self.assertEqual(antwort.status_code, 200)
                daten = antwort.json()["data"]
                self.assertEqual(daten["neustart_noetig"], [])
                self.assertEqual(daten["quelle"]["gnss"], "replay")

                # Die Quelle ist eine andere, die alte steht, die Lenkung ist aus,
                # die Aufzeichnung beendet - nichts davon darf den Wechsel überleben.
                self.assertIsNot(app.source, alte_quelle)
                self.assertIsInstance(app.source, recorder.ReplaySource)
                self.assertFalse(alte_quelle.running)
                self.assertFalse(app.steering.armed)
                self.assertFalse(app.aufzeichnung.laeuft)
                self.assertIs(app.engine.imu, app.source.imu)

                # Die aufgezeichnete Fahrt kommt an: Breitengrad aus der Datei.
                time.sleep(1.0)
                zustand = client.get("/api/state").json()
                self.assertEqual(zustand["system"]["gnss"]["source"], "replay")
                self.assertAlmostEqual(zustand["fix"]["lat"], 48.2, delta=0.001)
                self.assertAlmostEqual(zustand["imu"]["roll_deg"], -4.0, places=2)

                # Und zurück auf den Simulator - ebenfalls ohne Neustart.
                self.assertEqual(client.post("/api/settings", json={"aenderungen": {
                    "gnss.source": "simulator"}}).status_code, 200)
                time.sleep(0.5)
                zustand = client.get("/api/state").json()
                self.assertEqual(zustand["system"]["gnss"]["source"], "simulator")
                self.assertIsNotNone(app.engine.simulator)
                self.assertEqual(client.post("/api/quelle/neustart").status_code, 200)


class OberflaecheTest(unittest.TestCase):
    """Die Kabinenanzeige hat keine eigenen Tests - aber zwei Fehlerklassen
    lassen sich ohne Browser fangen: ein Knopf, den das Programm anspricht und
    den es im HTML nicht gibt, und eine ID, die zweimal vergeben ist. Beides ist
    im Feld unsichtbar, bis jemand genau diesen Knopf drückt."""

    FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "frontend")

    def _lesen(self, name):
        with open(os.path.join(self.FRONTEND, name), encoding="utf-8") as f:
            return f.read()

    def test_every_element_the_script_uses_exists_exactly_once(self):
        import re
        html = self._lesen("index.html")
        js = self._lesen("app.js")
        ids = re.findall(r'\bid="([^"]+)"', html)
        doppelt = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(doppelt, [], f"doppelte IDs im HTML: {doppelt}")
        benutzt = set(re.findall(r"el\('([A-Za-z0-9_]+)'\)", js))
        fehlt = sorted(benutzt - set(ids))
        self.assertEqual(fehlt, [], f"im Programm angesprochen, im HTML nicht vorhanden: {fehlt}")

    def test_the_shell_cache_lists_only_files_that_exist(self):
        """Ein Eintrag in der Hülle, den es nicht gibt, lässt die ganze
        Installation der Kachel scheitern - addAll ist alles oder nichts."""
        import re
        sw = self._lesen("sw.js")
        eintraege = re.findall(r"'(/[^']+)'", sw.split("HUELLE = [")[1].split("];")[0])
        fehlt = [e for e in eintraege if e != "/" and not os.path.exists(
            os.path.join(self.FRONTEND, e.lstrip("/")))]
        self.assertEqual(fehlt, [], f"in der Hülle gelistet, aber nicht vorhanden: {fehlt}")

    def test_fonts_are_bundled_and_referenced_relative_to_the_stylesheet(self):
        """Der Pi hat kein Internet: Schriften müssen mit im Repo liegen, und
        ihre Pfade sind relativ zur CSS-Datei, nicht zur Seite."""
        import re
        css = self._lesen(os.path.join("fonts", "fonts.css"))
        for url in re.findall(r"url\(([^)]+)\)", css):
            self.assertFalse(url.startswith(("http", "/", "fonts/")), url)
            self.assertTrue(os.path.exists(os.path.join(self.FRONTEND, "fonts", url)), url)
        self.assertNotIn("fonts.googleapis", self._lesen("index.html"))


class WendeabbruchTest(unittest.TestCase):
    """Der Befund aus der Durchsicht: nach einem Wendeabbruch fiel die Führung im
    selben Zyklus auf die Spur zurück, deren Abweichung modulo Spurabstand immer
    klein aussieht - und die Lenkung blieb an. Jetzt übernimmt der Fahrer, wirklich."""

    class _Lenkung:
        def __init__(self):
            from agripilot.config import SteeringConfig
            self.config = SteeringConfig(enabled=True, max_cross_track_m=1.5)
            self.armed = True
            self.gruende = []

        def disarm(self, grund="aus"):
            self.armed = False
            self.gruende.append(grund)

    def _motor(self):
        import tempfile as tf
        from agripilot import config as config_module
        from agripilot.engine import Engine
        ordner = tf.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "e.db"))
        self.addCleanup(store.close)
        motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
        motor.update_profile({"width_m": 3.0, "antenna_forward_m": 0.0, "tool_trailing_m": 0.0})
        feld = store.save_field({"name": "Feld", "datum_lat": 48.0, "datum_lon": 11.0,
                                 "boundary": [list(p) for p in QUADRAT], "area_ha": 1.0})
        motor.load_field(feld["id"])
        motor.line = GuidanceLine("ab", [(50.0, 0.0), (50.0, 100.0)], motor.profile.spacing_m)
        motor.steering = self._Lenkung()
        motor.tool_position, motor.heading = (50.0, 50.0), 0.0
        motor.update_headland({"muster": "u", "radius_m": 4.0})
        motor.plan_turn()
        motor.start_turn()
        return motor

    def test_leaving_the_route_disarms_the_steering_and_leaves_no_line_active(self):
        motor = self._motor()
        # Weit neben der Route - aber genau zwischen zwei Spuren, wo die
        # Spurführung "nur 0 m daneben" melden würde.
        motor.tool_position = (56.0, 50.0)
        motor._update_guidance(_fix(speed_ms=1.5))
        self.assertIsNone(motor.turn)
        self.assertFalse(motor.steering.armed)
        self.assertIn("verlassen", motor.steering.gruende[-1])
        self.assertFalse(motor.guidance.active)
        self.assertIn("abgebrochen", motor.guidance.message)

    def test_reaching_the_end_hands_over_to_the_line_with_the_steering_kept(self):
        """Regulär am Ziel: die Route endete geprüft auf der Nachbarspur."""
        motor = self._motor()
        ende = motor.turn.pfad[-1]
        motor.turn.index = len(motor.turn.pfad) - 2
        motor.tool_position, motor.heading = ende, 180.0
        motor._update_guidance(_fix(speed_ms=1.5))
        self.assertIsNone(motor.turn)
        self.assertTrue(motor.steering.armed)
        self.assertTrue(motor.guidance.active)
        self.assertEqual(motor.guidance.mode, "ab")

    def test_the_follower_gives_up_no_later_than_the_steering_would(self):
        motor = self._motor()
        self.assertLessEqual(motor.turn.abbruch_abstand_m, 1.5)


class QuellenwechselAufraeumTest(unittest.IsolatedAsyncioTestCase):
    """Der zweite Befund: cancel() ist nur eine Bitte. Bevor die neue Quelle
    startet, muss die alte wirklich zu Ende sein - sonst hält sie den Port."""

    async def test_old_source_task_is_finished_before_the_new_one_starts(self):
        import asyncio as aio
        import tempfile as tf
        from pathlib import Path
        from agripilot import config as config_module
        from agripilot.server import Application

        with tf.TemporaryDirectory() as ordner:
            config = config_module.load(Path(ordner) / "config.yaml")
            config.server.data_dir = ordner
            config.gnss.source = "simulator"
            app = Application(config)
            await app.start()
            try:
                alte = list(app._quellen_tasks)
                await aio.sleep(0.05)
                await app.quelle_wechseln()
                # Alle alten Aufgaben sind beendet, keine läuft mehr nebenher.
                self.assertTrue(all(t.done() for t in alte))
                self.assertTrue(all(not t.done() for t in app._quellen_tasks))
                self.assertFalse(app.steering.armed)
            finally:
                await app.stop()


class HeadlandApiTest(unittest.TestCase):
    """Die Bedienung von außen - so, wie die Kabine sie aufruft."""

    def test_contour_headland_and_turn_over_the_interface(self):
        from fastapi.testclient import TestClient
        from agripilot import config as config_module
        from agripilot.server import create_app

        with tempfile.TemporaryDirectory() as ordner:
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = ordner
            config.gnss.source = "simulator"
            with TestClient(create_app(config)) as client:
                feld = client.post("/api/fields", json={"name": "Kontur"}).json()
                feld_id = feld["data"]["id"]

                # Ohne Grenze gibt es keine Kontur - und das steht auch so da.
                antwort = client.post("/api/guidance/contour")
                self.assertEqual(antwort.status_code, 400)
                self.assertIn("Feldgrenze", antwort.json()["detail"])

                app = client.app.state.app
                app.engine.save_boundary([(0.0, 0.0), (200.0, 0.0),
                                          (200.0, 200.0), (0.0, 200.0)])
                self.assertEqual(client.post("/api/guidance/contour").status_code, 200)
                zustand = client.get("/api/state").json()
                self.assertEqual(zustand["line"]["mode"], "contour")
                self.assertTrue(zustand["line"]["derived"])

                # Vorgewende einstellen und wiederfinden
                self.assertEqual(client.post("/api/headland", json={
                    "spuren": 3, "muster": "u", "alarm_abstand_m": 25.0,
                }).status_code, 200)
                vorgewende = client.get("/api/headland").json()
                self.assertEqual(vorgewende["spuren"], 3)
                self.assertEqual(vorgewende["muster"], "u")

                # Der Simulator fährt - danach lässt sich eine Wende planen.
                time.sleep(1.2)
                plan = client.post("/api/turn/plan", json={"richtung": "links"})
                self.assertEqual(plan.status_code, 200)
                self.assertGreater(len(plan.json()["data"]["punkte"]), 10)
                self.assertEqual(client.post("/api/turn/stop").status_code, 200)
                self.assertNotEqual(feld_id, "")


# ---------------------------------------------------------------- Shapefile


def _tm_vorwaerts(lat_deg, lon_deg, lon0_deg=9.0, k0=0.9996, false_e=500_000.0,
                  a=6_378_137.0, f_inv=298.257222101):
    """Transversale Mercator-Abbildung vorwärts (Snyder 8-9 bis 8-13) - unabhängig
    vom Programm geschrieben, damit die Umkehrung dort gegen etwas geprüft wird."""
    f = 1.0 / f_inv
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    phi, lam = math.radians(lat_deg), math.radians(lon_deg - lon0_deg)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t_ = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    a_ = lam * math.cos(phi)
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    x = k0 * n * (a_ + (1 - t_ + c) * a_ ** 3 / 6
                  + (5 - 18 * t_ + t_ ** 2 + 72 * c - 58 * ep2) * a_ ** 5 / 120)
    y = k0 * (m + n * math.tan(phi) * (a_ ** 2 / 2 + (5 - t_ + 9 * c + 4 * c ** 2) * a_ ** 4 / 24
                                        + (61 - 58 * t_ + t_ ** 2 + 600 * c - 330 * ep2) * a_ ** 6 / 720))
    return x + false_e, y


UTM32_PRJ = ('PROJCS["ETRS_1989_UTM_Zone_32N",GEOGCS["GCS_ETRS_1989",DATUM["D_ETRS_1989",'
             'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
             'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
             'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
             'PARAMETER["Central_Meridian",9.0],PARAMETER["Scale_Factor",0.9996],'
             'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]')
WGS84_PRJ = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,'
             '298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
GK4_PRJ = ('PROJCS["DHDN_3_Degree_Gauss_Zone_4",GEOGCS["GCS_Deutsches_Hauptdreiecksnetz",'
           'DATUM["D_Deutsches_Hauptdreiecksnetz",SPHEROID["Bessel_1841",6377397.155,'
           '299.1528128]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
           'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",4500000.0],'
           'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",12.0],'
           'PARAMETER["Scale_Factor",1.0],PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]')


def _shp_bauen(polygone):
    """Ein Shapefile (Typ 5) aus Listen von Ringen; jeder Ring eine Liste (x, y).
    Ringe werden geschlossen, wie das Format es verlangt."""
    import struct
    saetze = b""
    for nummer, ringe in enumerate(polygone, 1):
        geschlossen = [r + [r[0]] for r in ringe]
        punkte = [p for r in geschlossen for p in r]
        xs, ys = [p[0] for p in punkte], [p[1] for p in punkte]
        inhalt = struct.pack("<i", 5) + struct.pack("<4d", min(xs), min(ys), max(xs), max(ys))
        inhalt += struct.pack("<ii", len(geschlossen), len(punkte))
        start = 0
        for r in geschlossen:
            inhalt += struct.pack("<i", start)
            start += len(r)
        for x, y in punkte:
            inhalt += struct.pack("<dd", x, y)
        saetze += struct.pack(">ii", nummer, len(inhalt) // 2) + inhalt
    laenge = (100 + len(saetze)) // 2
    kopf = struct.pack(">i", 9994) + b"\x00" * 20 + struct.pack(">i", laenge)
    kopf += struct.pack("<ii", 1000, 5) + struct.pack("<8d", 0, 0, 0, 0, 0, 0, 0, 0)
    return kopf + saetze


def _dbf_bauen(spalten, zeilen, codepage=0x57, kodierung="cp1252"):
    import struct
    satz_laenge = 1 + sum(l for _, l in spalten)
    kopf_laenge = 32 + 32 * len(spalten) + 1
    kopf = bytearray(32)
    kopf[0] = 0x03
    struct.pack_into("<IHH", kopf, 4, len(zeilen), kopf_laenge, satz_laenge)
    kopf[29] = codepage
    daten = bytes(kopf)
    for name, laenge in spalten:
        feld = bytearray(32)
        feld[:11] = name.encode("ascii").ljust(11, b"\x00")
        feld[11] = ord("C")
        feld[16] = laenge
        daten += bytes(feld)
    daten += b"\x0D"
    for zeile in zeilen:
        satz = b" "
        for (name, laenge), wert in zip(spalten, zeile):
            satz += wert.encode(kodierung).ljust(laenge, b" ")[:laenge]
        daten += satz
    return daten


class ShapefileTest(unittest.TestCase):
    """Feldgrenzen aus dem Flächenantrag - selbst gelesen, ohne Zusatzpakete."""

    # Ein Rechteck von 100 x 200 m bei Freising, gegen den Uhrzeigersinn in Grad
    # (Shapefile will Außenringe im Uhrzeigersinn - hier wird es umgedreht).
    ECKEN_WGS = [(48.40, 11.70), (48.40, 11.70135), (48.4018, 11.70135), (48.4018, 11.70)]

    def _utm_ring(self):
        ring = [_tm_vorwaerts(lat, lon) for lat, lon in self.ECKEN_WGS]
        return list(reversed(ring))   # im Uhrzeigersinn = Außenring

    def test_utm32_polygon_lands_on_the_right_spot(self):
        from agripilot import shapefile
        shp = _shp_bauen([[self._utm_ring()]])
        dbf = _dbf_bauen([("SCHLAGNAME", 30), ("FLIK", 16)], [("Große Wiese", "DEBYLI0123")])
        umrisse = shapefile.lesen(shp, dbf, UTM32_PRJ)
        self.assertEqual(len(umrisse), 1)
        self.assertEqual(umrisse[0].name, "Große Wiese")
        # Jede Ecke muss auf einen Zentimeter wieder dort liegen, wo sie herkam.
        for (lat, lon) in self.ECKEN_WGS:
            naechste = min(umrisse[0].ring, key=lambda p: geo.haversine(lat, lon, p[0], p[1]))
            self.assertLess(geo.haversine(lat, lon, naechste[0], naechste[1]), 0.01)
        feld = umrisse[0].als_feld()
        self.assertAlmostEqual(feld["area_ha"], 2.0, delta=0.01)   # 100 x 200 m
        self.assertEqual(len(feld["boundary"]), 4)

    def test_zone_prefix_in_the_easting_is_understood_with_and_without_prj(self):
        from agripilot import shapefile
        ring = [(x + 32_000_000, y) for x, y in self._utm_ring()]
        shp = _shp_bauen([[ring]])
        ohne = shapefile.lesen(shp, None, None)
        mit = shapefile.lesen(shp, None, UTM32_PRJ.replace('"False_Easting",500000.0',
                                                            '"False_Easting",32500000.0'))
        for umriss in (ohne[0], mit[0]):
            self.assertLess(geo.haversine(48.40, 11.70, *min(
                umriss.ring, key=lambda p: geo.haversine(48.40, 11.70, *p))), 0.01)
        self.assertEqual(ohne[0].name, "Feld 1")

    def test_degrees_are_taken_as_they_are(self):
        from agripilot import shapefile
        ring = [(lon, lat) for lat, lon in reversed(self.ECKEN_WGS)]
        umrisse = shapefile.lesen(_shp_bauen([[ring]]), None, WGS84_PRJ)
        self.assertEqual(umrisse[0].ring[0], (self.ECKEN_WGS[-1][0], self.ECKEN_WGS[-1][1]))
        # ... und ohne .prj erkannt: Zahlen unter 180 sind Grad.
        self.assertEqual(shapefile.lesen(_shp_bauen([[ring]]), None, None)[0].ring,
                         umrisse[0].ring)

    def test_gauss_krueger_on_bessel_is_refused_with_a_reason(self):
        from agripilot import shapefile
        shp = _shp_bauen([[[(4_500_000, 5_360_000), (4_500_100, 5_360_000),
                            (4_500_100, 5_360_100), (4_500_000, 5_360_100)][::-1]]])
        with self.assertRaises(shapefile.ShapefileFehler) as fehler:
            shapefile.lesen(shp, None, GK4_PRJ)
        self.assertIn("Bessel", str(fehler.exception))
        self.assertIn("EPSG:25832", str(fehler.exception))

    def test_unknown_numbers_without_prj_are_an_error_not_a_guess(self):
        from agripilot import shapefile
        shp = _shp_bauen([[[(500_000, 5_360_000), (500_100, 5_360_000),
                            (500_100, 5_360_100), (500_000, 5_360_100)][::-1]]])
        with self.assertRaises(shapefile.ShapefileFehler) as fehler:
            shapefile.lesen(shp, None, None)
        self.assertIn(".prj", str(fehler.exception))

    def test_holes_are_dropped_and_several_outer_rings_become_several_fields(self):
        from agripilot import shapefile
        aussen = self._utm_ring()
        # Ein Loch: kleiner Ring gegen den Uhrzeigersinn mitten drin.
        cx = sum(p[0] for p in aussen) / 4
        cy = sum(p[1] for p in aussen) / 4
        loch = [(cx - 5, cy - 5), (cx + 5, cy - 5), (cx + 5, cy + 5), (cx - 5, cy + 5)]
        zweites = [(x + 1000, y) for x, y in aussen]
        umrisse = shapefile.lesen(_shp_bauen([[aussen, loch, zweites]]),
                                  _dbf_bauen([("NAME", 20)], [("Doppel",)]), UTM32_PRJ)
        self.assertEqual([u.name for u in umrisse], ["Doppel (1)", "Doppel (2)"])
        self.assertEqual(len(umrisse[0].ring), 4)

    def test_dbf_names_come_through_the_codepage(self):
        from agripilot import shapefile
        dbf = _dbf_bauen([("BEZEICHN", 20)], [("Föhrenäcker",)], codepage=0x02, kodierung="cp850")
        self.assertEqual(shapefile.dbf_lesen(dbf)[0]["BEZEICHN"], "Föhrenäcker")
        dbf = _dbf_bauen([("ID", 6), ("SCHLAG", 20)], [("17", "Am Bach")])
        umriss = shapefile.lesen(_shp_bauen([[self._utm_ring()]]), dbf, UTM32_PRJ)[0]
        self.assertEqual(umriss.name, "Am Bach")   # der Name schlägt die Nummer

    def test_garbage_is_refused(self):
        from agripilot import shapefile
        with self.assertRaises(shapefile.ShapefileFehler):
            shapefile.lesen(b"\x00" * 200, None, None)
        with self.assertRaises(shapefile.ShapefileFehler):
            shapefile.lesen(_shp_bauen([]), None, None)


class ShapefileImportTest(unittest.TestCase):
    """Der Import in den Motor: Felder anlegen, Namen wiedererkennen, Spuren behalten."""

    def _motor(self):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "e.db"))
        self.addCleanup(store.close)
        return Engine(config_module.load("/kein-solcher-pfad.yaml"), store), store

    def test_import_creates_fields_and_reimport_keeps_id_and_lines(self):
        from agripilot import shapefile
        motor, store = self._motor()
        ring = list(reversed([_tm_vorwaerts(lat, lon) for lat, lon in ShapefileTest.ECKEN_WGS]))
        shp = _shp_bauen([[ring]])
        dbf = _dbf_bauen([("NAME", 20)], [("Wiese",)])
        felder = motor.import_fields(shapefile.lesen(shp, dbf, UTM32_PRJ))
        self.assertEqual(len(felder), 1)
        feld = felder[0]
        self.assertAlmostEqual(feld["area_ha"], 2.0, delta=0.01)
        self.assertGreater(len(feld["boundary"]), 3)
        # Eine Spur dazu ...
        spur = store.save_line({"field_id": feld["id"], "name": "AB", "mode": "ab",
                                "points": [[0, 0], [0, 50]], "spacing_m": 3.0})
        # ... dann kommt die Datei noch einmal, die Grenze leicht anders.
        ring2 = list(reversed([_tm_vorwaerts(lat, lon + 0.00002)
                               for lat, lon in ShapefileTest.ECKEN_WGS]))
        felder2 = motor.import_fields(shapefile.lesen(_shp_bauen([[ring2]]), dbf, UTM32_PRJ))
        self.assertEqual(felder2[0]["id"], feld["id"])
        self.assertEqual(len(store.list_fields()), 1)
        self.assertEqual(store.get_line(spur["id"])["field_id"], feld["id"])
        # Der Bezug blieb, die Grenze wanderte - um etwa 1,5 m nach Osten.
        self.assertEqual((felder2[0]["datum_lat"], felder2[0]["datum_lon"]),
                         (feld["datum_lat"], feld["datum_lon"]))
        self.assertAlmostEqual(felder2[0]["boundary"][0][0] - feld["boundary"][0][0], 1.48, delta=0.1)


# ----------------------------------------------------------- Saisonspuren


class SaisonspurTest(unittest.TestCase):
    """Fahrgassen bleiben das Jahr über: die Spur vom Säen wird beim Düngen
    wieder die Spur - ohne dass jemand daran denken muss."""

    def setUp(self):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        self.dir = tempfile.TemporaryDirectory()
        self.store = Storage(os.path.join(self.dir.name, "s.db"))
        self.engine = Engine(config_module.load("/kein-solcher-pfad.yaml"), self.store)
        self.feld = self.store.save_field({"name": "Acker", "datum_lat": 48.0, "datum_lon": 11.0,
                                           "boundary": [list(p) for p in QUADRAT], "area_ha": 1.0})

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def _spur(self, name, **extra):
        return self.store.save_line({"field_id": self.feld["id"], "name": name, "mode": "ab",
                                     "points": [[10, 0], [10, 100]], "spacing_m": 3.0, **extra})

    def test_an_old_database_gets_the_new_columns(self):
        import sqlite3
        pfad = os.path.join(self.dir.name, "alt.db")
        alt = sqlite3.connect(pfad)
        alt.execute("""CREATE TABLE lines (id TEXT PRIMARY KEY, field_id TEXT NOT NULL,
            name TEXT NOT NULL, mode TEXT NOT NULL, points TEXT NOT NULL, spacing_m REAL NOT NULL,
            nudge_m REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)""")
        alt.execute("INSERT INTO lines VALUES ('l1','f1','Alt','ab','[[0,0],[0,10]]',3,0,1,0)")
        alt.commit(); alt.close()
        store = Storage(pfad)
        try:
            spur = store.get_line("l1")
            self.assertEqual((spur["saison"], spur["fahrgasse_m"]), (0, 0.0))
            store.update_line("l1", saison=2026, fahrgasse_m=24.0)
            self.assertEqual(store.get_line("l1")["fahrgasse_m"], 24.0)
        finally:
            store.close()   # vor tearDown: Windows löscht keine offene Datei

    def test_the_season_line_is_loaded_with_the_field(self):
        jahr = time.localtime().tm_year
        self._spur("Zuletzt")
        saison = self._spur("Saat", saison=jahr, fahrgasse_m=24.0)
        self._spur("Vorjahr", saison=jahr - 1, fahrgasse_m=24.0)
        self.engine.load_field(self.feld["id"])
        self.assertEqual(self.engine.line.id, saison["id"])
        self.assertEqual(self.engine.line.fahrgasse_m, 24.0)
        self.assertEqual(self.engine.line.fahrgasse_jede(), 8)
        self.assertTrue(self.engine.line.ist_fahrgasse(8))
        self.assertTrue(self.engine.line.ist_fahrgasse(-16))
        self.assertFalse(self.engine.line.ist_fahrgasse(3))
        self.assertEqual(self.engine.state()["line"]["fahrgasse_jede"], 8)

    def test_without_a_season_line_the_newest_line_is_loaded(self):
        erste = self._spur("Erste")
        time.sleep(0.01)
        zweite = self._spur("Zweite")
        self.engine.load_field(self.feld["id"])
        self.assertEqual(self.engine.line.id, zweite["id"])
        self.assertEqual(self.engine.line.fahrgasse_jede(), 0)
        self.assertIsNotNone(erste)

    def test_setting_the_tramline_spacing_marks_the_season_and_updates_the_active_line(self):
        jahr = time.localtime().tm_year
        spur = self._spur("Saat")
        self.engine.load_field(self.feld["id"])
        record = self.engine.update_line(spur["id"], {"fahrgasse_m": 18})
        self.assertEqual((record["saison"], record["fahrgasse_m"]), (jahr, 18.0))
        self.assertEqual(self.engine.line.fahrgasse_jede(), 6)
        self.assertEqual(self.store.season_line(self.feld["id"], jahr)["id"], spur["id"])
        # Null nimmt die Fahrgassen und die Saison wieder weg.
        record = self.engine.update_line(spur["id"], {"fahrgasse_m": 0})
        self.assertEqual((record["saison"], record["fahrgasse_m"]), (0, 0.0))
        self.assertIsNone(self.store.season_line(self.feld["id"], jahr))
        with self.assertRaises(ValueError):
            self.engine.update_line(spur["id"], {"fahrgasse_m": -1})
        with self.assertRaises(KeyError):
            self.engine.update_line("gibt-es-nicht", {"name": "x"})
        self.assertEqual(self.engine.update_line(spur["id"], {"name": "  Neu "})["name"], "Neu")

    def test_the_newest_season_line_wins(self):
        jahr = time.localtime().tm_year
        alt = self._spur("Erste Saat", saison=jahr, fahrgasse_m=24.0)
        time.sleep(0.01)
        neu = self._spur("Nachgesät", saison=jahr, fahrgasse_m=24.0)
        self.assertEqual(self.store.season_line(self.feld["id"], jahr)["id"], neu["id"])
        self.assertEqual([l["id"] for l in self.store.list_lines(self.feld["id"])][:2],
                         [neu["id"], alt["id"]])


class GrenzeZuKleinTest(unittest.TestCase):
    """Eine Gerade als Grenze abschließen darf die echte Grenze nicht ersetzen."""

    def test_a_degenerate_boundary_is_refused_and_the_old_one_kept(self):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        with tempfile.TemporaryDirectory() as ordner:
            store = Storage(os.path.join(ordner, "g.db"))
            try:
                motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
                feld = store.save_field({"name": "Feld", "datum_lat": 48.0, "datum_lon": 11.0,
                                         "boundary": [list(p) for p in QUADRAT], "area_ha": 1.0})
                motor.load_field(feld["id"])
                with self.assertRaises(RuntimeError) as fehler:
                    motor.save_boundary([(0.0, 0.0), (0.0, 50.0), (0.0, 100.0), (0.1, 150.0)])
                self.assertIn("zu klein", str(fehler.exception))
                self.assertEqual(store.get_field(feld["id"])["boundary"], [list(p) for p in QUADRAT])
                self.assertEqual(motor.field["boundary"], [list(p) for p in QUADRAT])
            finally:
                store.close()


class VersatzVomSitzAusTest(unittest.TestCase):
    """„10 cm rechts" heißt rechts vom Fahrer - auch rückwärts auf der AB-Spur
    und auf dem Ring gegen den Uhrzeigersinn. Vorher hieß es rechts von A nach
    B bzw. nach innen, und der Fahrer sah die Spur zur falschen Seite springen."""

    def _motor(self):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "n.db"))
        self.addCleanup(store.close)
        motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
        motor.update_profile({"width_m": 3.0, "antenna_forward_m": 0.0, "tool_trailing_m": 0.0})
        feld = store.save_field({"name": "Feld", "datum_lat": 48.0, "datum_lon": 11.0,
                                 "boundary": [list(p) for p in QUADRAT], "area_ha": 1.0})
        motor.load_field(feld["id"])
        return motor

    def _fuehren(self, motor, position, heading):
        motor.tool_position, motor.heading, motor.position = position, heading, position
        motor._update_guidance(_fix(speed_ms=2.0))
        return motor.guidance

    def test_reverse_pass_right_is_still_the_drivers_right(self):
        motor = self._motor()
        motor.line = GuidanceLine("ab", [(50.0, 0.0), (50.0, 100.0)], 3.0)
        # Nach Süden, also entgegen A→B. Rechts vom Fahrer ist Westen.
        vorher = self._fuehren(motor, (50.0, 50.0), 180.0)
        self.assertTrue(vorher.reversed_direction)
        motor.nudge(0.10)
        nachher = self._fuehren(motor, (50.0, 50.0), 180.0)
        # Die Spur liegt jetzt 10 cm rechts vom Fahrer: er steht links davon.
        self.assertAlmostEqual(nachher.cross_track_m, -0.10, places=3)
        self.assertAlmostEqual(motor.line.nudge_m, -0.10, places=3)   # im Muster: nach Westen

    def test_forward_pass_unchanged(self):
        motor = self._motor()
        motor.line = GuidanceLine("ab", [(50.0, 0.0), (50.0, 100.0)], 3.0)
        self._fuehren(motor, (50.0, 50.0), 0.0)
        motor.nudge(0.10)
        nachher = self._fuehren(motor, (50.0, 50.0), 0.0)
        self.assertAlmostEqual(nachher.cross_track_m, -0.10, places=3)
        self.assertAlmostEqual(motor.line.nudge_m, 0.10, places=3)

    def test_contour_right_is_the_drivers_right_either_way_round(self):
        motor = self._motor()
        motor.use_contour()
        # Auf der Westkante (x = 0) nach Norden: innen ist rechts (Osten).
        self._fuehren(motor, (0.0, 50.0), 0.0)
        motor.nudge(0.10)
        nachher = self._fuehren(motor, (0.0, 50.0), 0.0)
        self.assertAlmostEqual(nachher.cross_track_m, -0.10, places=2)
        # Dieselbe Kante nach Süden: innen ist jetzt links. "Rechts" muss
        # trotzdem rechts vom Fahrer liegen, also nach Westen (aus dem Feld).
        motor.line.nudge_m = 0.0
        self._fuehren(motor, (0.0, 50.0), 180.0)
        motor.nudge(0.10)
        nachher = self._fuehren(motor, (0.0, 50.0), 180.0)
        self.assertAlmostEqual(nachher.cross_track_m, -0.10, places=2)

    def test_startpunkt_im_feld(self):
        motor = self._motor()
        self.assertEqual(motor.startpunkt_im_feld(), (50.0, 50.0, 0.0))
        motor.line = GuidanceLine("ab", [(20.0, 10.0), (20.0, 90.0)], 3.0)
        self.assertEqual(motor.startpunkt_im_feld(), (20.0, 10.0, 0.0))


class GeraetHinterAchseTest(unittest.TestCase):
    """Andys Spritze: 5 m hinter der Achse, gezogen. Die Führung schaukelte
    sich im Simulator auf ±1,75 m auf und die Lenkung schaltete im Sekundentakt
    ab - weil auf einen Punkt hinter der Hinterachse gelenkt wurde. Der wandert
    beim Einlenken erst zur falschen Seite. Gelenkt wird jetzt auf die Achse;
    markiert wird weiterhin am Gerät."""

    def _motor(self, **profil):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "s.db"))
        self.addCleanup(store.close)
        motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
        motor.update_profile({"width_m": 21.0, "antenna_forward_m": 1.2, "wheelbase_m": 2.6,
                              "tool_trailing_m": 5.0, "trailed": True, "hitch_length_m": 4.0,
                              "steer_gain": 0.9, "max_steer_deg": 35.0, **profil})
        feld = store.save_field({"name": "Feld", "datum_lat": 48.0, "datum_lon": 11.0,
                                 "boundary": [[0, 0], [400, 0], [400, 400], [0, 400]], "area_ha": 16.0})
        motor.load_field(feld["id"])
        motor.line = GuidanceLine("ab", [(100.0, 0.0), (100.0, 400.0)], motor.profile.spacing_m)
        return motor

    def _fahren(self, motor, start, kurs, sekunden, tempo=2.5, dt=0.1):
        from agripilot.nmea import Fix
        ost, nord, heading = start[0], start[1], kurs
        uhr = 1_000_000.0
        fehler = []
        for i in range(int(sekunden / dt)):
            lat, lon = motor.plane.to_wgs(ost, nord)
            motor.on_fix(Fix(lat=lat, lon=lon, fix_quality=4, speed_ms=tempo,
                             course_deg=heading, received_at=uhr))
            einschlag = motor.guidance.steer_angle_deg if motor.guidance.active else 0.0
            # Der Lenkmotor braucht seine Zeit: höchstens 25°/s wie im Profil.
            drehrate = math.degrees(tempo / 2.6 * math.tan(math.radians(einschlag)))
            heading = (heading + drehrate * dt) % 360.0
            h = math.radians(heading)
            ost += math.sin(h) * tempo * dt
            nord += math.cos(h) * tempo * dt
            uhr += dt
            fehler.append(ost - 100.0)     # Abstand der Antenne von der Spur
        return fehler

    def test_the_sprayer_settles_on_the_line_instead_of_oscillating(self):
        motor = self._motor()
        fehler = self._fahren(motor, (101.0, 20.0), 0.0, sekunden=40)
        spaet = fehler[len(fehler) // 2:]
        self.assertLess(max(abs(f) for f in spaet), 0.10,
                        f"schaukelt: {[round(f, 2) for f in spaet[::20]]}")

    def test_the_sprayer_drives_a_turn_onto_the_neighbouring_pass(self):
        """Die Route wird ab der Achse geplant - ab dem Gerät geplant läge sie
        sieben Meter hinter der Maschine und der Folger bräche sofort ab."""
        from agripilot.nmea import Fix
        motor = self._motor()
        motor.update_headland({"aktiv": True, "muster": "omega", "richtung": "links",
                               "radius_m": 6.0, "ueberspringen": 0, "spuren": 2})
        # Anfahren, damit Position und Achse gesetzt sind, dann planen.
        self._fahren(motor, (100.0, 100.0), 0.0, sekunden=4)
        plan = motor.plan_turn()
        self.assertTrue(plan["im_feld"])
        motor.start_turn()
        ost, nord, heading = motor.position[0], motor.position[1], motor.heading
        uhr, tempo, dt = 2_000_000.0, 2.0, 0.1
        for _ in range(900):
            lat, lon = motor.plane.to_wgs(ost, nord)
            motor.on_fix(Fix(lat=lat, lon=lon, fix_quality=4, speed_ms=tempo,
                             course_deg=heading, received_at=uhr))
            einschlag = motor.guidance.steer_angle_deg if motor.guidance.active else 0.0
            drehrate = math.degrees(tempo / 2.6 * math.tan(math.radians(einschlag)))
            heading = (heading + drehrate * dt) % 360.0
            h = math.radians(heading)
            ost += math.sin(h) * tempo * dt
            nord += math.cos(h) * tempo * dt
            uhr += dt
            if motor.turn is None:
                break
        self.assertIsNone(motor.turn, "Wende nicht zu Ende")
        self.assertNotIn("abgebrochen", motor.guidance.message)
        self.assertFalse(motor.state()["turn"]["geplant"])   # Route ist verbraucht
        self.assertAlmostEqual(heading, 180.0, delta=15.0)
        self.assertAlmostEqual(ost, 100.0 - motor.profile.spacing_m, delta=1.5)

    def test_marking_still_happens_at_the_implement(self):
        motor = self._motor()
        self._fahren(motor, (100.0, 20.0), 0.0, sekunden=4)
        # Das Gerät hängt 5 m hinter der Achse: es liegt südlich der Antenne.
        self.assertLess(motor.implement_position[1], motor.position[1] - 4.0)
        # Gelenkt wird auf die Achse - der Führungspunkt liegt nicht hinten am Gerät.
        self.assertGreater(motor.steer_position[1], motor.implement_position[1] + 3.0)


class APlusErsetztTest(unittest.TestCase):
    """A+ zweimal gedrückt ist eine Spur, nicht zwei - außer die erste ist Saisonspur."""

    def _motor(self):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        store = Storage(os.path.join(ordner.name, "a.db"))
        self.addCleanup(store.close)
        motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
        feld = store.save_field({"name": "Feld", "datum_lat": 48.0, "datum_lon": 11.0,
                                 "boundary": [list(p) for p in QUADRAT], "area_ha": 1.0})
        motor.load_field(feld["id"])
        motor.tool_position, motor.heading = (50.0, 50.0), 0.0
        motor.fix = _fix()
        return motor, store

    def test_pressing_a_plus_again_replaces_the_previous_a_plus_line(self):
        motor, store = self._motor()
        erste = motor.set_ab_from_heading(0.0)
        zweite = motor.set_ab_from_heading(90.0)
        self.assertEqual(erste["id"], zweite["id"])
        self.assertEqual(len(store.list_lines()), 1)
        self.assertEqual(zweite["name"], "A+ 90°")
        self.assertEqual(motor.line.id, zweite["id"])

    def test_a_season_line_and_a_named_line_are_not_overwritten(self):
        motor, store = self._motor()
        erste = motor.set_ab_from_heading(0.0)
        motor.update_line(erste["id"], {"fahrgasse_m": 24})
        zweite = motor.set_ab_from_heading(45.0)
        self.assertNotEqual(erste["id"], zweite["id"])
        dritte = motor.set_ab_from_heading(10.0, name="Hauptrichtung")
        self.assertNotEqual(zweite["id"], dritte["id"])
        self.assertEqual(len(store.list_lines()), 3)


class ArbeitVerworfenTest(unittest.TestCase):
    """Markieren an, Markieren aus, nichts gefahren - keine Zeile in der Liste."""

    def test_a_job_without_distance_is_discarded(self):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        with tempfile.TemporaryDirectory() as ordner:
            store = Storage(os.path.join(ordner, "v.db"))
            try:
                motor = Engine(config_module.load("/kein-solcher-pfad.yaml"), store)
                feld = store.save_field({"name": "Feld", "datum_lat": 48.0, "datum_lon": 11.0,
                                         "boundary": [], "area_ha": 0.0})
                motor.load_field(feld["id"])
                motor.start_job("Grubbern")
                ergebnis = motor.stop_job()
                self.assertTrue(ergebnis["verworfen"])
                self.assertEqual(store.list_jobs(), [])
                # Mit Strecke bleibt die Arbeit.
                motor.start_job("Grubbern")
                motor.distance_m = 40.0
                ergebnis = motor.stop_job()
                self.assertNotIn("verworfen", ergebnis)
                self.assertEqual(len(store.list_jobs()), 1)
            finally:
                store.close()


class VerwaisteArbeitTest(unittest.TestCase):
    """Zündung aus statt „Arbeit beenden": beim nächsten Start wird die offene
    Arbeit dieses Geräts abgeschlossen - die eines anderen Traktors nicht."""

    def test_open_jobs_of_this_device_are_closed_at_start(self):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        with tempfile.TemporaryDirectory() as ordner:
            store = Storage(os.path.join(ordner, "j.db"))
            try:
                eigene = store.start_job("feld", "pi-test", "Traktor", "Grubbern")
                fremde = store.start_job("feld", "pi-anderer", "Fendt", "Säen")
                config = config_module.load("/kein-solcher-pfad.yaml")
                config.network.device_id = "pi-test"
                motor = Engine(config, store)
                self.assertIsNotNone(store.get_job(eigene["id"])["ended_at"])
                self.assertIsNone(store.get_job(fremde["id"])["ended_at"])
                self.assertIn("abgeschlossen", motor.messages[-1])
                # Beim zweiten Start gibt es nichts mehr zu schließen.
                self.assertEqual(store.close_orphan_jobs("pi-test"), 0)
            finally:
                store.close()


# --------------------------------------------------------------- Maschinen


class MaschinenTest(unittest.TestCase):
    """Mehrere Maschinen, eine davon aktiv - und das aktive Profil bleibt, was es war."""

    def setUp(self):
        from agripilot import config as config_module
        from agripilot.engine import Engine
        self.dir = tempfile.TemporaryDirectory()
        self.store = Storage(os.path.join(self.dir.name, "m.db"))
        self.engine = Engine(config_module.load("/kein-solcher-pfad.yaml"), self.store)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_the_existing_profile_becomes_the_first_machine(self):
        self.engine.update_profile({"name": "Fendt", "width_m": 6.0})
        liste = self.engine.list_profiles()
        self.assertEqual(len(liste), 1)
        self.assertEqual((liste[0]["name"], liste[0]["width_m"], liste[0]["aktiv"]), ("Fendt", 6.0, True))
        # Änderungen am aktiven Profil landen in der Liste.
        self.engine.update_profile({"width_m": 9.0})
        self.assertEqual(self.engine.list_profiles()[0]["width_m"], 9.0)

    def test_new_select_delete(self):
        from agripilot.engine import Engine
        self.engine.update_profile({"name": "Fendt", "width_m": 6.0})
        spritze = self.engine.save_profile_as("Spritze", {"width_m": 24.0})
        self.assertEqual(self.engine.profile.name, "Spritze")
        self.assertEqual(self.engine.profile.width_m, 24.0)
        liste = self.engine.list_profiles()
        self.assertEqual([p["name"] for p in liste], ["Fendt", "Spritze"])
        self.assertEqual([p["aktiv"] for p in liste], [False, True])
        fendt = liste[0]["id"]
        self.engine.select_profile(fendt)
        self.assertEqual((self.engine.profile.name, self.engine.profile.width_m), ("Fendt", 6.0))
        # Ein Neustart liest das aktive Profil wieder ein.
        neu = Engine(self.engine.config, self.store)
        self.assertEqual(neu.profile.name, "Fendt")
        self.assertEqual([p["aktiv"] for p in neu.list_profiles()], [True, False])
        # Löschen der aktiven wechselt auf die verbleibende; die letzte bleibt.
        self.engine.delete_profile(fendt)
        self.assertEqual(self.engine.profile.name, "Spritze")
        with self.assertRaises(ValueError):
            self.engine.delete_profile(spritze["id"])
        with self.assertRaises(KeyError):
            self.engine.select_profile("nix")
        with self.assertRaises(ValueError):
            self.engine.save_profile_as("   ")


# ------------------------------------------------------------ Gerätesuche


class GeraeteTest(unittest.TestCase):
    """Die Suche sammelt, was die drei Wege finden - und ein Weg, der scheitert,
    verdirbt den anderen nichts."""

    def test_results_and_problems_are_collected(self):
        from agripilot import geraete

        def empfaenger():
            return ([geraete.Fund("gnss", "COM5", "u-blox GNSS receiver", "sieht nach dem F9P aus")],
                    {"gnss.source": "serial", "gnss.port": "COM5"}, [])

        def sensor():
            return [], {}, ["Kein Brick Daemon"]

        def kaputt():
            raise OSError("Treiber fehlt")

        ergebnis = geraete.suchen(sucher=[empfaenger, sensor, kaputt])
        self.assertEqual([f.kennung for f in ergebnis.funde], ["COM5"])
        self.assertEqual(ergebnis.vorschlag["gnss.port"], "COM5")
        self.assertEqual(len(ergebnis.probleme), 2)
        self.assertIn("Treiber fehlt", ergebnis.probleme[1])
        daten = ergebnis.to_dict()
        self.assertEqual(daten["funde"][0]["hinweis"], "sieht nach dem F9P aus")

    def test_the_real_serial_scan_runs_without_hardware(self):
        from agripilot import geraete
        funde, vorschlag, probleme = geraete.serielle_anschluesse()
        self.assertIsInstance(funde, list)
        self.assertIsInstance(vorschlag, dict)


class MenueApiTest(unittest.TestCase):
    """Die neuen Menüpunkte von außen: Import, Saisonspur, Maschinen, Gerätesuche."""

    def test_import_lines_profiles_and_device_scan_over_the_interface(self):
        import base64
        from unittest import mock
        from fastapi.testclient import TestClient
        from agripilot import config as config_module, geraete
        from agripilot.server import create_app

        with tempfile.TemporaryDirectory() as ordner:
            config = config_module.load("/kein-solcher-pfad.yaml")
            config.server.data_dir = ordner
            config.gnss.source = "simulator"
            with TestClient(create_app(config)) as client:
                ring = list(reversed([_tm_vorwaerts(lat, lon) for lat, lon in ShapefileTest.ECKEN_WGS]))
                antwort = client.post("/api/fields/import", json={
                    "name": "antrag.shp",
                    "shp": base64.b64encode(_shp_bauen([[ring]])).decode(),
                    "dbf": base64.b64encode(_dbf_bauen([("NAME", 20)], [("Wiese",)])).decode(),
                    "prj": UTM32_PRJ,
                })
                self.assertEqual(antwort.status_code, 200, antwort.text)
                felder = antwort.json()["data"]["felder"]
                self.assertEqual(felder[0]["name"], "Wiese")
                self.assertEqual(client.get("/api/fields").json()[0]["id"], felder[0]["id"])

                # Kaputte Datei: 400 mit Grund, kein 500.
                kaputt = client.post("/api/fields/import", json={"shp": base64.b64encode(b"x" * 300).decode()})
                self.assertEqual(kaputt.status_code, 400)
                self.assertEqual(client.post("/api/fields/import", json={"shp": ""}).status_code, 400)

                # Spur anlegen, als Saisonspur setzen, wiederfinden.
                app = client.app.state.app
                app.engine.load_field(felder[0]["id"])
                spur = app.store.save_line({"field_id": felder[0]["id"], "name": "AB", "mode": "ab",
                                            "points": [[0, 0], [0, 50]], "spacing_m": 3.0})
                antwort = client.post(f"/api/lines/{spur['id']}", json={"fahrgasse_m": 21})
                self.assertEqual(antwort.status_code, 200)
                self.assertEqual(antwort.json()["data"]["saison"], time.localtime().tm_year)
                self.assertEqual(client.get("/api/lines?field_id=" + felder[0]["id"]).json()[0]["fahrgasse_m"], 21.0)
                self.assertEqual(client.post("/api/lines/nix", json={"name": "x"}).status_code, 400)

                # Maschinen
                self.assertEqual(len(client.get("/api/profiles").json()), 1)
                neu = client.post("/api/profiles", json={"name": "Spritze", "werte": {"width_m": 24}})
                self.assertEqual(neu.status_code, 200)
                self.assertEqual(neu.json()["data"]["width_m"], 24.0)
                liste = client.get("/api/profiles").json()
                self.assertEqual([p["aktiv"] for p in liste], [False, True])
                self.assertEqual(client.post(f"/api/profiles/{liste[0]['id']}/select").json()["data"]["name"],
                                 "Traktor")
                self.assertEqual(client.delete(f"/api/profiles/{liste[1]['id']}").status_code, 200)
                self.assertEqual(client.delete(f"/api/profiles/{liste[0]['id']}").status_code, 400)
                self.assertEqual(client.post("/api/profiles", json={"name": ""}).status_code, 400)

                # Gerätesuche - ohne Hardware nachgestellt.
                ergebnis = geraete.Suchergebnis(
                    funde=[geraete.Fund("gnss", "COM5", "u-blox", "sieht nach dem F9P aus")],
                    vorschlag={"gnss.source": "serial", "gnss.port": "COM5"})
                with mock.patch.object(geraete, "suchen", return_value=ergebnis):
                    antwort = client.get("/api/geraete/suchen")
                self.assertEqual(antwort.status_code, 200)
                self.assertEqual(antwort.json()["funde"][0]["kennung"], "COM5")
                self.assertEqual(antwort.json()["vorschlag"]["gnss.port"], "COM5")


if __name__ == "__main__":
    unittest.main(verbosity=2)
