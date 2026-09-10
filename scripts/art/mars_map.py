"""Dependency-free terrain and circulation checks for the isolated Mars blockout.

Coordinates use X/right, Y/up and Z/depth, in metres. These helpers describe a
heightfield and planned routes; they do not validate runtime collision or art.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

MAX_GRADE = 0.1
HEIGHT_TOLERANCE = 0.05


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _smooth(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _geology(x: float, z: float) -> float:
    """A low basin, higher northern/western ridges, and a winding deep ravine."""
    basin = 5.0 - z * 0.025 - x * 0.01
    basin += 1.5 * math.sin(x / 72) * math.cos(z / 58)
    basin += 0.65 * math.sin((x + z) / 19)
    western_ridge = 32 * math.exp(-(((x + 295) / 60) ** 2))
    western_ridge *= 0.75 + 0.25 * math.sin(z / 80)
    northern_ridge = 32 * math.exp(-(((z + 225) / 44) ** 2))
    northern_ridge *= 0.6 + 0.4 * math.cos(x / 100) ** 2
    eastern_spur = 22 * math.exp(-(((x - 230) / 75) ** 2) - ((z + 125) / 95) ** 2)
    southern_rim = 12 * math.exp(-(((z - 260) / 48) ** 2))
    ravine_x = 60 + 12 * math.sin(z / 90)
    ravine_width = 12 + 2 * math.sin(z / 60)
    ravine = 38 * math.exp(-0.5 * ((x - ravine_x) / ravine_width) ** 2)
    return basin + western_ridge + northern_ridge + eastern_spur + southern_rim - ravine


def _segment_sample(
    a: dict[str, Any], b: dict[str, Any], x: float, z: float
) -> tuple[float, float]:
    """Return planar distance to a road and its pad-aware design elevation."""
    ax, ay, az = a["position"]
    bx, by, bz = b["position"]
    dx, dz = bx - ax, bz - az
    length = math.hypot(dx, dz)
    t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / (length * length)))
    perpendicular = math.hypot(x - ax - t * dx, z - az - t * dz)
    start, end = a.get("radius", 0), b.get("radius", 0)
    ramp_length = length - start - end
    # Coincident/overlapping pads of equal elevation need no ramp.
    progress = max(0.0, min(1.0, (t * length - start) / ramp_length)) if ramp_length > 0 else 0
    return perpendicular, ay + (by - ay) * progress


def height_at(layout: dict[str, Any], x: float, z: float) -> float:
    """Sample a validated layout's terrain, leaving bridge spans unfilled.

    Flat pads take precedence. Ground routes grade the full road width and fade
    into terrain over a soft shoulder. At overlapping roads the nearest centreline
    wins; validation reports intersections whose requested heights conflict.
    """
    x, z = _number(x, "sample x"), _number(z, "sample z")
    nodes = {node["id"]: node for node in layout["nodes"]}
    pads = []
    for node in nodes.values():
        nx, ny, nz = node["position"]
        distance = math.hypot(x - nx, z - nz)
        if distance < 1e-9:
            return float(ny)
        radius = node.get("radius", 0)
        if radius > 0:
            pads.append((distance / radius, node["id"], distance, radius, ny))
    pads.sort()
    if pads and pads[0][0] <= 1:
        return float(pads[0][4])

    terrain = _geology(x, z)
    # Graded pad shoulders soften their edges without changing flat pad tops.
    best_pad = None
    for _, identifier, distance, radius, elevation in pads:
        shoulder = max(5.0, min(18.0, radius * 0.35))
        weight = 1 - _smooth((distance - radius) / shoulder)
        candidate = (weight, identifier, elevation)
        if weight > 0 and (best_pad is None or candidate[:2] > best_pad[:2]):
            best_pad = candidate
    if best_pad:
        weight, _, elevation = best_pad
        terrain += (elevation - terrain) * weight

    nearest_road = None
    for link in layout["links"]:
        if link["kind"] != "ground":
            continue
        distance, elevation = _segment_sample(nodes[link["a"]], nodes[link["b"]], x, z)
        half_width = link["width"] / 2
        shoulder = max(5.0, link["width"])
        weight = 1 - _smooth((distance - half_width) / shoulder)
        candidate = (distance, link["id"], elevation, weight)
        if weight > 0 and (nearest_road is None or candidate[:2] < nearest_road[:2]):
            nearest_road = candidate
    if nearest_road:
        _, _, elevation, weight = nearest_road
        if weight == 1:
            return float(elevation)
        terrain += (elevation - terrain) * weight
    return terrain


def _terrain_issues(layout: dict[str, Any], nodes: dict[str, Any]) -> list[dict[str, Any]]:
    """Sample intended roads, explicitly reporting pad/intersection conflicts."""
    issues = []
    for link in layout["links"]:
        if link["kind"] != "ground":
            continue
        a, b = nodes[link["a"]], nodes[link["b"]]
        ax, _, az = a["position"]
        bx, _, bz = b["position"]
        length = math.hypot(bx - ax, bz - az)
        count = max(2, math.ceil(length / 2))
        samples = {i / count for i in range(count + 1)}
        samples.update(
            (min(1.0, a.get("radius", 0) / length), max(0.0, 1 - b.get("radius", 0) / length))
        )
        # Project every pad centre too: a narrow crossing must not hide between samples.
        for node in nodes.values():
            nx, _, nz = node["position"]
            t = ((nx - ax) * (bx - ax) + (nz - az) * (bz - az)) / (length * length)
            if 0 <= t <= 1:
                samples.add(t)
        conflicts = []
        for t in sorted(samples):
            x, z = ax + (bx - ax) * t, az + (bz - az) * t
            _, expected = _segment_sample(a, b, x, z)
            actual = height_at(layout, x, z)
            if abs(actual - expected) > HEIGHT_TOLERANCE:
                conflicts.append((abs(actual - expected), x, z, expected, actual))
        if conflicts:
            deviation, x, z, expected, actual = max(conflicts)
            issues.append(
                {
                    "kind": "uncovered_ground_path",
                    "link": link["id"],
                    "position": [x, actual, z],
                    "expected_height": expected,
                    "max_height_error": deviation,
                    "conflicting_samples": len(conflicts),
                }
            )
    return issues


def validate_layout(data: Any) -> dict[str, Any]:
    """Validate the graph and designed ramp grades; report terrain conflicts.

    The returned report is JSON-serializable. A clear terrain report establishes
    sampled blockout consistency only, not mesh collision or runtime traversal.
    Unknown metadata is accepted and left untouched.
    """
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise ValueError("layout schema must be 1")
    if data.get("units") != "metres":
        raise ValueError("layout units must be metres")
    bounds = data.get("bounds")
    if not isinstance(bounds, dict):
        raise ValueError("layout bounds are required")
    limits = {
        key: _number(bounds.get(key), f"bounds.{key}") for key in ("minX", "maxX", "minZ", "maxZ")
    }
    if limits["minX"] >= limits["maxX"] or limits["minZ"] >= limits["maxZ"]:
        raise ValueError("layout bounds must have positive extent")
    if not isinstance(data.get("nodes"), list) or not data["nodes"]:
        raise ValueError("layout needs at least one node")
    if not isinstance(data.get("links"), list):
        raise ValueError("layout links must be a list")

    nodes: dict[str, Any] = {}
    centres: dict[tuple[float, float], float] = {}
    for item in data["nodes"]:
        if not isinstance(item, dict):
            raise ValueError("each node must be an object")
        identifier = _identifier(item.get("id"), "node id")
        if identifier in nodes:
            raise ValueError(f"duplicate node id: {identifier}")
        p = item.get("position")
        if not isinstance(p, list) or len(p) != 3:
            raise ValueError(f"node {identifier} needs position [x, y, z]")
        x, y, z = (_number(value, f"node {identifier} coordinate") for value in p)
        if not limits["minX"] <= x <= limits["maxX"] or not limits["minZ"] <= z <= limits["maxZ"]:
            raise ValueError(f"node {identifier} is outside bounds")
        radius = _number(item.get("radius", 0), f"node {identifier} radius")
        if radius < 0:
            raise ValueError(f"node {identifier} radius cannot be negative")
        if (x, z) in centres and centres[x, z] != y:
            raise ValueError("coincident pad centres cannot have different heightfield elevations")
        centres[x, z] = y
        nodes[identifier] = item

    link_ids: set[str] = set()
    adjacent: dict[str, set[str]] = {identifier: set() for identifier in nodes}
    max_grade = 0.0
    lengths = {"ground": 0.0, "bridge": 0.0}
    for link in data["links"]:
        if not isinstance(link, dict):
            raise ValueError("each link must be an object")
        identifier = _identifier(link.get("id"), "link id")
        if identifier in link_ids:
            raise ValueError(f"duplicate link id: {identifier}")
        link_ids.add(identifier)
        a_id = _identifier(link.get("a"), f"link {identifier} endpoint a")
        b_id = _identifier(link.get("b"), f"link {identifier} endpoint b")
        if a_id not in nodes or b_id not in nodes:
            raise ValueError(f"link {identifier} has an unknown endpoint")
        kind = link.get("kind")
        if kind not in ("ground", "bridge"):
            raise ValueError(f"link {identifier} kind must be ground or bridge")
        width = _number(link.get("width"), f"link {identifier} width")
        if width <= 0:
            raise ValueError(f"link {identifier} width must be positive")
        a, b = nodes[a_id], nodes[b_id]
        ax, ay, az = a["position"]
        bx, by, bz = b["position"]
        length = math.hypot(bx - ax, bz - az)
        if length <= 0:
            raise ValueError(f"link {identifier} must have positive horizontal length")
        run = length - a.get("radius", 0) - b.get("radius", 0) if kind == "ground" else length
        grade = abs(by - ay) / run if run > 0 else (0 if ay == by else math.inf)
        if grade > MAX_GRADE + 1e-9:
            raise ValueError(
                f"link {identifier} exceeds maximum grade {MAX_GRADE:.0%} between pad edges"
            )
        max_grade = max(max_grade, grade)
        lengths[kind] += length
        adjacent[a_id].add(b_id)
        adjacent[b_id].add(a_id)

    visited: set[str] = set()
    pending = [next(iter(nodes))]
    while pending:
        identifier = pending.pop()
        if identifier not in visited:
            visited.add(identifier)
            pending.extend(adjacent[identifier] - visited)
    if len(visited) != len(nodes):
        raise ValueError(
            f"layout graph is disconnected: {', '.join(sorted(nodes.keys() - visited))}"
        )
    positions = [node["position"] for node in nodes.values()]
    return {
        "node_count": len(nodes),
        "link_count": len(link_ids),
        "connected_nodes": len(visited),
        "span": {
            axis: max(p[i] for p in positions) - min(p[i] for p in positions)
            for i, axis in enumerate(("x", "y", "z"))
        },
        "max_grade": max_grade,
        "ground_length": lengths["ground"],
        "bridge_length": lengths["bridge"],
        "terrain_issues": _terrain_issues(data, nodes),
        "scope": "Sampled blockout geometry; runtime collision and traversal are unverified.",
    }


def load_layout(path: str | Path) -> dict[str, Any]:
    """Load UTF-8 JSON (optional BOM) and reject invalid blockout geometry."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    validate_layout(data)
    return data
