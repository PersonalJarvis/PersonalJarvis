"""Packaged canonical Mars layout, independent of rendering and authoring tools."""

from __future__ import annotations

import json
import math
from importlib.resources import files
from typing import Any

from .mobility_geometry import MobilityGeometry


def validate_definition(data: dict[str, Any]) -> None:
    """Reject inconsistent layout/route identities before either client uses them."""
    if data.get("schema_version") != 1 or data.get("layout_version") != 1:
        raise ValueError("unsupported Mars definition version")
    if data.get("world_id") != "mars:ordinary" or data.get("units") != "metres":
        raise ValueError("unsupported Mars world scope or units")
    bounds = data["bounds"]

    def vector(value: Any) -> None:
        if (
            not isinstance(value, list)
            or len(value) != 3
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in value
            )
        ):
            raise ValueError("expected a finite three-dimensional position")

    vector(bounds["min"])
    vector(bounds["max"])
    if any(a >= b for a, b in zip(bounds["min"], bounds["max"], strict=True)):
        raise ValueError("invalid world bounds")

    def position(value: Any) -> None:
        vector(value)
        if any(
            v < lo or v > hi for v, lo, hi in zip(value, bounds["min"], bounds["max"], strict=True)
        ):
            raise ValueError("position lies outside world bounds")

    def index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result = {row["id"]: row for row in rows}
        if len(rows) != len(result) or any(not key for key in result):
            raise ValueError("duplicate or empty world identity")
        return result

    districts = index(data["districts"])
    for district in districts.values():
        position(district["center"])
        position(
            [
                district["center"][0],
                district["center"][1] + district["landmark_height"],
                district["center"][2],
            ]
        )
    buildings = index(data["buildings"])
    for building in buildings.values():
        if building["district_id"] not in districts:
            raise ValueError("building references an unknown district")
        position(building["position"])
        vector(building["size"])
        if min(building["size"]) <= 0:
            raise ValueError("building dimensions must be positive")
        for sign in (-1, 1):
            position(
                [
                    building["position"][0] + sign * building["size"][0] / 2,
                    building["position"][1] + (building["size"][1] if sign == 1 else 0),
                    building["position"][2] + sign * building["size"][2] / 2,
                ]
            )
    navigation = data["navigation"]
    if navigation["version"] != data["layout_version"]:
        raise ValueError("navigation and layout versions differ")
    nodes = index(navigation["nodes"])
    edges = index(navigation["edges"])
    adjacent: dict[str, set[str]] = {key: set() for key in nodes}
    for node in nodes.values():
        position(node["position"])
    for edge in edges.values():
        start, end = edge["from"], edge["to"]
        if start not in nodes or end not in nodes or start == end:
            raise ValueError("route references an invalid endpoint")
        width = edge["width"]
        if not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0:
            raise ValueError("route width must be positive")
        if "pedestrian" not in edge["modes"]:
            raise ValueError("colony routes must retain pedestrian access")
        if "rover" in edge["modes"] and width < 7:
            raise ValueError("rover route lacks required clearance")
        adjacent[start].add(end)
        adjacent[end].add(start)
    if not nodes:
        raise ValueError("world requires navigation nodes")
    visited: set[str] = set()
    pending = [next(iter(nodes))]
    while pending:
        key = pending.pop()
        if key not in visited:
            visited.add(key)
            pending.extend(adjacent[key] - visited)
    if visited != set(nodes):
        raise ValueError("colony navigation contains a disconnected route")
    stations = index(data["stations"])
    for station in stations.values():
        building = buildings.get(station["building_id"])
        if (
            building is None
            or building["district_id"] != station["district_id"]
            or station["anchor"] not in visited
            or station["capacity"] != 1
        ):
            raise ValueError("invalid station location or capacity")
    destinations = index(navigation.get("destinations", []))
    if stations.keys() & destinations.keys() or len(stations) + len(destinations) > 64:
        raise ValueError("invalid bounded navigation destination identities")
    for destination in destinations.values():
        if (
            destination["anchor"] not in visited
            or type(destination["capacity"]) is not int
            or destination["capacity"] != 1
            or not isinstance(destination.get("name"), str)
            or not destination["name"].strip()
            or set(destination) != {"id", "name", "anchor", "capacity"}
        ):
            raise ValueError("invalid visit-only destination")
    for row in [*nodes.values(), *edges.values()]:
        resources = row.get("resource_ids", [])
        if (
            not isinstance(resources, list)
            or len(resources) > 16
            or len(set(resources)) != len(resources)
            or any(not isinstance(key, str) or not key or len(key) > 128 for key in resources)
        ):
            raise ValueError("invalid shared physical resource identities")
    rovers = index(navigation.get("rovers", []))
    docks = index(navigation.get("rover_docks", []))
    if bool(rovers) != bool(docks) or len(rovers) > 8 or len(docks) > 16:
        raise ValueError("invalid bounded rover catalog")
    if rovers:
        geometry = MobilityGeometry(data)
        visits = {**stations, **destinations}
        surface_edges = index(navigation["surface_edges"])
        for edge in surface_edges.values():
            if edge["from"] not in nodes or edge["to"] not in nodes:
                raise ValueError("unknown authored road endpoint")
        for dock in docks.values():
            if dock["node_id"] not in nodes or dock["boarding_station_id"] not in visits:
                raise ValueError("invalid rover dock location")
            yaw = dock["yaw"]
            if isinstance(yaw, bool) or not isinstance(yaw, (float, int)) or not math.isfinite(yaw):
                raise ValueError("invalid rover dock orientation")
            exits = dock["exit_station_ids"]
            if not 2 <= len(exits) <= 4 or len(set(exits)) != len(exits):
                raise ValueError("rover dock requires distinct alternative exits")
            anchors = [dock["boarding_station_id"], *exits]
            if len(set(anchors)) != len(anchors):
                raise ValueError("rover boarding and exit anchors must be distinct")
            center = nodes[dock["node_id"]]["position"]
            for station in anchors:
                if station not in destinations:
                    raise ValueError("rover access must use visit-only destinations")
                point = nodes[visits[station]["anchor"]]["position"]
                if not 2.3 <= math.dist(center, point) <= 3 or not geometry.capsule_clear(point):
                    raise ValueError("rover access lacks adjacent supported clearance")
        for rover in rovers.values():
            if (
                rover["home_dock_id"] not in docks
                or type(rover["seat_capacity"]) is not int
                or rover["seat_capacity"] != 1
            ):
                raise ValueError("invalid rover home or seat capacity")
            dimensions = [rover[key] for key in ("width_m", "length_m", "height_m", "speed_m_s")]
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 < value <= 10
                for value in dimensions
            ):
                raise ValueError("invalid rover physical dimensions")
            for dock in docks.values():
                if not geometry.vehicle_clear(
                    nodes[dock["node_id"]]["position"], dock["yaw"], *dimensions[:3]
                ):
                    raise ValueError("rover dock lacks complete supported clearance")
                center = nodes[dock["node_id"]]["position"]
                anchors = [dock["boarding_station_id"], *dock["exit_station_ids"]]
                points = [nodes[visits[anchor]["anchor"]]["position"] for anchor in anchors]
                sine, cosine = math.sin(dock["yaw"]), math.cos(dock["yaw"])
                for i, point in enumerate(points):
                    dx, dz = point[0] - center[0], point[2] - center[2]
                    across = abs(dx * cosine - dz * sine)
                    along = abs(dx * sine + dz * cosine)
                    clearance = math.hypot(
                        max(0, across - dimensions[0] / 2),
                        max(0, along - dimensions[1] / 2),
                    )
                    if clearance < 0.6:
                        raise ValueError("rover access overlaps the vehicle body")
                    if any(math.dist(point, previous) < 1.2 for previous in points[:i]):
                        raise ValueError("rover access anchors overlap each other")
    position(data["spawn"]["position"])


def load_definition() -> dict[str, Any]:
    """Read a fresh value so callers cannot mutate another world's definition."""
    data = json.loads(files(__package__).joinpath("definition.json").read_text(encoding="utf-8"))
    validate_definition(data)
    return data
