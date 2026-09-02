#!/usr/bin/env python
"""Figure-asset gate — every shipped society figure honours the character contract.

Why this exists: the agent society's island renders any character through ONE
runtime that knows archetypes, bone names, clip names and a forward axis
(docs/agent-society/character-pipeline.md). A figure that faces -Z walks
backwards; a clip with root motion makes the feet slide; a mirrored node
turns the shading inside out; a hips-rooted rig breaks the heading maths. None
of that is caught by TypeScript, a build or a unit test — only by reading the
binary and checking it against ``scripts/figures/contract.json``. So this gate
does exactly that, with the stdlib only (``scripts/figures/glb_tools.py``), and
runs in pre-commit, in CI, and at the end of every build.

Exit codes: 0 = every figure passes, 1 = a violation, 78 = nothing to check
(no ``.glb`` shipped yet), so callers can treat "could not measure" as neutral.
Covered by ``tests/unit/ui/test_society_figures_gate.py``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FIGURES = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "assets" / "society" / "figures"
_CONTRACT = _REPO / "scripts" / "figures" / "contract.json"
_TOOLS = _REPO / "scripts" / "figures" / "glb_tools.py"

NEAREST = 9728
FORBIDDEN_EXTENSIONS = {
    "KHR_draco_mesh_compression",
    "EXT_meshopt_compression",
    "KHR_texture_basisu",
}
ALLOWED_ATTRIBUTES = {"POSITION", "NORMAL", "TEXCOORD_0", "JOINTS_0", "WEIGHTS_0"}
EXIT_SKIPPED = 78


def _load_tools():
    if "glb_tools" in sys.modules:
        return sys.modules["glb_tools"]
    spec = importlib.util.spec_from_file_location("glb_tools", _TOOLS)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Registered BEFORE exec: dataclasses resolve their annotations through
    # sys.modules[cls.__module__], which is None for a loose module (3.14).
    sys.modules["glb_tools"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_contract(path: Path = _CONTRACT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _skin_joint_names(doc: dict, skin: dict) -> list[str]:
    return [doc["nodes"][j].get("name", "") for j in skin["joints"]]


def _top_joints(doc: dict, skin: dict, gt) -> list[str]:
    joints = set(skin["joints"])
    parents = gt.parent_map(doc)
    return [doc["nodes"][j].get("name", "") for j in skin["joints"] if parents[j] not in joints]


def check_file(
    path: Path,
    contract: dict | None = None,
    sources_md: Path | None = None,
    require_ledger: bool = True,
) -> list[str]:
    """Every contract violation of one GLB, as human-readable lines (empty = pass).

    ``require_ledger=False`` is for a person's own imported figure: it must honour
    the contract like a shipped one, but it has no row in the provenance ledger.
    """
    gt = _load_tools()
    contract = contract or load_contract()
    limits = contract["limits"]
    problems: list[str] = []
    try:
        glb = gt.read_glb(path)
    except (ValueError, OSError) as exc:  # a broken file is a failed gate, not a crash
        return [f"unreadable: {exc}"]
    doc = glb.doc

    # 1 — extras
    figure = gt.figure_extras(doc)
    part = gt.part_extras(doc)
    extras = figure or part
    if extras is None:
        return ["no asset.extras.jarvis_figure / jarvis_part block (contract §4.2)"]
    if extras.get("contract") != contract["contract"]:
        problems.append(f"contract version {extras.get('contract')!r} != {contract['contract']}")
    archetype_name = extras.get("archetype")
    archetype = contract["archetypes"].get(archetype_name)
    if archetype is None:
        return problems + [f"unknown archetype {archetype_name!r}"]
    if figure and figure.get("variant") not in archetype["variants"]:
        problems.append(f"unknown variant {figure.get('variant')!r} for {archetype_name}")
    if figure and figure.get("forward") != "+Z":
        problems.append(f"forward must be '+Z', got {figure.get('forward')!r}")

    nodes = doc.get("nodes", [])
    worlds = gt.world_matrices(doc)
    names = gt.node_index_by_name(doc)

    # 2 — FWD marker
    if figure:
        if "FWD" not in names:
            problems.append("no FWD marker node (contract §4.1)")
        else:
            m = worlds[names["FWD"]]
            if m[14] < 0.9:
                problems.append(f"FWD marker sits at z={m[14]:.2f}; the figure faces the wrong way")

    # 3 — scales
    eps = limits["scale_epsilon"]
    for node in nodes:
        _t, _r, s = gt.node_local_trs(node)
        if any(v <= 0 for v in s):
            problems.append(f"node {node.get('name')!r} has a negative/zero scale {s}")
        elif any(abs(v - 1.0) > eps for v in s):
            problems.append(f"node {node.get('name')!r} carries a scale {s}; apply transforms")

    # 5 — skeleton
    skins = doc.get("skins", [])
    skin = skins[0] if skins else None
    if figure and skin is None:
        problems.append("figure has no skin")
    if skin is not None:
        joint_names = _skin_joint_names(doc, skin)
        expected = set(archetype["bones"])
        allowed = expected | set(archetype.get("optional_bones", []))
        have = set(joint_names)
        if figure:
            missing = expected - have
            extra = have - allowed
            if missing:
                problems.append(f"missing bones {sorted(missing)}")
            if extra:
                problems.append(f"unexpected bones {sorted(extra)}")
            tops = _top_joints(doc, skin, gt)
            if tops != ["root"]:
                problems.append(f"top joint must be exactly ['root'], got {tops}")
        if part and not have <= allowed:
            problems.append(f"part binds to bones outside the archetype: {sorted(have - allowed)}")

    # 4 — origin, height, feet
    skinned = gt.skinned_mesh_nodes(doc)
    if figure and skinned:
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for n in skinned:
            mlo, mhi = gt.mesh_bounds(doc, doc["meshes"][nodes[n]["mesh"]])
            lo = [min(a, b) for a, b in zip(lo, mlo, strict=True)]
            hi = [max(a, b) for a, b in zip(hi, mhi, strict=True)]
        if abs(lo[1]) > limits["origin_epsilon"]:
            problems.append(f"lowest vertex at y={lo[1]:.3f}; the feet must stand on y=0")
        height = hi[1] - lo[1]
        if abs(height - float(figure.get("height_m", 0))) > 0.01 * max(height, 1e-6):
            problems.append(f"extras.height_m {figure.get('height_m')} != measured {height:.4f}")
        cx = (lo[0] + hi[0]) / 2
        cz = (lo[2] + hi[2]) / 2
        if abs(cx) > limits["center_epsilon"] or abs(cz) > limits["center_epsilon"]:
            problems.append(f"body centred at ({cx:.3f}, {cz:.3f}); must stand on the origin")

    # 6 — mesh budgets
    budget = archetype["budget"]
    tri_total = 0
    prim_total = 0
    for n in skinned or [i for i, nd in enumerate(nodes) if "mesh" in nd]:
        mesh = doc["meshes"][nodes[n]["mesh"]]
        tri_total += gt.triangle_count(doc, mesh)
        prim_total += len(mesh["primitives"])
        for prim in mesh["primitives"]:
            attrs = set(prim["attributes"])
            if not attrs <= ALLOWED_ATTRIBUTES:
                problems.append(
                    f"mesh {mesh.get('name')!r} carries attributes "
                    f"{sorted(attrs - ALLOWED_ATTRIBUTES)}"
                )
            if "JOINTS_1" in prim["attributes"]:
                problems.append(
                    f"mesh {mesh.get('name')!r} uses more than "
                    f"{limits['max_influences']} influences"
                )
    tri_limit = limits["part_triangles"] if part else budget["triangles"]
    if tri_total > tri_limit:
        problems.append(f"{tri_total} triangles > budget {tri_limit}")
    if figure and prim_total > budget["primitives"]:
        problems.append(f"{prim_total} primitives > budget {budget['primitives']}")
    if len(doc.get("materials", [])) > budget["materials"]:
        problems.append(f"{len(doc['materials'])} materials > budget {budget['materials']}")

    # 7 — images and samplers
    for i, img in enumerate(doc.get("images", [])):
        if img.get("mimeType") != "image/png" or "bufferView" not in img:
            problems.append(f"image {i} must be an embedded PNG")
            continue
        try:
            w, h = gt.png_size(gt.image_bytes(glb, i))
        except ValueError as exc:
            problems.append(f"image {i}: {exc}")
            continue
        if w != h or w & (w - 1) or w > limits["max_texture"]:
            problems.append(
                f"image {i} is {w}x{h}; square power-of-two up to {limits['max_texture']} only"
            )
    for i, sampler in enumerate(doc.get("samplers", [])):
        if sampler.get("magFilter") != NEAREST or sampler.get("minFilter") != NEAREST:
            problems.append(f"sampler {i} is not NEAREST/NEAREST (pixel sheets must not blur)")
    if doc.get("textures") and not doc.get("samplers"):
        problems.append("textures without an explicit NEAREST sampler")
    for i, mat in enumerate(doc.get("materials", [])):
        if mat.get("alphaMode", "OPAQUE") not in ("OPAQUE", "MASK"):
            problems.append(
                f"material {i} uses alphaMode {mat.get('alphaMode')}; OPAQUE or MASK only"
            )

    # 8 — palette strip
    sheet = contract["sheet"]
    if doc.get("images") and not extras.get("paletteLocked"):
        try:
            w, h, channels, rows = gt.decode_png(gt.image_bytes(glb, 0))
        except ValueError as exc:
            problems.append(f"sheet: {exc}")
        else:
            if w == sheet["size"] and h == sheet["size"]:
                cw, ch = sheet["cell_width"], sheet["cell_height"]
                for cell in range(len(sheet["cells"])):
                    x0 = cell * cw
                    first = rows[0][x0 * channels : x0 * channels + 3]
                    flat = all(
                        rows[y][x * channels : x * channels + 3] == first
                        for y in range(ch)
                        for x in range(x0, x0 + cw)
                    )
                    if not flat:
                        problems.append(
                            f"palette cell {cell} ({sheet['cells'][cell]}) is not one flat colour"
                        )
                        break
            else:
                problems.append(
                    f"sheet is {w}x{h}; the palette strip layout needs "
                    f"{sheet['size']}x{sheet['size']}"
                )

    # 9–12 — clips
    anims = doc.get("animations", [])
    by_name = {a.get("name"): a for a in anims}
    if part and anims:
        problems.append(f"part carries {len(anims)} animations; parts have none")
    if figure:
        for clip, spec in archetype["clips"].items():
            anim = by_name.get(clip)
            if anim is None:
                problems.append(f"clip {clip!r} missing")
                continue
            duration = gt.clip_duration(glb, anim)
            lo_d, hi_d = spec["duration"]
            if not lo_d <= duration <= hi_d:
                problems.append(f"clip {clip!r} lasts {duration:.3f}s; band is {lo_d}-{hi_d}s")
            for ch in anim["channels"]:
                target = ch["target"]
                if "node" not in target:
                    continue
                node_name = nodes[target["node"]].get("name")
                sampler = anim["samplers"][ch["sampler"]]
                values = gt.accessor_values(glb, sampler["output"])
                if target["path"] == "scale":
                    problems.append(f"clip {clip!r} animates scale on {node_name!r}")
                if target["path"] == "translation" and node_name == "root" and values:
                    xs = [v[0] for v in values]
                    zs = [v[2] for v in values]
                    if max(xs) - min(xs) > 1e-3 or max(zs) - min(zs) > 1e-3:
                        problems.append(
                            f"clip {clip!r} moves root in XZ (root motion); clips are in place"
                        )
                if spec["loop"] and len(values) > 1:
                    first, last = values[0], values[-1]
                    if target["path"] == "rotation":
                        # Same orientation within loop_epsilon_deg (q and -q are one rotation).
                        dot = abs(sum(a * b for a, b in zip(first, last, strict=True)))
                        same = min(dot, 1.0) >= math.cos(
                            math.radians(limits["loop_epsilon_deg"]) / 2
                        )
                    else:
                        same = all(
                            abs(a - b) <= limits["loop_epsilon"]
                            for a, b in zip(first, last, strict=True)
                        )
                    if not same:
                        problems.append(
                            f"clip {clip!r} does not loop cleanly on {node_name!r}/{target['path']}"
                        )
                        break
            if spec.get("stride"):
                recorded = float(figure.get("clips", {}).get(clip, {}).get("stride_m", 0) or 0)
                measured = gt.measure_stride(glb, anim, archetype["feet"])
                if recorded <= 0:
                    problems.append(f"clip {clip!r} has no stride_m in extras")
                elif abs(measured - recorded) > 0.1 * recorded:
                    problems.append(f"clip {clip!r} stride_m {recorded} != measured {measured:.4f}")
        for name in by_name:
            if name not in archetype["clips"]:
                problems.append(f"clip {name!r} is not in the {archetype_name} clip set")

    # 13 — parts
    if part:
        slot = part.get("slot")
        if not slot:
            problems.append("part without a slot")

    # 14 — packaging
    size_kb = path.stat().st_size / 1024
    cap = limits["part_file_kb"] if part else limits["base_file_kb"]
    if size_kb > cap:
        problems.append(f"{size_kb:.0f} KB > {cap} KB")
    used = set(doc.get("extensionsUsed", [])) | set(doc.get("extensionsRequired", []))
    if used & FORBIDDEN_EXTENSIONS:
        problems.append(f"needs a decoder we do not ship: {sorted(used & FORBIDDEN_EXTENSIONS)}")

    # 15 — provenance
    if require_ledger:
        ledger = sources_md if sources_md is not None else path.parent / "SOURCES.md"
        if not ledger.exists() or f"`{path.name}`" not in ledger.read_text(encoding="utf-8"):
            problems.append(f"no row for `{path.name}` in {ledger.name}")

    return problems


def check_dir(directory: Path = _FIGURES) -> dict[str, list[str]]:
    contract = load_contract()
    return {
        p.relative_to(directory).as_posix(): check_file(p, contract, directory / "SOURCES.md")
        for p in sorted(directory.rglob("*.glb"))
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "paths", nargs="*", help="GLB files or directories (default: the shipped figures)"
    )
    args = parser.parse_args(argv)
    targets = [Path(p) for p in args.paths] or [_FIGURES]
    results: dict[str, list[str]] = {}
    for target in targets:
        if target.is_dir():
            results.update(check_dir(target))
        elif target.exists():
            results[target.name] = check_file(target)
    if not results:
        print("check_society_figures: no figure assets to check (skipped)")
        return EXIT_SKIPPED
    failed = {k: v for k, v in results.items() if v}
    for name, problems in failed.items():
        print(f"{name}:")
        for p in problems:
            print(f"  - {p}")
    if failed:
        print(
            f"\ncheck_society_figures: {len(failed)} of {len(results)} figure(s) "
            "violate the contract"
        )
        return 1
    print(f"check_society_figures: OK - {len(results)} figure(s) honour the contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
