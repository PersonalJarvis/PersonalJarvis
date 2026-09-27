"""Portable collision and support checks for the authored transport corridor.

These conservative checks consume packaged world coordinates. Renderer loading
and animation cannot grant support, clear a wall, or authorize an exit.
"""

from __future__ import annotations

import math
from typing import Any

Point = tuple[float, float]
_EPS = 1e-7
_GROUND_TOLERANCE = 0.18


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segment_distance(point: Point, start: Point, end: Point) -> float:
    dx, dz = end[0] - start[0], end[1] - start[1]
    length2 = dx * dx + dz * dz
    t = (
        0
        if length2 == 0
        else max(0, min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dz) / length2))
    )
    return math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dz)


def _inside(point: Point, polygon: list[Point]) -> bool:
    inside = False
    for start, end in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if _segment_distance(point, start, end) <= _EPS:
            return True
        if (start[1] > point[1]) != (end[1] > point[1]) and point[0] < (
            (end[0] - start[0]) * (point[1] - start[1]) / (end[1] - start[1]) + start[0]
        ):
            inside = not inside
    return inside


def _rectangle(center: Point, yaw: float, width: float, length: float) -> list[Point]:
    forward = (math.sin(yaw), math.cos(yaw))
    right = (forward[1], -forward[0])
    return [
        (
            center[0] + side * width / 2 * right[0] + along * length / 2 * forward[0],
            center[1] + side * width / 2 * right[1] + along * length / 2 * forward[1],
        )
        for side, along in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]


def _overlap(a: list[Point], b: list[Point]) -> bool:
    """Separating-axis check for convex footprints, including touching edges."""
    for polygon in (a, b):
        for p, q in zip(polygon, polygon[1:] + polygon[:1], strict=True):
            axis = (q[1] - p[1], p[0] - q[0])
            av = [x * axis[0] + z * axis[1] for x, z in a]
            bv = [x * axis[0] + z * axis[1] for x, z in b]
            if max(av) < min(bv) - _EPS or max(bv) < min(av) - _EPS:
                return False
    return True


def _contains_footprint(polygon: list[Point], footprint: list[Point]) -> bool:
    if not all(_inside(point, polygon) for point in footprint):
        return False
    # Clip each shoreline segment against the strict interior of the convex
    # footprint. Corner-to-corner notches have no proper edge intersections and
    # no strictly interior vertices, but still remove support under the body.
    for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        lower, upper = 0.0, 1.0
        for c, d in zip(footprint, footprint[1:] + footprint[:1], strict=True):
            initial, change = _cross(c, d, a), _cross(c, d, b) - _cross(c, d, a)
            if abs(change) <= _EPS:
                if initial <= _EPS:
                    upper = -1.0
                    break
            elif change > 0:
                lower = max(lower, (_EPS - initial) / change)
            else:
                upper = min(upper, (_EPS - initial) / change)
        if upper - lower > _EPS:
            return False
    return True


