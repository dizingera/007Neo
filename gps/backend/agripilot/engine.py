"""The live system: one fix in, one complete cab picture out.

Everything else in this package is a pure piece - parse, project, compute,
store.  This module is the one place where they meet and where the state of the
current work lives: which field, which line, what has been covered, whether a
job is running, whether steering may engage.

The flow for every position update is always the same, and the order matters:

    fix -> local metres -> tool position -> heading -> guidance
        -> coverage & section control -> autosteer -> broadcast

Guidance is computed for the tool, not the antenna, and coverage is recorded
where the tool actually is, so what the screen shows is what the field gets.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict
from typing import Any, Optional

from . import geo, storage
from .actuators import SteerContext
from .config import Config
from .coverage import CoverageMap, Section, build_sections
from .guidance import GuidanceLine, GuidanceState, HeadingFilter, VehicleProfile
from .imu import terrain_offset
from .nmea import Fix

TRACK_MIN_DISTANCE_M = 1.0
TRACK_FLUSH_COUNT = 25


class Engine:
    def __init__(self, config: Config, store: storage.Storage) -> None:
        self.config = config
        self.store = store

        self.profile = self._load_profile()
        self.sections: list[Section] = build_sections(
            self.profile.width_m, self.profile.sections
        )
        self.heading_filter = HeadingFilter()

        self.field: Optional[dict] = None
        self.plane: Optional[geo.LocalPlane] = None
        self.line: Optional[GuidanceLine] = None
        self.coverage = CoverageMap(cell_size=0.5)
        self.job: Optional[dict] = None

        self.fix: Optional[Fix] = None
        self.position: Optional[geo.Point] = None      # antenna, local metres
        self.tool_position: Optional[geo.Point] = None
        self.heading: Optional[float] = None
        self.guidance = GuidanceState()

        self.record_mode: Optional[str] = None          # "boundary" | "curve"
        self._recording: list[geo.Point] = []
        self._pending_a: Optional[geo.Point] = None

        self.distance_m = 0.0
        self.working_time_s = 0.0
        self.session_started_at = time.time()
        self.messages: list[str] = []
        self.auto_sections = True

        self._track_buffer: list[tuple] = []
        self._last_track_point: Optional[geo.Point] = None
        self._last_update_at: Optional[float] = None
        self.steering = None    # set by the server once the controller exists
        self.simulator = None   # set when running on the simulator source
        self.imu = None         # set when a tilt sensor is configured
        self.terrain_offset_m = (0.0, 0.0)   # (rechts, vorn) - nur zur Anzeige

    # -- profile ----------------------------------------------------------

    def _load_profile(self) -> VehicleProfile:
        stored = self.store.get_setting("vehicle_profile")
        if not stored:
            return VehicleProfile()
        fields = VehicleProfile.__dataclass_fields__
        return VehicleProfile(**{k: v for k, v in stored.items() if k in fields})

    def update_profile(self, values: dict) -> VehicleProfile:
        fields = VehicleProfile.__dataclass_fields__
        current = asdict(self.profile)
        current.update({k: v for k, v in values.items() if k in fields})
        self.profile = VehicleProfile(**current)
        self.store.set_setting("vehicle_profile", current)
        self.sections = build_sections(self.profile.width_m, self.profile.sections)
        if self.line is not None:
            # Changing the working width changes the pass spacing, and with it
            # which pass the machine is on - but not the reference line itself.
            self.line.spacing = self.profile.spacing_m
        return self.profile

    # -- field and line ---------------------------------------------------

    def load_field(self, field_id: str) -> dict:
        field = self.store.get_field(field_id)
        if field is None:
            raise KeyError("Feld nicht gefunden")
        self.field = field
        self.plane = geo.LocalPlane(field["datum_lat"], field["datum_lon"])
        self.coverage = CoverageMap(cell_size=0.5)
        self.line = None
        lines = self.store.list_lines(field_id)
        if lines:
            self.load_line(lines[0]["id"])
        self.note(f"Feld geladen: {field['name']}")
        return field

    def create_field(self, name: str) -> dict:
        """Anchor a new field at the current position.

        The datum is what makes coverage grids from different machines line up,
        so it is fixed once at creation and never moved afterwards.
        """
        if self.fix is None or not self.fix.valid:
            raise RuntimeError("Ohne GPS-Fix lässt sich kein Feld anlegen")
        field = self.store.save_field({
            "name": name,
            "datum_lat": self.fix.lat,
            "datum_lon": self.fix.lon,
            "boundary": [],
            "area_ha": 0.0,
        })
        return self.load_field(field["id"])

    def load_line(self, line_id: str) -> dict:
        record = self.store.get_line(line_id)
        if record is None:
            raise KeyError("Spurlinie nicht gefunden")
        self.line = GuidanceLine(
            record["mode"], [tuple(p) for p in record["points"]],
            self.profile.spacing_m, record["name"], record["id"],
        )
        self.line.nudge_m = record.get("nudge_m", 0.0)
        self.note(f"Spur aktiv: {record['name']}")
        return record

    def clear_line(self) -> None:
        self.line = None
        self.guidance = GuidanceState()

    def set_a(self) -> dict:
        self._require_position()
        self._pending_a = self.tool_position
        self.note("Punkt A gesetzt - jetzt bis zum Ende fahren und B setzen")
        return {"a": list(self._pending_a)}

    def set_b(self, name: str = "") -> dict:
        self._require_position()
        if self._pending_a is None:
            raise RuntimeError("Erst Punkt A setzen")
        if geo.distance(self._pending_a, self.tool_position) < 3.0:
            raise RuntimeError("A und B liegen zu dicht beieinander (mind. 3 m)")
        line = self._save_line(
            "ab", [self._pending_a, self.tool_position],
            name or f"AB {time.strftime('%H:%M')}",
        )
        self._pending_a = None
        return line

    def set_ab_from_heading(self, heading: float, name: str = "") -> dict:
        """A+ line: one point plus a compass bearing.

        Useful when the neighbouring field or a previous year's pass already
        defines the direction and there is no room to drive out a B point.
        """
        self._require_position()
        a = self._pending_a or self.tool_position
        h = math.radians(heading)
        b = (a[0] + math.sin(h) * 200.0, a[1] + math.cos(h) * 200.0)
        self._pending_a = None
        return self._save_line("ab", [a, b], name or f"A+ {heading:.0f}°")

    def start_recording(self, mode: str) -> None:
        if mode not in ("boundary", "curve"):
            raise ValueError("Aufzeichnung: 'boundary' oder 'curve'")
        self._require_position()
        self.record_mode = mode
        self._recording = [self.tool_position]
        self.note("Grenze wird aufgezeichnet" if mode == "boundary"
                  else "Kurve wird aufgezeichnet")

    def stop_recording(self, name: str = "") -> dict:
        if self.record_mode is None:
            raise RuntimeError("Es läuft keine Aufzeichnung")
        mode, points = self.record_mode, self._recording
        self.record_mode, self._recording = None, []
        if len(points) < 3:
            raise RuntimeError("Zu wenige Punkte aufgezeichnet")
        if mode == "curve":
            return self._save_line("curve", points, name or f"Kurve {time.strftime('%H:%M')}")
        return self.save_boundary(points)

    def save_boundary(self, points: list[geo.Point]) -> dict:
        """Close the recorded loop and store it as the field boundary."""
        if self.field is None:
            raise RuntimeError("Kein Feld ausgewählt")
        simplified = geo.simplify(points, 0.3)
        area_ha = geo.polygon_area(simplified) / 10_000.0
        field = self.store.save_field({
            **{k: self.field[k] for k in
               ("id", "name", "datum_lat", "datum_lon", "note")},
            "boundary": [list(p) for p in simplified],
            "area_ha": area_ha,
        })
        self.field = field
        self.note(f"Feldgrenze gespeichert: {area_ha:.2f} ha")
        return field

    def _save_line(self, mode: str, points: list[geo.Point], name: str) -> dict:
        if self.field is None:
            raise RuntimeError("Kein Feld ausgewählt")
        record = self.store.save_line({
            "field_id": self.field["id"],
            "name": name,
            "mode": mode,
            "points": [list(p) for p in points],
            "spacing_m": self.profile.spacing_m,
        })
        self.load_line(record["id"])
        return record

    def nudge(self, metres: float) -> float:
        """Trim the whole pattern sideways.

        Real fields drift: a slightly different antenna position between
        machines, or a headland that was not quite square.  Nudging moves every
        pass rather than the one you are on, so the pattern stays consistent.
        """
        if self.line is None:
            raise RuntimeError("Keine Spur aktiv")
        self.line.nudge_m += metres
        self.store.save_line({
            "id": self.line.id,
            "field_id": self.field["id"],
            "name": self.line.name,
            "mode": self.line.mode,
            "points": [list(p) for p in self.line.points],
            "spacing_m": self.line.spacing,
            "nudge_m": self.line.nudge_m,
        })
        return self.line.nudge_m

    # -- jobs -------------------------------------------------------------

    def start_job(self, operation: str = "") -> dict:
        if self.field is None:
            raise RuntimeError("Kein Feld ausgewählt")
        if self.job is not None:
            return self.job
        self.job = self.store.start_job(
            self.field["id"], self.config.network.device_id,
            self.profile.name, operation,
            self.line.id if self.line else None,
        )
        self.distance_m = 0.0
        self.working_time_s = 0.0
        self.note(f"Arbeit gestartet{': ' + operation if operation else ''}")
        return self.job

    def stop_job(self) -> Optional[dict]:
        if self.job is None:
            return None
        self._flush_track(force=True)
        self.store.update_job(
            self.job["id"],
            ended_at=time.time(),
            distance_m=self.distance_m,
            area_ha=self.coverage.area_ha,
            overlap_ha=self.coverage.overlap_m2 / 10_000.0,
            working_time_s=self.working_time_s,
            coverage=self.coverage.pack(),
        )
        job = self.store.get_job(self.job["id"])
        self.job = None
        self.note(f"Arbeit beendet: {job['area_ha']:.2f} ha")
        return job

    # -- the position pipeline -------------------------------------------

    def on_fix(self, fix: Fix) -> None:
        if not fix.valid:
            return
        now = fix.received_at or time.time()
        self.fix = fix

        if self.plane is None:
            # No field chosen yet: anchor a working plane at the first fix so
            # the display is useful immediately.
            self.plane = geo.LocalPlane(fix.lat, fix.lon)

        self.position = self.plane.to_local(fix.lat, fix.lon)
        dt = (now - self._last_update_at) if self._last_update_at else 0.0
        self._last_update_at = now

        attitude = self.imu.attitude if self.imu is not None else None
        yaw_rate = attitude.yaw_rate_deg_s if (attitude and attitude.fresh) else None
        use_yaw = yaw_rate if (self.imu is not None
                               and self.config.imu.use_for_heading) else None
        heading = self.heading_filter.update(
            fix.course_deg, fix.heading_deg, fix.speed_ms, use_yaw, dt
        )
        self.heading = heading
        if heading is None:
            return

        self.position = self._compensate_terrain(self.position, heading, attitude)

        previous_tool = self.tool_position
        self.tool_position = self.profile.tool_position(self.position, heading)

        # A position that moved further than the machine could have travelled is
        # a receiver artefact - a re-acquired fix after a gap under trees, or a
        # jump between RTK float and fix.  Believing it would add phantom metres
        # to the record and paint a swath of worked ground straight across the
        # field, so the step is dropped and the trail simply picks up again.
        plausible = True
        if previous_tool is not None and 0 < dt < 5.0:
            step = geo.distance(previous_tool, self.tool_position)
            plausible = step < fix.speed_ms * dt * 3.0 + 5.0
            if plausible:
                self.distance_m += step
                if fix.speed_ms > 0.3:
                    self.working_time_s += dt
        elif previous_tool is not None:
            plausible = False

        self._update_guidance(fix)
        self._update_coverage(previous_tool if plausible else None)
        self._update_steering(fix)
        self._record_track(fix, now)

        if self.record_mode is not None:
            last = self._recording[-1] if self._recording else None
            if last is None or geo.distance(last, self.tool_position) > 0.5:
                self._recording.append(self.tool_position)

    def _compensate_terrain(self, antenna: geo.Point, heading: float,
                            attitude) -> geo.Point:
        """Von der Antenne auf den Punkt am Boden rechnen.

        Bei drei Metern Antennenhöhe sind sechs Grad Seitenhang gut 30 cm - die
        Spur wandert genau um diesen Betrag, ohne dass der Empfänger irgendetwas
        Falsches misst. Deshalb wird die Neigung herausgerechnet, bevor
        Führung und Fläche daraus etwas machen.
        """
        if (attitude is None or not attitude.fresh
                or not self.config.imu.terrain_compensation):
            self.terrain_offset_m = (0.0, 0.0)
            return antenna
        right_off, forward_off = terrain_offset(
            attitude, self.profile.antenna_height_m, self.config.imu.roll_sign
        )
        self.terrain_offset_m = (right_off, forward_off)
        h = math.radians(heading)
        forward = (math.sin(h), math.cos(h))
        right = (math.cos(h), -math.sin(h))
        return (antenna[0] - right[0] * right_off - forward[0] * forward_off,
                antenna[1] - right[1] * right_off - forward[1] * forward_off)

    def _update_guidance(self, fix: Fix) -> None:
        if self.line is None or self.tool_position is None or self.heading is None:
            self.guidance = GuidanceState(message="Keine Spur aktiv")
            return
        self.guidance = self.line.solve(
            self.tool_position, self.heading, fix.speed_ms, self.profile
        )

    def _update_coverage(self, previous_tool: Optional[geo.Point]) -> None:
        """Mark the ground swept since the previous position.

        `previous_tool` is None when the last step was not believable; the
        anchor is then simply moved without painting, so a dropout leaves a gap
        in the map rather than a false stripe.
        """
        if self.job is None or self.tool_position is None or self.heading is None:
            return
        boundary = None
        if self.field and self.field.get("boundary"):
            boundary = [tuple(p) for p in self.field["boundary"]]
        if self.auto_sections:
            self.coverage.update_auto_sections(
                self.tool_position, self.heading, self.sections,
                speed_ms=self.fix.speed_ms if self.fix else 0.0,
                boundary=boundary,
            )
        if previous_tool is not None:
            self.coverage.add_swath(
                previous_tool, self.tool_position, self.heading, self.sections
            )

    def _update_steering(self, fix: Fix) -> None:
        if self.steering is None:
            return
        attitude = self.imu.attitude if self.imu is not None else None
        context = SteerContext(
            speed_ms=fix.speed_ms,
            wheelbase_m=self.profile.wheelbase_m,
            yaw_rate_deg_s=(attitude.yaw_rate_deg_s
                            if attitude and attitude.fresh else None),
            cross_track_m=self.guidance.cross_track_m,
        )
        command = self.steering.update(self.guidance, fix, context)
        # On the simulator, close the loop so autosteer can be demonstrated and
        # tuned without a machine.
        if self.simulator is not None:
            self.simulator.set_steer(command.angle_deg if command.engaged else 0.0)

    def _record_track(self, fix: Fix, now: float) -> None:
        if self.job is None or self.tool_position is None:
            return
        if (self._last_track_point is not None
                and geo.distance(self._last_track_point, self.tool_position)
                < TRACK_MIN_DISTANCE_M):
            return
        self._last_track_point = self.tool_position
        self._track_buffer.append((
            now, fix.lat, fix.lon, fix.altitude, fix.speed_ms,
            self.heading, fix.fix_quality, self.guidance.cross_track_m,
        ))
        self._flush_track()

    def _flush_track(self, force: bool = False) -> None:
        if not self._track_buffer or self.job is None:
            return
        if force or len(self._track_buffer) >= TRACK_FLUSH_COUNT:
            self.store.add_track_points(self.job["id"], self._track_buffer)
            self._track_buffer = []

    def tick(self, now: Optional[float] = None) -> None:
        """Regelmäßiger Herzschlag, unabhängig von eintreffenden Positionen.

        Die ganze Kette hängt sonst daran, dass Positionen kommen: bleibt der
        Empfänger stehen, wird auch nie neu entschieden, ob noch gelenkt werden
        darf. Die Hardware fängt das über ihren Failsafe ab, aber die Anzeige
        stünde weiter auf "lenkt". Deshalb wird hier von außen nachgesehen.
        """
        if self.steering is None or not self.steering.armed:
            return
        now = now or time.time()
        age = now - self.fix.received_at if (self.fix and self.fix.received_at) else 99.0
        if age > 2.0:
            self.steering.disarm("keine GPS-Daten mehr")
            self.note("Lenkung abgeschaltet: keine GPS-Daten")

    # -- output -----------------------------------------------------------

    def note(self, message: str) -> None:
        self.messages.append(f"{time.strftime('%H:%M:%S')}  {message}")
        del self.messages[:-30]

    def state(self) -> dict[str, Any]:
        fix = self.fix
        return {
            "time": time.time(),
            "device": {
                "id": self.config.network.device_id,
                "name": self.config.network.device_name,
                "role": self.config.network.role,
            },
            "fix": fix.to_dict() if fix else None,
            "position": list(self.position) if self.position else None,
            "tool_position": list(self.tool_position) if self.tool_position else None,
            "heading": self.heading,
            "guidance": self.guidance.to_dict(),
            "line": self.line.to_dict() if self.line else None,
            "field": {
                "id": self.field["id"],
                "name": self.field["name"],
                "area_ha": self.field["area_ha"],
                "boundary": self.field["boundary"],
                "datum": [self.field["datum_lat"], self.field["datum_lon"]],
            } if self.field else None,
            "profile": asdict(self.profile),
            "sections": [asdict(s) for s in self.sections],
            "auto_sections": self.auto_sections,
            "job": {
                **self.job,
                "area_ha": self.coverage.area_ha,
                "overlap_ha": self.coverage.overlap_m2 / 10_000.0,
                "overlap_percent": self.coverage.overlap_percent,
                "distance_m": self.distance_m,
                "working_time_s": self.working_time_s,
            } if self.job else None,
            "coverage": {
                "cell_size": self.coverage.cell_size,
                "area_ha": self.coverage.area_ha,
                "overlap_percent": self.coverage.overlap_percent,
                "cell_count": len(self.coverage.cells),
            },
            "recording": {
                "mode": self.record_mode,
                "points": len(self._recording),
                "area_ha": (geo.polygon_area(self._recording) / 10_000.0
                            if self.record_mode == "boundary"
                            and len(self._recording) > 2 else 0.0),
                "pending_a": list(self._pending_a) if self._pending_a else None,
            },
            "imu": ({
                **self.imu.attitude.to_dict(),
                "status": self.imu.status,
                "healthy": self.imu.healthy,
                "terrain_offset_cm": [self.terrain_offset_m[0] * 100.0,
                                      self.terrain_offset_m[1] * 100.0],
                "compensation": self.config.imu.terrain_compensation,
            } if self.imu is not None else None),
            "steering": self.steering.status() if self.steering else None,
            "messages": self.messages[-6:],
        }

    def _require_position(self) -> None:
        if self.tool_position is None:
            raise RuntimeError("Noch keine Position - warte auf GPS")
