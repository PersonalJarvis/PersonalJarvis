"""Generate and verify the complete owned diorama asset inventory (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/figures"))
import glb_tools as gt  # noqa: E402

ASSETS = ROOT / "jarvis/ui/web/frontend/src/assets/society"
INVENTORY = ASSETS / "world/asset-inventory.json"


def inspect(path: Path) -> dict:
    glb = gt.read_glb(path)
    doc = glb.doc
    matrices = gt.world_matrices(doc)
    points = []
    for index, node in enumerate(doc.get("nodes", [])):
        if "mesh" not in node:
            continue
        lo, hi = gt.mesh_bounds(doc, doc["meshes"][node["mesh"]])
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    points.append(gt.mat_point(matrices[index], (x, y, z)))
    bounds = None
    if points:
        bounds = [
            [round(min(p[i] for p in points), 5) for i in range(3)],
            [round(max(p[i] for p in points), 5) for i in range(3)],
        ]
    figure = gt.figure_extras(doc)
    part = gt.part_extras(doc)
    kind = (
        "figure" if figure else "part" if part else "clips" if gt.clips_extras(doc) else "building"
    )
    return {
        "file": path.relative_to(ASSETS).as_posix(),
        "kind": kind,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
        "triangles": sum(gt.triangle_count(doc, m) for m in doc.get("meshes", [])),
        "primitives": sum(len(m["primitives"]) for m in doc.get("meshes", [])),
        "bounds": bounds,
        "animations": sorted(a.get("name", "") for a in doc.get("animations", [])),
    }


def inventory() -> dict:
    return {
        "contract": 2,
        "style": "pixel-diorama",
        "assets": [inspect(p) for p in sorted(ASSETS.rglob("*.glb"))],
    }


def write() -> dict:
    result = inventory()
    INVENTORY.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def validate_building(path: Path, spec: dict) -> list[str]:
    glb = gt.read_glb(path)
    doc = glb.doc
    problems = []
    raw = next(
        (
            n.get("extras", {}).get("jarvis_building")
            for n in doc.get("nodes", [])
            if n.get("extras", {}).get("jarvis_building")
        ),
        None,
    )
    metadata = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(metadata, dict):
        return ["missing building metadata"]
    for key, expected in {
        "contract": 2,
        "id": spec["asset"],
        "collision": spec["collision"],
        "door": spec["door"],
        "stand": spec["stand"],
        "ramps": spec["ramps"],
        "forward": "+Z",
    }.items():
        if metadata.get(key) != expected:
            problems.append(f"manifest mismatch: {key}")
    facts = inspect(path)
    if facts["triangles"] > 3000:
        problems.append("more than 3000 building triangles")
    if facts["primitives"] > 5:
        problems.append("more than five building primitives")
    if len(doc.get("materials", [])) > 2:
        problems.append("more than two building materials")
    if not facts["bounds"] or abs(facts["bounds"][0][1]) > 0.02:
        problems.append("building does not sit on ground")
    for mesh in doc.get("meshes", []):
        for prim in mesh["primitives"]:
            if "COLOR_0" not in prim["attributes"]:
                problems.append("missing palette/cavity colours")
            else:
                for color in gt.accessor_values(glb, prim["attributes"]["COLOR_0"]):
                    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in color):
                        problems.append("invalid palette/cavity colours")
                        break
    # Everything low enough to intersect a walking body stays within the
    # authored collision polygon. Upper-level signs and roof accents may overhang.
    matrices = gt.world_matrices(doc)
    half_w, half_d = [v / 2 for v in spec["sizeM"]]
    for index, node in enumerate(doc.get("nodes", [])):
        if "mesh" not in node:
            continue
        for primitive in doc["meshes"][node["mesh"]]["primitives"]:
            for vertex in gt.accessor_values(glb, primitive["attributes"]["POSITION"]):
                x, y, z = gt.mat_point(matrices[index], vertex)
                if y <= 3 and (abs(x) > half_w + 0.03 or abs(z) > half_d + 0.03):
                    problems.append("body-height geometry exceeds collision footprint")
                    break
    return sorted(set(problems))


if __name__ == "__main__":
    result = write()
    print(f"Inventoried {len(result['assets'])} diorama assets")
