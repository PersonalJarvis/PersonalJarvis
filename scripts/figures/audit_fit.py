#!/usr/bin/env python
"""Fit audit — does every part the creator offers actually sit on the body?

    python scripts/figures/audit_fit.py [--json] [--base knight]

The creator can produce ~500 base+part combinations. Nobody can look at that
many, and the eye is the wrong instrument anyway: a hat that leaves 2 cm of
skull showing is a bug at any zoom, and a plate cut for one torso hanging in
front of a wider one is a bug in the numbers before it is one in a screenshot.

So this reads the SHIPPED GLBs, groups every vertex by the joint that owns it,
and compares the box a part occupies on a joint against the box the body
occupies on the same joint. It reports, per combination:

* ``floats``    — a gap between the part and the body region it hangs on
* ``buried``    — the part is entirely inside the body; nobody will see it
* ``exposes``   — a covering piece (headgear, torso_over, belt) is narrower or
                  shallower than the body under it, so the body pokes out
* ``off-centre``— the part is not centred on the body region it belongs to
* ``oversized`` — the part dwarfs the body region, which reads as a prop
                  floating around the figure rather than worn on it

Rest pose only, and that is the point: a fit problem is a rest-pose problem.
Stdlib only (`glb_tools.py`); never imports ``jarvis.*``.

Exit codes: 0 = every offered combination fits, 1 = at least one does not,
78 = nothing to audit (no catalog / no assets).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FIGURES = _REPO / "jarvis/ui/web/frontend/src/assets/society/figures"
_CATALOG = _REPO / "jarvis/ui/web/frontend/src/components/society/figures/catalog.json"
_TOOLS = Path(__file__).resolve().parent / "glb_tools.py"

EXIT_SKIPPED = 78

#: Styles the creator never offers, so their combinations are not audited.
RESERVED_STYLES = {"spirit", "custom"}

#: Which way a piece is expected to cover the body under it. A garment says
#: so in the catalog (`covers`), because "covers" is not one question: an
#: apron covers the front, a vest the sides and back, a belt all the way
#: round, and marking any of them short for a gap it has on purpose is noise.
#: Headgear that hides the hair is a hat and covers every way.
COVER_AXES = {"sides": 0, "front_back": 2}

#: Slots held in a hand: they hang off a slot bone in open air, so "floats"
#: and "off-centre" mean nothing for them.
HELD_SLOTS = {"hand_l", "hand_r"}

#: Slots that hang clear of the body along one axis BY DESIGN — a cape falls
#: behind the chest and past the hips, glasses sit in front of the face — so
#: their depth and height are not measured against the body region.
TRAILING_SLOTS = {"back", "face_extra", "tail_extra"}

#: Metres. A part may sit this far from the body before it reads as floating.
GAP_TOLERANCE = 0.06
#: Metres. How far a covering piece may fall short of the body under it.
COVER_TOLERANCE = 0.015
#: Metres. How far a part may drift SIDEWAYS from the body region it hangs on.
#: Sideways only: front-to-back drift is what a cape and a pair of glasses are.
CENTRE_TOLERANCE = 0.10
#: A part wider than this multiple of the body region reads as scenery.
OVERSIZE_FACTOR = 3.0


def _load_tools():
    if "glb_tools" in sys.modules:
        return sys.modules["glb_tools"]
    spec = importlib.util.spec_from_file_location("glb_tools", _TOOLS)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["glb_tools"] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass(frozen=True)
class Box:
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(hi - lo for hi, lo in zip(self.hi, self.lo, strict=True))  # type: ignore

    @property
    def centre(self) -> tuple[float, float, float]:
        return tuple((hi + lo) / 2 for hi, lo in zip(self.hi, self.lo, strict=True))  # type: ignore

    def gap_to(self, other: Box) -> float:
        """0 when the boxes touch or overlap; else the shortest distance between them."""
        out = 0.0
        for axis in range(3):
            d = max(self.lo[axis] - other.hi[axis], other.lo[axis] - self.hi[axis], 0.0)
            out = max(out, d)
        return out

    def inside(self, other: Box, slack: float = 0.0) -> bool:
        return all(
            self.lo[a] >= other.lo[a] - slack and self.hi[a] <= other.hi[a] + slack
            for a in range(3)
        )


def _accessor(gt, glb, index: int):
    return gt.accessor_values(glb, index)


def _apply(matrix, point) -> tuple[float, float, float]:
    """Column-major glTF matrix times a point."""
    x, y, z = point[0], point[1], point[2]
    return (
        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
    )


def joint_boxes(gt, path: Path) -> dict[str, Box]:
    """A box per joint: the vertices that joint owns, in rest-pose model space.

    A vertex belongs to the joint with its largest weight — every piece here is
    rigidly weighted, so that is simply "the bone it hangs on".
    """
    glb = gt.read_glb(path)
    doc = glb.doc
    skins = doc.get("skins", [])
    if not skins:
        return {}
    joints = [doc["nodes"][n].get("name", str(n)) for n in skins[0]["joints"]]
    worlds = gt.world_matrices(doc)
    nodes = doc.get("nodes", [])
    out: dict[str, list[list[float]]] = defaultdict(lambda: [[1e9] * 3, [-1e9] * 3])
    for node_index in gt.skinned_mesh_nodes(doc):
        mesh = doc["meshes"][nodes[node_index]["mesh"]]
        matrix = worlds[node_index]
        for prim in mesh["primitives"]:
            attrs = prim["attributes"]
            if "JOINTS_0" not in attrs or "WEIGHTS_0" not in attrs:
                continue
            positions = _accessor(gt, glb, attrs["POSITION"])
            joint_ids = _accessor(gt, glb, attrs["JOINTS_0"])
            weights = _accessor(gt, glb, attrs["WEIGHTS_0"])
            for pos, ids, ws in zip(positions, joint_ids, weights, strict=True):
                best = max(range(len(ws)), key=lambda i: ws[i])
                if ws[best] <= 0:
                    continue
                name = joints[int(ids[best])]
                world = _apply(matrix, pos)
                box = out[name]
                for axis in range(3):
                    box[0][axis] = min(box[0][axis], world[axis])
                    box[1][axis] = max(box[1][axis], world[axis])
    return {k: Box(tuple(v[0]), tuple(v[1])) for k, v in out.items()}


#: Height of one measuring slice, in metres. Fine enough to separate a hood
#: from the chest under it, coarse enough that a 12-triangle box still lands
#: in several.
SLICE = 0.04


def material_slot(doc: dict, primitive: dict) -> str:
    """The slot a primitive paints, read off its material name (`<id>-hair`)."""
    index = primitive.get("material")
    if index is None:
        return ""
    name = doc.get("materials", [])[index].get("name", "")
    cut = name.rfind("-")
    return name[cut + 1 :] if cut >= 0 else ""


def slice_profile(
    gt, path: pathlib.Path
) -> tuple[dict[str, Box], dict[str, dict[str, dict[int, tuple[float, float, float, float]]]]]:
    """Per-joint boxes AND, per joint, the X/Z span of each height slice.

    The slices are what makes "does this cover the body" answerable: a garment
    is compared with the body only where the two share height.
    """
    glb = gt.read_glb(path)
    doc = glb.doc
    skins = doc.get("skins", [])
    if not skins:
        return {}, {}
    joints = [doc["nodes"][n].get("name", str(n)) for n in skins[0]["joints"]]
    worlds = gt.world_matrices(doc)
    nodes = doc.get("nodes", [])
    boxes: dict[str, list[list[float]]] = defaultdict(lambda: [[1e9] * 3, [-1e9] * 3])
    # joint -> material slot -> height slice -> (x lo, x hi, z lo, z hi)
    slices: dict[str, dict[str, dict[int, list[float]]]] = defaultdict(lambda: defaultdict(dict))
    for node_index in gt.skinned_mesh_nodes(doc):
        mesh = doc["meshes"][nodes[node_index]["mesh"]]
        matrix = worlds[node_index]
        for prim in mesh["primitives"]:
            attrs = prim["attributes"]
            if "JOINTS_0" not in attrs or "WEIGHTS_0" not in attrs:
                continue
            slot = material_slot(doc, prim)
            positions = _accessor(gt, glb, attrs["POSITION"])
            joint_ids = _accessor(gt, glb, attrs["JOINTS_0"])
            weights = _accessor(gt, glb, attrs["WEIGHTS_0"])
            for pos, ids, ws in zip(positions, joint_ids, weights, strict=True):
                best = max(range(len(ws)), key=lambda i: ws[i])
                if ws[best] <= 0:
                    continue
                name = joints[int(ids[best])]
                world = _apply(matrix, pos)
                box = boxes[name]
                for axis in range(3):
                    box[0][axis] = min(box[0][axis], world[axis])
                    box[1][axis] = max(box[1][axis], world[axis])
                key = int(world[1] // SLICE)
                cur = slices[name][slot].get(key)
                if cur is None:
                    slices[name][slot][key] = [world[0], world[0], world[2], world[2]]
                else:
                    cur[0] = min(cur[0], world[0])
                    cur[1] = max(cur[1], world[0])
                    cur[2] = min(cur[2], world[2])
                    cur[3] = max(cur[3], world[2])
    return (
        {k: Box(tuple(v[0]), tuple(v[1])) for k, v in boxes.items()},
        {
            joint: {slot: {i: tuple(v) for i, v in run.items()} for slot, run in by_slot.items()}
            for joint, by_slot in slices.items()
        },
    )


def body_under(
    body_slices: dict[str, dict[str, dict[int, tuple[float, float, float, float]]]],
    joints: list[str],
    lo_y: float,
    hi_y: float,
    hidden: set[str] | None = None,
) -> Box | None:
    """The body's own box, clipped to the height band a garment occupies.

    A hood and a pair of shoulder pads ride the `chest` bone; a vest worn
    under them is not short for letting them show. Clipping to the garment's
    own band asks the question that matters — is it narrower than the body it
    actually sits on — and nothing else.
    """
    lo = [1e9, lo_y, 1e9]
    hi = [-1e9, hi_y, -1e9]
    found = False
    hidden = hidden or set()
    for joint in joints:
        for slot, run in body_slices.get(joint, {}).items():
            # A hat that hides the hair is measured against the head WITHOUT
            # it: the pack's mage keeps his bun in its own material slot so a
            # helmet can switch it off, and comparing against a bun that is
            # not drawn reports head showing that nobody sees.
            if slot in hidden:
                continue
            for key, span in run.items():
                # The band is the garment's OWN height, exactly. A slice of
                # slack either side let the shoulder pads just above a vest,
                # and the ear tips just below a beanie, count as body the
                # garment failed to cover.
                centre = (key + 0.5) * SLICE
                if not (lo_y <= centre <= hi_y):
                    continue
                found = True
                lo[0] = min(lo[0], span[0])
                hi[0] = max(hi[0], span[1])
                lo[2] = min(lo[2], span[2])
                hi[2] = max(hi[2], span[3])
    return Box(tuple(lo), tuple(hi)) if found else None


def merged(boxes: dict[str, Box], names: list[str]) -> Box | None:
    lo = [1e9] * 3
    hi = [-1e9] * 3
    found = False
    for name in names:
        box = boxes.get(name)
        if box is None:
            continue
        found = True
        for axis in range(3):
            lo[axis] = min(lo[axis], box.lo[axis])
            hi[axis] = max(hi[axis], box.hi[axis])
    return Box(tuple(lo), tuple(hi)) if found else None


def audit_pair(
    base_boxes: dict[str, Box],
    part_boxes: dict[str, Box],
    part: dict,
    base_slices: dict | None = None,
    part_slices: dict | None = None,
) -> list[str]:
    """Every way this part fails to sit on this body, in plain words."""
    problems: list[str] = []
    slot = part["slot"]
    joints = sorted(part_boxes)
    if not joints:
        return ["part has no skinned geometry"]
    part_box = merged(part_boxes, joints)
    body_box = merged(base_boxes, joints)
    if part_box is None:
        return ["part has no geometry on its own joints"]
    if body_box is None:
        # A quadruped's `tail_2` may carry no body vertices of its own; the
        # part still rides the bone, so there is nothing to compare against.
        return []
    trailing = slot in TRAILING_SLOTS
    base_slices = base_slices or {}
    part_slices = part_slices or {}

    if slot not in HELD_SLOTS:
        gap = part_box.gap_to(body_box)
        if gap > GAP_TOLERANCE:
            problems.append(f"floats {gap * 100:.1f} cm off the body")
        # Sideways only. A cape IS behind the chest and glasses ARE in front
        # of the face; drifting left or right is the defect.
        drift = abs(part_box.centre[0] - body_box.centre[0])
        if drift > CENTRE_TOLERANCE and not part.get("asymmetric"):
            problems.append(f"sits {drift * 100:.1f} cm off to one side")

    if part_box.inside(body_box, slack=-0.005):
        problems.append("is buried inside the body")

    covers = list(part.get("covers") or [])
    if slot == "headgear" and "hair" in part.get("hides", []):
        covers = ["sides", "front_back"]
    if covers:
        under = body_under(
            base_slices,
            joints,
            part_box.lo[1],
            part_box.hi[1],
            hidden=set(part.get("hides", [])),
        )
        # No body in the garment's own height band means there is nothing for
        # it to cover. Falling back to the whole joint here measured a chest
        # plate against a hood two hand-widths above it.
        for name in covers if under else ():
            axis = COVER_AXES[name]
            short = max(under.hi[axis] - part_box.hi[axis], part_box.lo[axis] - under.lo[axis])
            if short > COVER_TOLERANCE:
                problems.append(f"leaves the body showing at the {name} by {short * 100:.1f} cm")

    if slot == "tail_extra":
        axes: tuple[tuple[int, str], ...] = ()  # a bow IS wider than the tail it ties
    elif trailing:
        axes = ((0, "wide"),)
    else:
        axes = ((0, "wide"), (1, "tall"), (2, "deep"))
    for axis, name in axes:
        body_size = body_box.size[axis]
        if body_size > 0.02 and part_box.size[axis] > body_size * OVERSIZE_FACTOR:
            problems.append(
                f"is {part_box.size[axis] / body_size:.1f}x as {name} as the body part it hangs on"
            )
    return problems


def offered(catalog: dict) -> list[tuple[dict, dict]]:
    """Every base+part pair the creator can actually produce."""
    pairs = []
    for base in catalog["bases"]:
        styles = [s for s in base["styles"] if s not in RESERVED_STYLES]
        if not styles:
            continue
        seen: set[str] = set()
        for part in catalog["parts"]:
            if part["archetype"] != base["archetype"] or part["id"] in seen:
                continue
            if not set(part["styles"]) & set(styles):
                continue
            if part.get("fits_family") and part["fits_family"] != base.get("family"):
                continue
            if part.get("fits_size") and part["fits_size"] != base.get("fitSize"):
                continue
            seen.add(part["id"])
            pairs.append((base, part))
    return pairs


def run(only_base: str | None = None) -> dict[str, list[str]]:
    gt = _load_tools()
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    boxes: dict[str, tuple[dict[str, Box], dict]] = {}

    def cached(file: str):
        if file not in boxes:
            boxes[file] = slice_profile(gt, _FIGURES / file)
        return boxes[file]

    findings: dict[str, list[str]] = {}
    for base, part in offered(catalog):
        if only_base and base["base"] != only_base:
            continue
        base_boxes, base_slices = cached(base["file"])
        part_boxes, part_slices = cached(part["file"])
        problems = audit_pair(base_boxes, part_boxes, part, base_slices, part_slices)
        if problems:
            findings[f"{base['base']} + {part['id']}"] = problems
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--base", default=None)
    args = parser.parse_args(argv)
    if not _CATALOG.exists() or not _FIGURES.exists():
        print("audit_fit: nothing to audit (no catalog or no assets)")
        return EXIT_SKIPPED

    findings = run(args.base)
    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        by_problem: dict[str, list[str]] = defaultdict(list)
        for combo, problems in findings.items():
            for problem in problems:
                by_problem[problem.split(" by ")[0].split(" off")[0]].append(combo)
        for kind, combos in sorted(by_problem.items(), key=lambda kv: -len(kv[1])):
            print(f"\n{len(combos):4d}  {kind}")
            for combo in combos[:12]:
                print(f"        {combo}")
            if len(combos) > 12:
                print(f"        … and {len(combos) - 12} more")
        print(f"\naudit_fit: {len(findings)} combination(s) with a fit problem")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
