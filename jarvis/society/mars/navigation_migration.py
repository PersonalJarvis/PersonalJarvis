"""Conservative graph migration: only proven geometry-preserving subdivisions."""

from __future__ import annotations

import hashlib
import json
import math


def _descriptor_signature(descriptor):
    nav = {
        key: value for key, value in descriptor["navigation"].items() if key != "graph_migrations"
    }
    return hashlib.sha256(
        json.dumps(
            [nav, descriptor["stations"], descriptor["spawn"]],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def migrate_record(record, graph):
    """Retain a frozen record unless its exact old graph and pose can be proven."""
    if record.graph_signature == graph.signature:
        return record
    for migration in graph.migrations:
        descriptor = migration["source_descriptor"]
        if (
            migration["source_signature"] != record.graph_signature
            or _descriptor_signature(descriptor) != record.graph_signature
        ):
            continue
        old = descriptor["navigation"]
        nodes = {row["id"]: tuple(row["position"]) for row in old["nodes"]}
        edges = {row["id"]: row for row in old["edges"]}
        stations = {
            row["id"]: row["anchor"]
            for row in [*descriptor["stations"], *old.get("destinations", [])]
        }
        if (
            any(graph.nodes.get(key) != point for key, point in nodes.items())
            or any(graph.stations.get(key) != node for key, node in stations.items())
            or descriptor["spawn"] != graph.definition["spawn"]
        ):
            continue
        splits = migration.get("edge_splits", {})
        mapped_edges = {}
        valid = True
        for edge_id, edge in edges.items():
            chain = splits.get(edge_id, [edge_id])
            node = edge["from"]
            a, b = nodes[node], nodes[edge["to"]]
            length = math.dist(a, b)
            segments, total = [], 0.0
            for new_id in chain:
                new = graph.edges.get(new_id)
                if (
                    new is None
                    or node not in (new.start, new.end)
                    or new.width != edge["width"]
                    or new.modes != frozenset(edge["modes"])
                ):
                    valid = False
                    break
                end = new.end if node == new.start else new.start
                point = graph.nodes[end]
                distance = math.dist(a, point)
                # Both triangle equality and accumulated length prohibit detours,
                # reversals and changes disguised as a split.
                if not math.isclose(
                    distance + math.dist(point, b), length, abs_tol=1e-7
                ) or not math.isclose(total + new.length, distance, abs_tol=1e-7):
                    valid = False
                    break
                segments.append((new_id, node, end, total / length, (total + new.length) / length))
                total += new.length
                node = end
            if not valid or node != edge["to"] or not math.isclose(total, length, abs_tol=1e-7):
                valid = False
                break
            mapped_edges[edge_id] = segments
        if not valid or record.current_node not in nodes:
            continue
        updates = {}
        if record.edge_id and 0 < record.edge_progress < 1:
            edge = edges.get(record.edge_id)
            if edge is None or {record.current_node, record.next_node} != {
                edge["from"],
                edge["to"],
            }:
                continue
            forward = record.current_node == edge["from"]
            t = record.edge_progress if forward else 1 - record.edge_progress
            a, b = nodes[edge["from"]], nodes[edge["to"]]
            expected = tuple(x + (y - x) * t for x, y in zip(a, b, strict=True))
            if math.dist(expected, record.position) > 1e-7:
                continue
            for edge_id, start, end, lo, hi in mapped_edges[record.edge_id]:
                if lo - 1e-10 <= t <= hi + 1e-10:
                    q = min(1.0, max(0.0, (t - lo) / (hi - lo)))
                    if q < 1e-9 or q > 1 - 1e-9:
                        updates = {
                            "current_node": start if q < 0.5 else end,
                            "edge_id": None,
                            "next_node": None,
                            "edge_progress": 0,
                        }
                    else:
                        updates = {
                            "current_node": start if forward else end,
                            "next_node": end if forward else start,
                            "edge_id": edge_id,
                            "edge_progress": q if forward else 1 - q,
                        }
                    break
        else:
            if math.dist(nodes[record.current_node], record.position) > 1e-7:
                continue
            updates = {"edge_id": None, "next_node": None, "edge_progress": 0}
        return record.model_copy(
            update={
                **updates,
                "path": (),
                "graph_signature": graph.signature,
                "graph_version": graph.version,
                "layout_version": graph.layout_version,
            }
        )
    return record
