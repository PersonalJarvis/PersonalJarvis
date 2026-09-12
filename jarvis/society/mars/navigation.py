"""Canonical, deterministic graph routing with explicit travel clearance."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from dataclasses import dataclass
from typing import Any

from .models import StationError
from .navigation_models import NavigationOccupancy, NavigationRecord, TravelMode


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
        self.version = nav["version"]
        self.layout_version = data["layout_version"]
        if type(self.version) is not int or self.version < 1:
            raise ValueError("invalid graph version")
        if type(self.layout_version) is not int or self.layout_version < 1:
            raise ValueError("invalid layout version")
        self.signature = hashlib.sha256(
            json.dumps(
                [nav, data["stations"], data["spawn"]], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        self.nodes = {
            node["id"]: tuple(float(v) for v in node["position"]) for node in nav["nodes"]
        }
        if not 1 <= len(self.nodes) <= 512 or len(self.nodes) != len(nav["nodes"]):
            raise ValueError("invalid bounded navigation nodes")
        if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in self.nodes.values()):
            raise ValueError("invalid navigation position")
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
            self.adjacent[start].append((end, edge))
            self.adjacent[end].append((start, edge))
        self.stations = {row["id"]: row["anchor"] for row in data["stations"]}
        if (
            not 1 <= len(self.stations) <= 64
            or len(self.stations) != len(data["stations"])
            or any(
                anchor not in self.nodes or row["capacity"] != 1
                for row in data["stations"]
                for anchor in [row["anchor"]]
            )
        ):
            raise ValueError("invalid physical station slot")
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

    def occupancies(self, records: list[NavigationRecord]) -> tuple[NavigationOccupancy, ...]:
        """Derive bodies from each actor's latest receipt, even terminal receipts.

        An edge chosen while queueing with zero progress is not occupied yet.
        Bodies at endpoints occupy nodes and any station sharing that anchor.
        Graph changes do not silently erase old physical resource identities.
        """
        latest = {record.agent_id: record for record in records}
        occupied = []
        for record in latest.values():
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
            occupied.extend(
                NavigationOccupancy(
                    resource_id=resource,
                    command_id=record.command_id,
                    agent_id=record.agent_id,
                    position=record.position,
                    graph_signature=record.graph_signature,
                )
                for resource in resources
            )
        return tuple(occupied)
