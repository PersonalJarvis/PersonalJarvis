"""Build every shipped figure GLB from its recorded source — inside Blender, headless.

    blender -b --python scripts/figures/build_figures.py -- --out <dir> [--target biped-medium]

The ONLY producer of the GLBs under ``src/assets/society/figures/``
(docs/agent-society/character-pipeline.md §7). What it does per target in
``contract.json``:

1. fetch the source file named in ``sources/<source>.json`` into ``cache/``
   (gitignored) and verify its sha256 — a build never touches an unverified byte;
2. import it, keep only the base body meshes, drop IK/control bones, rename the
   deform bones to the archetype's contract names;
3. keep only the clips the archetype needs, rename them, zero the root's XZ
   motion (locomotion is the runtime's job), close the loops;
4. join the body into one mesh, re-UV every face onto the 16-cell palette strip
   of a fresh 128×128 sheet (nearest-filtered), one material;
5. add the ``FWD`` marker, export a binary glTF;
6. finish it with stdlib tools: strip constant channels, prune, write the
   ``asset.extras.jarvis_figure`` block with the measured height, feet and stride;
7. run the CI gate on the result — a failing figure fails the build.

Runs on Windows, macOS and Linux (Blender needs no display in ``-b`` mode). It
never imports ``jarvis.*``: Blender ships its own interpreter.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import urllib.request
from pathlib import Path

import bpy  # type: ignore[import-not-found]

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CACHE = HERE / "cache"
sys.path.insert(0, str(HERE))
import glb_tools as gt  # noqa: E402

CONTRACT = json.loads((HERE / "contract.json").read_text(encoding="utf-8"))


def log(msg: str) -> None:
    print(f"[figures] {msg}", flush=True)


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


def load_source(name: str) -> dict:
    return json.loads((HERE / "sources" / f"{name}.json").read_text(encoding="utf-8"))


def fetch_source_file(source: dict, filename: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    expected = source["files"][filename]
    dest = CACHE / f"{source_slug(source)}-{filename}"
    if not dest.exists():
        url = source["base_url"] + filename
        log(f"fetching {url}")
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - pinned URL + sha256
            dest.write_bytes(resp.read())
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if digest != expected:
        raise SystemExit(f"{dest.name}: sha256 {digest} != recorded {expected}; refusing to build")
    return dest


def source_slug(source: dict) -> str:
    return re.sub(r"[^a-z0-9]+", "-", source["name"].lower()).strip("-")


# ---------------------------------------------------------------------------
# Blender helpers
# ---------------------------------------------------------------------------


def reset_scene() -> None:
    # The scene keeps the source's frame rate: keys imported onto integer frames
    # are sampled exactly on export, so a closed loop stays closed.
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_glb(path: Path, fps: int) -> None:
    # The importer lands keys on frames at the SCENE rate; at the source's own
    # rate they sit on integer frames, so the export samples every key exactly
    # and a loop closed on the last key stays closed after sampling.
    bpy.context.scene.render.fps = fps
    bpy.ops.import_scene.gltf(filepath=str(path), import_shading="NORMALS")


def find_armature() -> bpy.types.Object:
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if len(arms) != 1:
        raise SystemExit(f"expected exactly one armature, found {[a.name for a in arms]}")
    return arms[0]


def iter_fcurves(action: bpy.types.Action):
    """Every F-curve of an action across the legacy and the slotted API."""
    seen = False
    try:
        for fc in action.fcurves:
            seen = True
            yield fc
    except (AttributeError, RuntimeError):
        seen = False
    if seen:
        return
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                yield from bag.fcurves


def remove_fcurve(action: bpy.types.Action, fc) -> None:
    try:
        action.fcurves.remove(fc)
        return
    except (AttributeError, RuntimeError):
        pass
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                if fc in list(bag.fcurves):
                    bag.fcurves.remove(fc)
                    return


def set_mode(obj: bpy.types.Object, mode: str) -> None:
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode=mode)


def bone_name_of(data_path: str) -> str | None:
    m = re.match(r'pose\.bones\["([^"]+)"\]', data_path)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# build steps
# ---------------------------------------------------------------------------


def keep_only_body(arm: bpy.types.Object, body_meshes: list[str]) -> list[bpy.types.Object]:
    keep = set(body_meshes)
    bodies = []
    for obj in list(bpy.data.objects):
        if obj is arm:
            continue
        if obj.type == "MESH" and obj.name in keep:
            bodies.append(obj)
            continue
        bpy.data.objects.remove(obj, do_unlink=True)
    missing = keep - {b.name for b in bodies}
    if missing:
        raise SystemExit(f"body meshes not found in source: {sorted(missing)}")
    return bodies


def drop_bones(arm: bpy.types.Object, patterns: list[str]) -> list[str]:
    victims = [b.name for b in arm.data.bones if any(p in b.name for p in patterns)]
    set_mode(arm, "EDIT")
    for name in victims:
        eb = arm.data.edit_bones.get(name)
        if eb is not None:
            arm.data.edit_bones.remove(eb)
    bpy.ops.object.mode_set(mode="OBJECT")
    for action in bpy.data.actions:
        for fc in list(iter_fcurves(action)):
            bone = bone_name_of(fc.data_path)
            if bone in victims:
                remove_fcurve(action, fc)
    return victims


def rename_bones(arm: bpy.types.Object, bone_map: dict[str, str], expected: list[str]) -> None:
    for old, new in bone_map.items():
        bone = arm.data.bones.get(old)
        if bone is None:
            raise SystemExit(f"bone {old!r} missing in source armature")
        bone.name = new
    have = {b.name for b in arm.data.bones}
    extra = have - set(expected)
    missing = set(expected) - have
    if extra or missing:
        raise SystemExit(f"bone set mismatch: extra={sorted(extra)} missing={sorted(missing)}")


def filter_clips(clip_map: dict[str, str], clip_spec: dict) -> dict[str, bpy.types.Action]:
    by_source = {v: k for k, v in clip_map.items()}
    kept: dict[str, bpy.types.Action] = {}
    for action in list(bpy.data.actions):
        target = by_source.get(action.name)
        if target is None:
            bpy.data.actions.remove(action)
            continue
        action.name = target
        action.use_fake_user = True
        kept[target] = action
    missing = set(clip_spec) - set(kept)
    if missing:
        raise SystemExit(f"clips missing in source: {sorted(missing)} (have {sorted(kept)})")
    return kept


def normalize_clip(action: bpy.types.Action, loop: bool) -> None:
    for fc in iter_fcurves(action):
        bone = bone_name_of(fc.data_path)
        keys = fc.keyframe_points
        if not len(keys):
            continue
        if bone == "root" and fc.data_path.endswith(".location") and fc.array_index in (0, 2):
            for k in keys:
                k.co[1] = 0.0
                k.handle_left[1] = 0.0
                k.handle_right[1] = 0.0
        if loop and len(keys) > 1:
            keys[-1].co[1] = keys[0].co[1]
        fc.update()


def join_bodies(bodies: list[bpy.types.Object]) -> bpy.types.Object:
    for obj in bpy.data.objects:
        obj.select_set(False)
    for obj in bodies:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = bodies[0]
    if len(bodies) > 1:
        bpy.ops.object.join()
    body = bpy.context.view_layer.objects.active
    body.name = "Body"
    body.data.name = "Body"
    return body


def cell_of(u: float, v_bottom_up: float, grid: dict) -> str:
    col = min(int(u * grid["columns"]), grid["columns"] - 1)
    row = min(int((1.0 - v_bottom_up) * grid["rows"]), grid["rows"] - 1)
    return f"c{col}r{row}"


def reuv_to_palette(
    body: bpy.types.Object, source_names: dict[str, str], cell_map: dict, grid: dict
) -> dict:
    """Move every face's UVs onto its semantic palette cell; report the mapping used."""
    sheet = CONTRACT["sheet"]
    cells = sheet["cells"]
    mesh = body.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        raise SystemExit("body has no UV layer")
    # A joined mesh keeps no per-object identity; material index / original
    # object is gone, so per-mesh overrides are resolved by the polygon's
    # original object recorded before the join (see build_target).
    origin = mesh.attributes.get("jarvis_origin")
    used: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    for poly in mesh.polygons:
        us = [uv_layer.data[li].uv[0] for li in poly.loop_indices]
        vs = [uv_layer.data[li].uv[1] for li in poly.loop_indices]
        cell = cell_of(sum(us) / len(us), sum(vs) / len(vs), grid)
        obj_name = source_names.get(str(origin.data[poly.index].value), "*") if origin else "*"
        semantic = cell_map.get(obj_name, {}).get(cell) or cell_map.get("*", {}).get(cell)
        if semantic is None:
            unmapped[cell] = unmapped.get(cell, 0) + 1
            semantic = "primary"
        idx = cells.index(semantic)
        used[semantic] = used.get(semantic, 0) + 1
        cu = (idx + 0.5) / len(cells)
        cv = 1.0 - (sheet["cell_height"] / 2) / sheet["size"]
        for li in poly.loop_indices:
            uv_layer.data[li].uv = (cu, cv)
    if unmapped:
        log(f"WARNING unmapped atlas cells (painted primary): {unmapped}")
    return {"used": used, "unmapped": unmapped}


