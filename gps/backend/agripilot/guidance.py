"""Guidance: turn a position into "how far off the pass am I, and where do I steer".

Two pattern types cover almost all field work:

* AB line - two points define a direction, and the field is covered by parallel
  passes spaced one working width apart.
* Curve (contour) - a recorded track is repeated at one width spacing, for
  headlands and irregular fields.

Both reduce to the same question: signed lateral distance to the nearest pass.
Everything the driver sees (lightbar, centimetres off, pass number) and
everything the autosteer needs (steer angle) comes out of that one number plus
the heading error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional, Sequence

from .geo import (
    Point,
    angle_difference,
    distance,
    heading_deg,
    normalize_heading,
    project_on_segment,
    simplify,
)

Mode = Literal["ab", "curve"]


@dataclass
class VehicleProfile:
    """Machine geometry.

    The antenna is never where the work happens.  Guidance is computed for the
    *tool* centre, otherwise every pass is offset by the antenna position and
    the overlap looks fine on screen while the field shows stripes.
    """

    name: str = "Traktor"
    width_m: float = 3.0            # working width of the implement
    overlap_m: float = 0.0          # deliberate overlap; reduces effective spacing
    wheelbase_m: float = 2.6
    antenna_forward_m: float = 1.2  # antenna ahead of the rear axle (+ = forward)
    antenna_right_m: float = 0.0    # antenna right of the centre line
    antenna_height_m: float = 3.0
    tool_offset_m: float = 0.0      # implement pulled off-centre (+ = right)
    tool_trailing_m: float = 0.0    # distance from rear axle back to the tool
    max_steer_deg: float = 35.0
    steer_gain: float = 0.9         # Stanley k: higher = harder pull back to line
    steer_softening: float = 1.2    # m/s added to the denominator; tames low speed
    max_steer_rate_deg_s: float = 25.0  # how fast the steering actuator may move
    sections: int = 1               # number of switchable sections across the width

    @property
    def spacing_m(self) -> float:
        """Distance between passes: working width minus intentional overlap."""
        return max(0.1, self.width_m - self.overlap_m)

    def tool_position(self, antenna: Point, heading: float) -> Point:
        """Project the antenna position onto the tool centre.

        Rotate the (right, forward) offsets into the local frame along the
        vehicle heading, then subtract them from the antenna position.
        """
        h = math.radians(heading)
        forward = (math.sin(h), math.cos(h))
        right = (math.cos(h), -math.sin(h))
        # From antenna back to the rear axle, then back to the tool.
        back = self.antenna_forward_m + self.tool_trailing_m
        side = self.tool_offset_m - self.antenna_right_m
        return (
            antenna[0] - forward[0] * back + right[0] * side,
            antenna[1] - forward[1] * back + right[1] * side,
        )


@dataclass
class GuidanceState:
    """Everything the cab display and the steering controller need."""

    active: bool = False
    mode: Mode = "ab"
    cross_track_m: float = 0.0     # + = vehicle right of the target pass
    pass_number: int = 0
    heading_error_deg: float = 0.0
    target_heading_deg: float = 0.0
    steer_angle_deg: float = 0.0   # + = steer right
    reversed_direction: bool = False
    distance_along_m: float = 0.0
    lightbar: int = 0              # LED offset, + = drift to the right
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "mode": self.mode,
            "cross_track_m": self.cross_track_m,
            "cross_track_cm": self.cross_track_m * 100.0,
            "pass_number": self.pass_number,
            "heading_error_deg": self.heading_error_deg,
            "target_heading_deg": self.target_heading_deg,
            "steer_angle_deg": self.steer_angle_deg,
            "reversed": self.reversed_direction,
            "distance_along_m": self.distance_along_m,
            "lightbar": self.lightbar,
            "message": self.message,
        }


class GuidanceLine:
    """A reference pattern plus the passes derived from it."""

    def __init__(self, mode: Mode, points: Sequence[Point], spacing: float,
                 name: str = "", line_id: str = "") -> None:
        if len(points) < 2:
            raise ValueError("Eine Führungslinie braucht mindestens zwei Punkte")
        self.mode: Mode = mode
        self.name = name
        self.id = line_id
        self.spacing = max(0.1, spacing)
        self.nudge_m = 0.0  # manual sideways trim of the whole pattern
        if mode == "ab":
            self.points = [tuple(points[0]), tuple(points[-1])]
        else:
            self.points = [tuple(p) for p in simplify(points, 0.15)]

    # -- geometry ---------------------------------------------------------

    def _ab_lateral(self, p: Point) -> tuple[float, float, float]:
        """Signed distance from the infinite AB line, its heading, and progress."""
        a, b = self.points[0], self.points[-1]
        head = heading_deg(a, b)
        h = math.radians(head)
        forward = (math.sin(h), math.cos(h))
        right = (math.cos(h), -math.sin(h))
        dx, dy = p[0] - a[0], p[1] - a[1]
        lateral = dx * right[0] + dy * right[1]
        along = dx * forward[0] + dy * forward[1]
        return lateral, head, along

    def _curve_lateral(self, p: Point) -> tuple[float, float, float]:
        """Nearest point on the recorded track: distance, segment heading, progress."""
        best = (float("inf"), 0.0, 0.0, 0.0)
        travelled = 0.0
        for i in range(len(self.points) - 1):
            a, b = self.points[i], self.points[i + 1]
            seg_len = distance(a, b)
            foot, t, lateral = project_on_segment(p, a, b)
            d = distance(p, foot)
            if d < best[0]:
                best = (d, lateral, heading_deg(a, b), travelled + t * seg_len)
            travelled += seg_len
        _, lateral, head, along = best
        return lateral, head, along

    def solve(self, position: Point, vehicle_heading: float,
              speed_ms: float, profile: VehicleProfile) -> GuidanceState:
        """Compute the guidance state for a tool position and heading."""
        if self.mode == "ab":
            lateral, line_heading, along = self._ab_lateral(position)
        else:
            lateral, line_heading, along = self._curve_lateral(position)

        lateral -= self.nudge_m

        # Which pass are we on, and how far off its centre?
        pass_number = round(lateral / self.spacing)
        cross_track = lateral - pass_number * self.spacing

        # Passes are driven in both directions.  Compare the heading against the
        # line and its reverse and keep whichever the driver is actually doing,
        # otherwise every second pass reports a 180 degree error.
        target = line_heading
        reversed_dir = abs(angle_difference(line_heading, vehicle_heading)) > 90.0
        if reversed_dir:
            target = normalize_heading(line_heading + 180.0)
        heading_error = angle_difference(target, vehicle_heading)

        # On a reversed pass "right of the line" flips too.
        signed_xte = -cross_track if reversed_dir else cross_track

        steer = self._steer_angle(signed_xte, heading_error, speed_ms, profile)

        return GuidanceState(
            active=True,
            mode=self.mode,
            cross_track_m=signed_xte,
            pass_number=pass_number,
            heading_error_deg=heading_error,
            target_heading_deg=target,
            steer_angle_deg=steer,
            reversed_direction=reversed_dir,
            distance_along_m=along,
            lightbar=lightbar_offset(signed_xte),
        )

    def _steer_angle(self, cross_track: float, heading_error: float,
                     speed_ms: float, profile: VehicleProfile) -> float:
        """Stanley controller.

        Two terms: line up with the pass (heading error) and close the remaining
        gap (cross track).  The gap term is divided by speed so the correction is
        gentle when fast and firm when crawling; the softening constant stops it
        from exploding as speed approaches zero.
        """
        approach = math.degrees(
            math.atan2(profile.steer_gain * cross_track,
                       abs(speed_ms) + profile.steer_softening)
        )
        steer = heading_error - approach
        limit = profile.max_steer_deg
        return max(-limit, min(limit, steer))

    # -- drawing helpers --------------------------------------------------

    def pass_geometry(self, centre_pass: int, count: int = 4,
                      length: float = 400.0) -> list[dict]:
        """Geometry of the neighbouring passes so the cab display can draw them."""
        result = []
        for offset in range(centre_pass - count, centre_pass + count + 1):
            shift = offset * self.spacing + self.nudge_m
            result.append({
                "pass": offset,
                "points": [list(p) for p in self._shift(shift, length)],
            })
        return result

    def _shift(self, shift: float, length: float) -> list[Point]:
        if self.mode == "ab":
            a, b = self.points[0], self.points[-1]
            head = math.radians(heading_deg(a, b))
            forward = (math.sin(head), math.cos(head))
            right = (math.cos(head), -math.sin(head))
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            half = max(length, distance(a, b)) / 2
            base = [
                (mid[0] - forward[0] * half, mid[1] - forward[1] * half),
                (mid[0] + forward[0] * half, mid[1] + forward[1] * half),
            ]
            return [(x + right[0] * shift, y + right[1] * shift) for x, y in base]
        # Curve: offset each vertex along the local normal.  Good enough for
        # drawing; the guidance maths above never relies on it.
        out: list[Point] = []
        pts = self.points
        for i, p in enumerate(pts):
            a = pts[max(0, i - 1)]
            b = pts[min(len(pts) - 1, i + 1)]
            head = math.radians(heading_deg(a, b))
            right = (math.cos(head), -math.sin(head))
            out.append((p[0] + right[0] * shift, p[1] + right[1] * shift))
        return out

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "points": [list(p) for p in self.points],
            "spacing_m": self.spacing,
            "nudge_m": self.nudge_m,
        }


def lightbar_offset(cross_track_m: float, led_cm: float = 5.0,
                    leds: int = 10) -> int:
    """Map centimetres of error onto lightbar LEDs.

    5 cm per LED is a useful compromise: with RTK it shows real drift, with a
    plain GPS receiver it does not flicker across the whole bar.
    """
    steps = int(round(cross_track_m * 100.0 / led_cm))
    return max(-leds, min(leds, steps))


@dataclass
class HeadingFilter:
    """Produces a usable heading from noisy inputs.

    Course over ground is derived from movement, so it is garbage below walking
    pace - exactly when the driver is lining up at the headland.  This keeps the
    last good heading while slow, prefers a dual-antenna heading when present,
    and smooths the rest.
    """

    min_speed_ms: float = 0.5
    smoothing: float = 0.35
    value: Optional[float] = None
    _history: list[float] = field(default_factory=list)

    def update(self, course: Optional[float], true_heading: Optional[float],
               speed_ms: float) -> Optional[float]:
        if true_heading is not None:
            self.value = true_heading
            return self.value
        if course is None or speed_ms < self.min_speed_ms:
            return self.value
        if self.value is None:
            self.value = course
            return self.value
        # Smooth on the shortest path so 359 -> 001 does not swing the long way.
        delta = angle_difference(course, self.value)
        self.value = normalize_heading(self.value + delta * self.smoothing)
        return self.value
