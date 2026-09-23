"""Project authored Outpost collision/support into the portable Mars definition."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = ROOT / "jarvis/society/mars/definition.json"
FRONTEND = ROOT / "jarvis/ui/web/frontend/src/components/society/mars/worldDefinition.json"
SOURCE = ROOT / "art/studies/mars-outpost-reference/source/geometry-contract.json"


def project_collision(definition: dict, source: dict) -> dict:
    """Keep geometry in world metres; source-relative paths never enter runtime."""
    origin = source["world_translation"]
    boxes, surfaces = [], []
    for collider in source["colliders"]:
        if collider["shape"] == "box":
            center, size = collider["center"], collider["size"]
            boxes.append(
                {
                    "id": "outpost:" + collider["id"],
                    "min": [center[i] + origin[i] - size[i] / 2 for i in range(3)],
                    "max": [center[i] + origin[i] + size[i] / 2 for i in range(3)],
                }
            )
        elif collider["shape"] == "plateau":
            surfaces.append(
                {
                    "id": "outpost:plateau",
                    "polygon": [[x + origin[0], z + origin[2]] for x, z in collider["footprint"]],
                    "plane": [0, 0, origin[1] + collider["top_y"]],
                }
            )
    terrace = source["walk_surfaces"]["terrace"]
    surfaces.append(
        {
            "id": "outpost:terrace",
            "polygon": [[x + origin[0], z + origin[2]] for x, z in terrace["footprint"]],
            "plane": [0, 0, origin[1] + terrace["top_y"]],
        }
    )
    nodes = {row["id"]: row["position"] for row in definition["navigation"]["nodes"]}
    # The split navigation segments share the same authored, continuous road.
    # Runtime support must not acquire cracks at artificial graph boundaries.
    edges = {
        row["id"]: row
        for row in definition["navigation"].get("surface_edges", definition["navigation"]["edges"])
    }
    source_edges = dict(edges)
    for migration in definition["navigation"].get("graph_migrations", []):
        for row in migration["source_descriptor"]["navigation"]["edges"]:
            source_edges.setdefault(row["id"], row)
    for edge_id in ("route-01", "route-02", "route-03", "route-05"):
        edge = source_edges[edge_id]
        start, end = nodes[edge["from"]], nodes[edge["to"]]
        dx, dz = end[0] - start[0], end[2] - start[2]
        distance = math.hypot(dx, dz)
        rx, rz = dz / distance * edge["width"] / 2, -dx / distance * edge["width"] / 2
        slope = (end[1] - start[1]) / distance**2
        a, b = slope * dx, slope * dz
        offset = source["walk_surfaces"][edge_id]["node_height_offset"]
        surfaces.append(
            {
                "id": "outpost:" + edge_id,
                "polygon": [
                    [start[0] - rx, start[2] - rz],
                    [start[0] + rx, start[2] + rz],
                    [end[0] + rx, end[2] + rz],
                    [end[0] - rx, end[2] - rz],
                ],
                "plane": [a, b, start[1] + offset - a * start[0] - b * start[2]],
            }
        )
    return {
        "version": 1,
        "source_sha256": hashlib.sha256(
            json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest(),
        "solid_boxes": boxes,
        "support_surfaces": surfaces,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Reject stale projections without writing"
    )
    args = parser.parse_args()
    definition = json.loads(CANONICAL.read_text(encoding="utf-8"))
    projected = project_collision(definition, json.loads(SOURCE.read_text(encoding="utf-8")))
    if args.check:
        if definition["navigation"].get("collision") != projected:
            raise SystemExit("Canonical Mars collision projection is stale")
        if json.loads(FRONTEND.read_text(encoding="utf-8")) != definition:
            raise SystemExit("Frontend Mars definition projection is stale")
    else:
        definition["navigation"]["collision"] = projected
        encoded = json.dumps(definition, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        CANONICAL.write_text(encoded, encoding="utf-8")
        FRONTEND.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