def hex_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def write_sheet(path: Path, palette: dict[str, str]) -> None:
    sheet = CONTRACT["sheet"]
    size = sheet["size"]
    cw, ch = sheet["cell_width"], sheet["cell_height"]
    rows: list[bytes] = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            if y < ch:
                name = sheet["cells"][min(x // cw, len(sheet["cells"]) - 1)]
                r, g, b = hex_rgb(palette[name])
            else:
                r, g, b = (128, 128, 128)
            row += bytes((r, g, b, 255))
        rows.append(bytes(row))
    path.write_bytes(gt.encode_png(size, size, rows, 4))


def apply_sheet_material(body: bpy.types.Object, sheet_path: Path, name: str) -> None:
    img = bpy.data.images.load(str(sheet_path))
    img.colorspace_settings.name = "sRGB"
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Closest"
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Metallic"].default_value = 0.0
    body.data.materials.clear()
    body.data.materials.append(mat)


def add_forward_marker(arm: bpy.types.Object) -> None:
    fwd = bpy.data.objects.new("FWD", None)
    fwd.empty_display_type = "ARROWS"
    fwd.empty_display_size = 0.2
    # glTF (0, 0.5, 1) == Blender (0, -1, 0.5): the exporter maps Blender -Y to glTF +Z.
    fwd.location = (0.0, -1.0, 0.5)
    fwd.parent = arm
    bpy.context.scene.collection.objects.link(fwd)


def export_glb(path: Path) -> None:
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        export_apply=True,
        export_yup=True,
        export_extras=True,
        export_texcoords=True,
        export_normals=True,
        export_tangents=False,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_skins=True,
        export_def_bones=False,
        export_rest_position_armature=True,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_force_sampling=True,
        export_frame_step=1,
        export_optimize_animation_size=True,
        export_leaf_bone=False,
        use_selection=False,
    )


# ---------------------------------------------------------------------------
# finish (stdlib): extras, samplers, pruning
# ---------------------------------------------------------------------------


def finish(glb_path: Path, target: dict, source: dict, archetype: dict, uv_report: dict) -> None:
    glb = gt.read_glb(glb_path)
    doc = glb.doc
    for sampler in doc.get("samplers", []):
        sampler["magFilter"] = 9728
        sampler["minFilter"] = 9728
        sampler["wrapS"] = 33071
        sampler["wrapT"] = 33071
    if not doc.get("samplers") and doc.get("textures"):
        doc["samplers"] = [{"magFilter": 9728, "minFilter": 9728, "wrapS": 33071, "wrapT": 33071}]
        for tex in doc["textures"]:
            tex["sampler"] = 0
    for mat in doc.get("materials", []):
        mat["alphaMode"] = "OPAQUE"
        mat.pop("doubleSided", None)

    # Strip constant scale channels (identity) — KayKit bakes one per bone.
    for anim in doc.get("animations", []):
        keep_channels = []
        for ch in anim["channels"]:
            if ch["target"].get("path") == "scale":
                vals = gt.accessor_values(glb, anim["samplers"][ch["sampler"]]["output"])
                if all(abs(v - 1.0) < 1e-3 for vec in vals for v in vec):
                    continue
            keep_channels.append(ch)
        anim["channels"] = keep_channels
        used = sorted({ch["sampler"] for ch in keep_channels})
        remap = {old: new for new, old in enumerate(used)}
        anim["samplers"] = [anim["samplers"][i] for i in used]
        for ch in keep_channels:
            ch["sampler"] = remap[ch["sampler"]]

    collapsed = gt.collapse_constant_channels(glb)
    glb = gt.prune_unused(glb)
    doc = glb.doc
    log(f"collapsed {collapsed} constant channels")

    # Facts the runtime and the gate read.
    skinned = gt.skinned_mesh_nodes(doc)
    lo, hi = gt.mesh_bounds(doc, doc["meshes"][doc["nodes"][skinned[0]]["mesh"]])
    clips: dict[str, dict] = {}
    for name, spec in archetype["clips"].items():
        anim = gt.clip_by_name(doc, name)
        if anim is None:
            continue
        entry: dict = {
            "duration": round(gt.clip_duration(glb, anim), 4),
            "loop": bool(spec["loop"]),
        }
        if spec.get("stride"):
            entry["stride_m"] = round(gt.measure_stride(glb, anim, archetype["feet"]), 4)
        clips[name] = entry
    doc.setdefault("asset", {})["extras"] = {
        "jarvis_figure": {
            "contract": CONTRACT["contract"],
            "archetype": target["archetype"],
            "variant": target["variant"],
            "forward": "+Z",
            "height_m": round(hi[1] - lo[1], 4),
            "target_height_m": archetype["variants"][target["variant"]]["height_m"],
            "clips": clips,
            "palette_cells": uv_report["used"],
            "source": f"{source['name']} ({source['license']}) / {target['character']}",
        }
    }
    doc["asset"]["generator"] = "Personal Jarvis build_figures.py"
    gt.write_glb(glb_path, doc, glb.blob)
    log(f"finished {glb_path.name}: {glb_path.stat().st_size // 1024} KB, clips {clips}")


def run_gate(glb_path: Path) -> None:
    gate = REPO / "scripts" / "ci" / "check_society_figures.py"
    if not gate.exists():
        log("gate not present yet — skipping validation")
        return
    spec = importlib.util.spec_from_file_location("check_society_figures", gate)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["check_society_figures"] = mod
    spec.loader.exec_module(mod)
    problems = mod.check_file(glb_path)
    if problems:
        for p in problems:
            log(f"GATE {glb_path.name}: {p}")
        raise SystemExit(f"{glb_path.name} violates the figure contract")
    log(f"gate OK for {glb_path.name}")


# ---------------------------------------------------------------------------
# one target
# ---------------------------------------------------------------------------


def build_target(target: dict, out_dir: Path) -> Path:
    archetype = CONTRACT["archetypes"][target["archetype"]]
    source = load_source(target["source"])
    character = source["characters"][target["character"]]
    src_file = fetch_source_file(source, f"{target['character']}.glb")
    log(f"building {target['id']} from {src_file.name}")

    reset_scene()
    import_glb(src_file, int(source.get("fps", 30)))
    arm = find_armature()
    bodies = keep_only_body(arm, character["body_meshes"])

    # Remember which source object each polygon came from (per-mesh cell overrides).
    source_names: dict[str, str] = {}
    for n, obj in enumerate(bodies):
        attr = obj.data.attributes.get("jarvis_origin") or obj.data.attributes.new(
            "jarvis_origin", "INT", "FACE"
        )
        for poly in obj.data.polygons:
            attr.data[poly.index].value = n
        source_names[str(n)] = obj.name

    dropped = drop_bones(arm, source["drop_bone_patterns"])
    log(f"dropped {len(dropped)} control bones")
    rename_bones(arm, source["bone_map"], archetype["bones"])
    kept = filter_clips(source["clip_map"], archetype["clips"])
    for name, action in kept.items():
        normalize_clip(action, archetype["clips"][name]["loop"])
    log(f"clips: {sorted(kept)}")

    body = join_bodies(bodies)
    uv_report = reuv_to_palette(body, source_names, character["cell_map"], source["atlas_grid"])
    body.data.attributes.remove(body.data.attributes["jarvis_origin"])
    sheet_path = CACHE / f"{target['id']}-sheet.png"
    write_sheet(sheet_path, character["default_palette"])
    apply_sheet_material(body, sheet_path, f"{target['id']}-sheet")
    add_forward_marker(arm)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{target['id']}.glb"
    export_glb(out)
    finish(out, target, source, archetype, uv_report)
    run_gate(out)
    return out


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default=str(REPO / "jarvis/ui/web/frontend/src/assets/society/figures")
    )
    parser.add_argument("--target", default=None)
    args = parser.parse_args(argv)
    targets = [t for t in CONTRACT["targets"] if args.target in (None, t["id"])]
    if not targets:
        raise SystemExit(f"no target named {args.target!r}")
    for target in targets:
        build_target(target, Path(args.out))


if __name__ == "__main__":
    main()
