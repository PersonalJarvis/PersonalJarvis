"""Canonical, deterministic graph routing with explicit travel clearance."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from dataclasses import dataclass
from typing import Any

from .models import StationError
from .navigation_models import (
    NavigationOccupancy,
    NavigationRecord,
    RoverRideRecord,
    TravelMode,
    VehicleRecord,
)


@dataclass(frozen=True)
class RouteEdge:
    id: str
    start: str
    end: str
    length: float
    width: float
    modes: frozenset[str]


class NavigationGraph:
    """Copies its inputs; the caller cannot mutate an in-flight route graph.

    This logical checkpoint uses conservative single-occupant edge segments.
    Width checks include body clearance; visual local avoidance is not authority.
    """

    def __init__(self, definition: dict[str, Any]) -> None:
        data = json.loads(json.dumps(definition, allow_nan=False))
        if data["world_id"] != "mars:ordinary":
            raise ValueError("unsupported navigation world")
        nav = data["navigation"]
        self.definition = data
        self.migrations = nav.get("graph_migrations", [])
        signature_nav = {k: v for k, v in nav.items() if k != "graph_migrations"}
        self.version = nav["version"]
        self.layout_version = data["layout_version"]
        if type(self.version) is not int or self.version < 1:
            raise ValueError("invalid graph version")
        if type(self.layout_version) is not int or self.layout_version < 1:
            raise ValueError("invalid layout version")
        self.signature = hashlib.sha256(
            json.dumps(
                [signature_nav, data["stations"], data["spawn"]],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.nodes = {
            node["id"]: tuple(float(v) for v in node["position"]) for node in nav["nodes"]
        }
        if not 1 <= len(self.nodes) <= 512 or len(self.nodes) != len(nav["nodes"]):
            raise ValueError("invalid bounded navigation nodes")
        if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in self.nodes.values()):
            raise ValueError("invalid navigation position")
        self.resource_aliases = {
            "node:" + row["id"]: tuple(row.get("resource_ids", ())) for row in nav["nodes"]
        }
        self.edges: dict[str, RouteEdge] = {}
        self.adjacent: dict[str, list[tuple[str, RouteEdge]]] = {key: [] for key in self.nodes}
        if len(nav["edges"]) > 2048:
            raise ValueError("too many navigation edges")
        for row in nav["edges"]:
            start, end, width = row["from"], row["to"], row["width"]
            if start not in self.nodes or end not in self.nodes or start == end:
                raise ValueError("invalid edge endpoint")
            length = math.dist(self.nodes[start], self.nodes[end])
            if length <= 0 or not math.isfinite(width) or width <= 0:
                raise ValueError("invalid route dimensions")
            edge = RouteEdge(row["id"], start, end, length, width, frozenset(row["modes"]))
            if edge.id in self.edges:
                raise ValueError("duplicate route identity")
            self.edges[edge.id] = edge
            self.resource_aliases["edge:" + edge.id] = tuple(row.get("resource_ids", ()))
            self.adjacent[start].append((end, edge))
            self.adjacent[end].append((start, edge))
        # Visit-only destinations share physical slots with task stations, but
        # have no capability and can never be dispatched through a task API.
        destinations = [*data["stations"], *nav.get("destinations", [])]
        self.stations = {row["id"]: row["anchor"] for row in destinations}
        if (
            not 1 <= len(self.stations) <= 64
            or len(self.stations) != len(destinations)
            or any(
                anchor not in self.nodes or row["capacity"] != 1
                for row in destinations
                for anchor in [row["anchor"]]
            )
        ):
            raise ValueError("invalid physical station slot")
        self.docks = {row["id"]: row for row in nav.get("rover_docks", [])}
        self.rovers = {row["id"]: row for row in nav.get("rovers", [])}
        self.geometry = None
        if self.rovers:
            from .mobility_geometry import MobilityGeometry

            self.geometry = MobilityGeometry(data)
            if len(self.rovers) > 16 or not 2 <= len(self.docks) <= 32:
                raise ValueError("invalid bounded rover catalogue")
            for dock in self.docks.values():
                node = dock["node_id"]
                if node not in self.nodes:
                    raise ValueError("unknown rover dock node")
                for station in [dock["boarding_station_id"], *dock["exit_station_ids"]]:
                    if station not in self.stations:
                        raise ValueError("unknown rover pedestrian dock station")
                    point = self.nodes[self.stations[station]]
                    if not 0.5 <= math.dist(point, self.nodes[node]) <= 4.0:
                        raise ValueError("invalid rover transfer clearance")
                    if not self.geometry.capsule_clear(point):
                        raise ValueError("unsafe rover pedestrian dock station")
                self.stations["rover-dock:" + dock["id"]] = node
            for rover in self.rovers.values():
                if (
                    rover["home_dock_id"] not in self.docks
                    or rover["seat_capacity"] != 1
                    or rover["width_m"] != 3.4
                    or rover["length_m"] != 4.8
                    or rover["height_m"] != 2.6
                    or rover["speed_m_s"] != 4
                ):
                    raise ValueError("unsupported rover envelope")
            for dock in self.docks.values():
                if not self.geometry.vehicle_clear(
                    self.nodes[dock["node_id"]], self.dock_yaw(dock["id"])
                ):
                    raise ValueError("unsafe rover dock")
        spawn = data["spawn"]["position"]
        self.spawn_node = min(self.nodes, key=lambda key: (math.dist(spawn, self.nodes[key]), key))

    @staticmethod
    def supports(edge: RouteEdge, mode: TravelMode) -> bool:
        # Declared logical envelopes; final character/rover geometry still needs alignment.
        clearance = 1.2 if mode is TravelMode.PEDESTRIAN else 3.4
        return mode.value in edge.modes and edge.width >= clearance

    def path(
        self, start: str, station_id: str, mode: TravelMode, blocked: frozenset[str] = frozenset()
    ) -> tuple[str, ...] | None:
        if station_id not in self.stations:
            raise StationError("unknown_navigation_station", 404)
        target = self.stations[station_id]
        if start not in self.nodes:
            return None
        pending: list[tuple[float, str, tuple[str, ...]]] = [(0, start, ())]
        best: dict[str, float] = {start: 0}
        while pending:
            distance, node, route = heapq.heappop(pending)
            if distance != best[node]:
                continue
            if node == target:
                return route
            for destination, edge in sorted(self.adjacent[node], key=lambda item: item[1].id):
                candidate = distance + edge.length
                if (
                    edge.id not in blocked
                    and self.supports(edge, mode)
                    and candidate < best.get(destination, math.inf)
                ):
                    best[destination] = candidate
                    heapq.heappush(pending, (candidate, destination, (*route, edge.id)))
        return None

    def destination(self, edge_id: str, node: str) -> str:
        edge = self.edges[edge_id]
        if node not in (edge.start, edge.end):
            raise StationError("invalid_navigation_location", 409)
        return edge.end if node == edge.start else edge.start

    def position(self, start: str, end: str, fraction: float) -> tuple[float, float, float]:
        return tuple(
            a + (b - a) * fraction for a, b in zip(self.nodes[start], self.nodes[end], strict=True)
        )  # type: ignore[return-value]

    def resources(self, resource_id: str) -> tuple[str, ...]:
        return (resource_id, *self.resource_aliases.get(resource_id, ()))

    def dock_yaw(self, dock_id: str) -> float:
        dock = self.docks[dock_id]
        if "yaw" in dock:
            return float(dock["yaw"])
        node = dock["node_id"]
        for destination, edge in self.adjacent[node]:
            if self.supports(edge, TravelMode.ROVER):
                start, end = self.nodes[node], self.nodes[destination]
                return math.atan2(end[0] - start[0], end[2] - start[2])
        raise ValueError("rover dock has no compatible road")

    def occupancies(
        self,
        records: list[NavigationRecord],
        vehicles: list[VehicleRecord] = (),
        rides: list[RoverRideRecord] = (),
    ) -> tuple[NavigationOccupancy, ...]:
        """Derive bodies from each actor's latest receipt, even terminal receipts.

        An edge chosen while queueing with zero progress is not occupied yet.
        Bodies at endpoints occupy nodes and any station sharing that anchor.
        Graph changes do not silently erase old physical resource identities.
        """
        attached = {ride.agent_id for ride in rides if ride.attached}
        latest = {record.agent_id: record for record in records}
        bodies = [record for agent_id, record in latest.items() if agent_id not in attached]
        occupied = []
        for record in [*bodies, *vehicles]:
            if record.presence == "spawn_queue":
                continue
            if record.edge_id is not None and 0 < record.edge_progress < 1:
                resources = ["edge:" + record.edge_id]
            else:
                node = record.current_node
                resources = ["node:" + node]
                resources.extend(
                    "station:" + station
                    for station, anchor in self.stations.items()
                    if anchor == node
                )
            resources = list(
                dict.fromkeys(item for resource in resources for item in self.resources(resource))
            )
            occupied.extend(
                NavigationOccupancy(
                    resource_id=resource,
                    command_id=record.command_id,
                    agent_id=record.agent_id if isinstance(record, NavigationRecord) else None,
                    actor=record.actor,
                    position=record.position,
                    graph_signature=record.graph_signature,
                )
                for resource in resources
            )
        return tuple(occupied)
