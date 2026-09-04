"""Tests für AgriPilot.

Bewusst mit `unittest` aus der Standardbibliothek: so laufen sie auf einem
frisch aufgesetzten Raspberry Pi ohne zusätzliche Installation.

    cd gps/backend && python3 -m unittest discover -s tests -v

Geprüft wird vor allem das, was auf dem Feld Geld kostet, wenn es falsch ist:
Flächen, Vorzeichen der Abweichung, und die Bedingungen, unter denen die
Lenkautomatik einschalten darf.
"""

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

    def test_output_choice_follows_the_configuration(self):
        from agripilot import config as config_module
        from agripilot.actuators import build_output
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.steering.output = "phidget"
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
