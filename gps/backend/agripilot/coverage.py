"""Coverage map: which ground has already been worked.

The map is a grid of small square cells.  A grid rather than a pile of polygons
is what makes this cheap enough for a Raspberry Pi: marking ground worked is a
set insert, the worked area is a count times the cell area, and asking "has this
section already been over here?" - the question section control lives on - is a
single lookup instead of a polygon intersection test.

Cell size trades memory for precision.  At 0.5 m a 100 ha field needs about two
million cells, which is a few tens of megabytes as a Python set: fine for a day's
work, and the map is thinned to disk between jobs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from .geo import Point, point_in_polygon


@dataclass
class Section:
    """One switchable part of the implement, measured from the tool centre."""

    index: int
    left_m: float   # + = right of centre, so left_m < right_m always
    right_m: float
    enabled: bool = True
    auto: bool = True      # may be switched off automatically on worked ground
    forced_off: bool = False

    @property
    def width(self) -> float:
        return self.right_m - self.left_m

    @property
    def centre(self) -> float:
        return (self.left_m + self.right_m) / 2.0


def build_sections(width_m: float, count: int) -> list[Section]:
    """Split a working width into equal sections, numbered left to right."""
    count = max(1, int(count))
    step = width_m / count
    start = -width_m / 2.0
    return [
        Section(index=i, left_m=start + i * step, right_m=start + (i + 1) * step)
        for i in range(count)
    ]


class CoverageMap:
    """Worked ground as a set of grid cells."""

    def __init__(self, cell_size: float = 0.5) -> None:
        self.cell_size = cell_size
        self.cells: set[tuple[int, int]] = set()
        self.applied_area_m2 = 0.0  # includes overlap: what the machine actually put out
        self._new_cells: list[tuple[int, int]] = []

    # -- queries ----------------------------------------------------------

    @property
    def area_m2(self) -> float:
        return len(self.cells) * self.cell_size * self.cell_size

    @property
    def area_ha(self) -> float:
        return self.area_m2 / 10_000.0

    @property
    def overlap_m2(self) -> float:
        """Ground gone over more than once - the number that costs money."""
        return max(0.0, self.applied_area_m2 - self.area_m2)

    @property
    def overlap_percent(self) -> float:
        if self.applied_area_m2 <= 0:
            return 0.0
        return 100.0 * self.overlap_m2 / self.applied_area_m2

    def is_covered(self, point: Point) -> bool:
        return self._cell(point) in self.cells

    def _cell(self, point: Point) -> tuple[int, int]:
        return (
            int(math.floor(point[0] / self.cell_size)),
            int(math.floor(point[1] / self.cell_size)),
        )

    # -- recording --------------------------------------------------------

    def add_swath(self, previous: Point, current: Point, heading: float,
                  sections: Sequence[Section]) -> float:
        """Mark the ground swept between two positions.

        Returns the applied area of this step in m².  Steps longer than a few
        metres (a GPS dropout, or simply driving fast between updates) are split
        so the swath stays a chain of small quads instead of one long one that
        cuts corners.
        """
        travel = math.dist(previous, current)
        if travel <= 0.001:
            return 0.0
        max_step = max(1.0, self.cell_size * 4)
        steps = max(1, int(math.ceil(travel / max_step)))
        applied = 0.0
        for s in range(steps):
            t0, t1 = s / steps, (s + 1) / steps
            p0 = (previous[0] + (current[0] - previous[0]) * t0,
                  previous[1] + (current[1] - previous[1]) * t0)
            p1 = (previous[0] + (current[0] - previous[0]) * t1,
                  previous[1] + (current[1] - previous[1]) * t1)
            applied += self._add_quad(p0, p1, heading, sections)
        self.applied_area_m2 += applied
        return applied

    def _add_quad(self, p0: Point, p1: Point, heading: float,
                  sections: Sequence[Section]) -> float:
        h = math.radians(heading)
        right = (math.cos(h), -math.sin(h))
        applied = 0.0
        for section in sections:
            if not section.enabled or section.forced_off:
                continue
            corners = [
                (p0[0] + right[0] * section.left_m, p0[1] + right[1] * section.left_m),
                (p0[0] + right[0] * section.right_m, p0[1] + right[1] * section.right_m),
                (p1[0] + right[0] * section.right_m, p1[1] + right[1] * section.right_m),
                (p1[0] + right[0] * section.left_m, p1[1] + right[1] * section.left_m),
            ]
            self._rasterise(corners)
            applied += math.dist(p0, p1) * section.width
        return applied

    def _rasterise(self, polygon: Sequence[Point]) -> None:
        """Mark every cell whose centre falls inside the quad."""
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        cs = self.cell_size
        ix0, ix1 = int(math.floor(min(xs) / cs)), int(math.ceil(max(xs) / cs))
        iy0, iy1 = int(math.floor(min(ys) / cs)), int(math.ceil(max(ys) / cs))
        for ix in range(ix0, ix1 + 1):
            cx = (ix + 0.5) * cs
            for iy in range(iy0, iy1 + 1):
                cell = (ix, iy)
                if cell in self.cells:
                    continue
                cy = (iy + 0.5) * cs
                if point_in_polygon((cx, cy), polygon):
                    self.cells.add(cell)
                    self._new_cells.append(cell)

    # -- section control --------------------------------------------------

    def update_auto_sections(self, tool_centre: Point, heading: float,
                             sections: Sequence[Section],
                             look_ahead_m: float = 1.0,
                             speed_ms: float = 0.0,
                             boundary: Optional[Sequence[Point]] = None) -> None:
        """Switch sections off over worked ground and outside the boundary.

        The check is done a little ahead of the machine, scaled with speed,
        because a sprayer valve needs time to close.  Only sections flagged
        `auto` are touched, so the driver can always override.
        """
        h = math.radians(heading)
        forward = (math.sin(h), math.cos(h))
        right = (math.cos(h), -math.sin(h))
        ahead = look_ahead_m + speed_ms * 0.5
        for section in sections:
            if not section.auto or section.forced_off:
                continue
            probe = (
                tool_centre[0] + forward[0] * ahead + right[0] * section.centre,
                tool_centre[1] + forward[1] * ahead + right[1] * section.centre,
            )
            outside = boundary is not None and not point_in_polygon(probe, boundary)
            section.enabled = not (self.is_covered(probe) or outside)

    # -- transfer ---------------------------------------------------------

    def drain_new_cells(self) -> list[tuple[int, int]]:
        """Cells marked since the last call - the display only needs the delta."""
        new, self._new_cells = self._new_cells, []
        return new

    def pack(self) -> bytes:
        """Compact binary form for storage and for syncing to the master."""
        import struct
        out = bytearray(struct.pack("<f", self.cell_size))
        for ix, iy in sorted(self.cells):
            out += struct.pack("<ii", ix, iy)
        return bytes(out)

    @classmethod
    def unpack(cls, blob: bytes) -> "CoverageMap":
        import struct
        if len(blob) < 4:
            return cls()
        (cell_size,) = struct.unpack_from("<f", blob, 0)
        cov = cls(cell_size=cell_size)
        for offset in range(4, len(blob) - 7, 8):
            cov.cells.add(struct.unpack_from("<ii", blob, offset))
        cov.applied_area_m2 = cov.area_m2
        return cov

    def merge(self, other: "CoverageMap") -> None:
        """Combine two machines' work on the same field.

        Cell sizes must match; grids are anchored to the same field datum, so
        identical ground lands on identical cells and the union is exact.
        """
        if abs(other.cell_size - self.cell_size) > 1e-6:
            raise ValueError("Rastergrößen unterschiedlich - Karten nicht kombinierbar")
        added = other.cells - self.cells
        self.cells |= added
        self.applied_area_m2 += other.applied_area_m2
        self._new_cells.extend(sorted(added))

    def cells_for_display(self, limit: int = 200_000) -> list[list[int]]:
        cells = sorted(self.cells)
        if len(cells) > limit:
            cells = cells[:limit]
        return [[ix, iy] for ix, iy in cells]
