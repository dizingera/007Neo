"""Geodesy helpers.

Everything the guidance code does happens in a local, flat, metric coordinate
system (east/north in metres) anchored to a datum point near the field.  Working
in metres instead of degrees keeps the maths readable and fast: a cross track
error is a subtraction, an area is a shoelace sum.

The projection is a tangent plane on the WGS84 ellipsoid.  Over a single field
(a few kilometres) the error stays below a millimetre, which is far under the
noise of even an RTK receiver, so nothing is lost by the simplification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple

# WGS84
_A = 6378137.0
_F = 1.0 / 298.257223563
_E2 = _F * (2.0 - _F)

Point = Tuple[float, float]  # (east, north) in metres


def _radii(lat_rad: float) -> Tuple[float, float]:
    """Meridian and prime-vertical radius of curvature at a latitude."""
    s = math.sin(lat_rad)
    w = math.sqrt(1.0 - _E2 * s * s)
    meridian = _A * (1.0 - _E2) / (w * w * w)
    prime_vertical = _A / w
    return meridian, prime_vertical


@dataclass(frozen=True)
class LocalPlane:
    """Converts between WGS84 degrees and local metres around a datum.

    The scale factors are evaluated at the *mid latitude* between the datum and
    the point, not at the datum itself.  That matters more than it sounds: the
    east scale carries a cos(latitude) term that changes by about 1.3 % per
    degree, so a datum set 20 km away would stretch every distance by a couple
    of metres per kilometre - and two machines that picked different datums for
    the same field would disagree about where the passes are.  With the mid
    latitude the projection is datum-independent to millimetres over any
    distance a field can span.
    """

    lat0: float
    lon0: float

    def _scales(self, lat: float) -> Tuple[float, float]:
        """Metres per degree of latitude and longitude, mid-way to `lat`."""
        mid = math.radians((self.lat0 + lat) / 2.0)
        meridian, prime_vertical = _radii(mid)
        return (math.radians(1.0) * meridian,
                math.radians(1.0) * prime_vertical * math.cos(mid))

    def to_local(self, lat: float, lon: float) -> Point:
        m_per_deg_lat, m_per_deg_lon = self._scales(lat)
        return (lon - self.lon0) * m_per_deg_lon, (lat - self.lat0) * m_per_deg_lat

    def to_wgs(self, east: float, north: float) -> Tuple[float, float]:
        # The latitude scale depends on the latitude we are solving for, so take
        # one corrective step: the datum estimate is already good to metres and
        # the second pass lands well under a millimetre.
        lat = self.lat0 + north / self._scales(self.lat0)[0]
        for _ in range(2):
            lat = self.lat0 + north / self._scales(lat)[0]
        lon = self.lon0 + east / self._scales(lat)[1]
        return lat, lon


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. Used for coarse checks, not guidance."""
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def heading_deg(a: Point, b: Point) -> float:
    """Compass heading from a to b: 0 = north, 90 = east."""
    return normalize_heading(math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])))


def normalize_heading(deg: float) -> float:
    """Fold any angle into [0, 360)."""
    return deg % 360.0


def angle_difference(target: float, current: float) -> float:
    """Shortest signed turn from current to target, in (-180, 180]."""
    return (target - current + 180.0) % 360.0 - 180.0


def polygon_area(points: Sequence[Point]) -> float:
    """Shoelace area in m². Sign is dropped: orientation is irrelevant here."""
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def polygon_perimeter(points: Sequence[Point]) -> float:
    n = len(points)
    if n < 2:
        return 0.0
    return sum(distance(points[i], points[(i + 1) % n]) for i in range(n))


def point_in_polygon(p: Point, polygon: Sequence[Point]) -> bool:
    """Ray casting. Used to tell whether the tractor is inside a field boundary."""
    x, y = p
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x_cross > x:
                inside = not inside
    return inside


def project_on_segment(p: Point, a: Point, b: Point) -> Tuple[Point, float, float]:
    """Closest point on segment a-b.

    Returns the foot point, the parameter t in [0, 1] along the segment, and the
    signed lateral distance (positive when p lies to the right of a->b).
    """
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return a, 0.0, distance(p, a)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / length_sq
    t_clamped = min(1.0, max(0.0, t))
    foot = (ax + t_clamped * dx, ay + t_clamped * dy)
    # Cross product z of (b-a) x (p-a); negative means p is to the right.
    cross = dx * (p[1] - ay) - dy * (p[0] - ax)
    return foot, t_clamped, -cross / math.sqrt(length_sq)


def simplify(points: Sequence[Point], tolerance: float = 0.25) -> list[Point]:
    """Ramer-Douglas-Peucker.

    Recorded boundaries and curved tracks arrive at 5-10 Hz and are mostly
    redundant.  Thinning them keeps the database small and the drawing fast
    without moving the line by more than `tolerance` metres.
    """
    if len(points) < 3:
        return list(points)
    stack = [(0, len(points) - 1)]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    while stack:
        start, end = stack.pop()
        max_dist = 0.0
        index = start
        for i in range(start + 1, end):
            _, _, lateral = project_on_segment(points[i], points[start], points[end])
            d = abs(lateral)
            if d > max_dist:
                max_dist, index = d, i
        if max_dist > tolerance:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [p for p, k in zip(points, keep) if k]


def bounds(points: Iterable[Point]) -> Tuple[float, float, float, float]:
    """(min_east, min_north, max_east, max_north)."""
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)