class MobilityGeometry:
    """Fail closed outside explicitly packaged support and solid geometry.

    A complete footprint must fit on one known patch. Overlapping patches may
    provide alternative support; unverified gaps between patches never do.
    """

    def __init__(self, definition: dict[str, Any]) -> None:
        collision = definition["navigation"].get("collision")
        if collision is None or collision.get("version") != 1:
            raise ValueError("transport requires validated collision data")
        signature = collision.get("source_sha256", "")
        if len(signature) != 64 or any(char not in "0123456789abcdef" for char in signature):
            raise ValueError("invalid collision source fingerprint")
        self.boxes: list[tuple[tuple[float, ...], tuple[float, ...]]] = []
        self.surfaces: list[tuple[list[Point], tuple[float, ...]]] = []

        def vector(value: Any, length: int) -> tuple[float, ...]:
            if (
                not isinstance(value, (list, tuple))
                or len(value) != length
                or any(
                    isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n)
                    for n in value
                )
            ):
                raise ValueError("invalid collision coordinates")
            return tuple(float(n) for n in value)

        boxes, surfaces = collision["solid_boxes"], collision["support_surfaces"]
        if not 1 <= len(boxes) <= 512 or not 1 <= len(surfaces) <= 128:
            raise ValueError("invalid bounded collision geometry")
        for rows in (boxes, surfaces):
            ids = [row["id"] for row in rows]
            if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
                raise ValueError("invalid collision identities")
        for row in boxes:
            lo, hi = vector(row["min"], 3), vector(row["max"], 3)
            if any(a >= b for a, b in zip(lo, hi, strict=True)):
                raise ValueError("invalid collision box")
            self.boxes.append((lo, hi))
        for row in surfaces:
            if not 3 <= len(row["polygon"]) <= 512:
                raise ValueError("invalid support polygon")
            polygon = [(p[0], p[1]) for p in (vector(v, 2) for v in row["polygon"])]
            area = sum(
                a[0] * b[1] - b[0] * a[1]
                for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True)
            )
            if abs(area) < _EPS or len(set(polygon)) != len(polygon):
                raise ValueError("degenerate support polygon")
            self.surfaces.append((polygon, vector(row["plane"], 3)))

    @staticmethod
    def _valid(position: tuple[float, ...] | list[float], *sizes: float) -> bool:
        return (
            len(position) == 3
            and all(math.isfinite(v) for v in position)
            and all(math.isfinite(size) and size > 0 for size in sizes)
        )

    def supported(self, position: tuple[float, ...] | list[float], radius: float = 0) -> bool:
        if not self._valid(position) or not math.isfinite(radius) or radius < 0:
            return False
        x, y, z = position
        for polygon, (a, b, c) in self.surfaces:
            if abs(a * x + b * z + c - y) + radius * math.hypot(a, b) > _GROUND_TOLERANCE:
                continue
            if _inside((x, z), polygon) and all(
                _segment_distance((x, z), p, q) + _EPS >= radius
                for p, q in zip(polygon, polygon[1:] + polygon[:1], strict=True)
            ):
                return True
        return False

    def capsule_clear(
        self, position: tuple[float, ...] | list[float], radius: float = 0.6, height: float = 1.8
    ) -> bool:
        if not self._valid(position, radius, height) or not self.supported(position, radius):
            return False
        x, y, z = position
        for lo, hi in self.boxes:
            if y + height <= lo[1] or y >= hi[1]:
                continue
            nearest = (max(lo[0], min(hi[0], x)), max(lo[2], min(hi[2], z)))
            if math.dist((x, z), nearest) <= radius + _EPS:
                return False
        return True

    def _footprint_clear(
        self, footprint: list[Point], plane: tuple[float, float, float], height: float
    ) -> bool:
        a, b, c = plane
        if not any(
            _contains_footprint(polygon, footprint)
            and all(
                abs((sa - a) * x + (sb - b) * z + sc - c) <= _GROUND_TOLERANCE for x, z in footprint
            )
            for polygon, (sa, sb, sc) in self.surfaces
        ):
            return False
        low = min(a * x + b * z + c for x, z in footprint)
        high = max(a * x + b * z + c for x, z in footprint) + height
        for lo, hi in self.boxes:
            if high <= lo[1] or low >= hi[1]:
                continue
            rectangle = [(lo[0], lo[2]), (hi[0], lo[2]), (hi[0], hi[2]), (lo[0], hi[2])]
            if _overlap(footprint, rectangle):
                return False
        return True

    def vehicle_clear(
        self,
        position: tuple[float, ...] | list[float],
        yaw: float,
        width: float = 3.4,
        length: float = 4.8,
        height: float = 2.6,
    ) -> bool:
        if not self._valid(position, width, length, height) or not math.isfinite(yaw):
            return False
        x, y, z = position
        return self._footprint_clear(_rectangle((x, z), yaw, width, length), (0, 0, y), height)

    def path_clear(
        self,
        start: tuple[float, ...] | list[float],
        end: tuple[float, ...] | list[float],
        width: float = 3.4,
        length: float = 4.8,
        height: float = 2.6,
    ) -> bool:
        if not self._valid(start, width, length, height) or not self._valid(end):
            return False
        dx, dz = end[0] - start[0], end[2] - start[2]
        distance2 = dx * dx + dz * dz
        if distance2 < _EPS:
            return False
        a, b = (end[1] - start[1]) * dx / distance2, (end[1] - start[1]) * dz / distance2
        plane = (a, b, start[1] - a * start[0] - b * start[2])
        center = ((start[0] + end[0]) / 2, (start[2] + end[2]) / 2)
        swept = _rectangle(center, math.atan2(dx, dz), width, length + math.sqrt(distance2))
        return self._footprint_clear(swept, plane, height)
