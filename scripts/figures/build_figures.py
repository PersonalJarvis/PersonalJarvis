"""Build every shipped figure GLB — bases and parts — from its recorded source, inside Blender.

    blender -b --python scripts/figures/build_figures.py -- [--out <dir>] [--target biped-rogue]

The ONLY producer of the GLBs under ``src/assets/society/figures/`` and of the
``catalog.json`` the creator reads (docs/agent-society/character-pipeline.md §7).
Per target in ``contract.json``:

1. fetch the source file named in ``sources/<source>.json`` into ``cache/``
   (gitignored) and verify its sha256 — a build never touches an unverified byte;
2. import it, keep the base body meshes and the listed accessories, drop
   IK/control bones, rename the deform bones to the archetype's contract names;
3. keep only the clips the archetype needs, rename them, zero the root's XZ
   motion (locomotion is the runtime's job), close the loops;
4. join the body into one mesh, re-UV every face onto the 16-cell palette strip
   of a fresh 128×128 sheet (nearest-filtered); hair faces get their own
   material slot so headgear can hide them;
5. add the ``FWD`` marker, export the base as a binary glTF;
6. turn every accessory into a part: unparent it at rest, skin it to its
   attach bone with weight 1, re-UV, export it without clips;
7. finish each file with stdlib tools (strip constant channels, prune, write
   ``asset.extras``), run the CI gate on it, and regenerate ``catalog.json`` and
   ``SOURCES.md`` from what was built.

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
DEFAULT_OUT = REPO / "jarvis/ui/web/frontend/src/assets/society/figures"
CATALOG_PATH = REPO / "jarvis/ui/web/frontend/src/components/society/figures/catalog.json"


def log(msg: str) -> None:
    print(f"[figures] {msg}", flush=True)


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


def load_source(name: str) -> dict:
    return json.loads((HERE / "sources" / f"{name}.json").read_text(encoding="utf-8"))


def source_slug(source: dict) -> str:
    return re.sub(r"[^a-z0-9]+", "-", source["name"].lower()).strip("-")


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


# ---------------------------------------------------------------------------
# Blender helpers
# ---------------------------------------------------------------------------


def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_glb(path: Path, fps: int) -> None:
    # Keys land on frames at the SCENE rate; at the source's own rate they sit
    # on integer frames, so the export samples every key exactly and a loop
    # closed on the last key stays closed after sampling.
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


def select_only(objects: list[bpy.types.Object], active: bpy.types.Object) -> None:
    for obj in bpy.data.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def set_mode(obj: bpy.types.Object, mode: str) -> None:
    select_only([obj], obj)
    bpy.ops.object.mode_set(mode=mode)


def bone_name_of(data_path: str) -> str | None:
    m = re.match(r'pose\.bones\["([^"]+)"\]', data_path)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# normalization steps
# ---------------------------------------------------------------------------


def keep_objects(arm: bpy.types.Object, names: set[str]) -> dict[str, bpy.types.Object]:
    kept: dict[str, bpy.types.Object] = {}
    for obj in list(bpy.data.objects):
        if obj is arm:
            continue
        if obj.type == "MESH" and obj.name in names:
            kept[obj.name] = obj
            continue
        bpy.data.objects.remove(obj, do_unlink=True)
    missing = names - set(kept)
    if missing:
        raise SystemExit(f"objects not found in source: {sorted(missing)}")
    return kept


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
            if bone_name_of(fc.data_path) in victims:
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


def tag_origin(objects: list[bpy.types.Object]) -> dict[str, str]:
    """Remember which source object each polygon came from (per-mesh cell overrides)."""
    names: dict[str, str] = {}
    for n, obj in enumerate(objects):
        attr = obj.data.attributes.get("jarvis_origin") or obj.data.attributes.new(
            "jarvis_origin", "INT", "FACE"
        )
        for poly in obj.data.polygons:
            attr.data[poly.index].value = n
        names[str(n)] = obj.name
    return names


def join_bodies(bodies: list[bpy.types.Object]) -> bpy.types.Object:
    select_only(bodies, bodies[0])
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
    obj: bpy.types.Object,
    source_names: dict[str, str],
    cell_map: dict,
    grid: dict,
    fallback: dict | None = None,
) -> dict:
    """Move every face's UVs onto its semantic palette cell; tag hair faces; report."""
    sheet = CONTRACT["sheet"]
    cells = sheet["cells"]
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        raise SystemExit(f"{obj.name} has no UV layer")
    origin = mesh.attributes.get("jarvis_origin")
    used: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    hair_faces: list[int] = []
    for poly in mesh.polygons:
        us = [uv_layer.data[li].uv[0] for li in poly.loop_indices]
        vs = [uv_layer.data[li].uv[1] for li in poly.loop_indices]
        cell = cell_of(sum(us) / len(us), sum(vs) / len(vs), grid)
        obj_name = source_names.get(str(origin.data[poly.index].value), "*") if origin else "*"
        semantic = (
            cell_map.get(obj_name, {}).get(cell)
            or cell_map.get("*", {}).get(cell)
            or (fallback or {}).get(cell)
        )
        if semantic is None:
            unmapped[cell] = unmapped.get(cell, 0) + 1
            semantic = "primary"
        idx = cells.index(semantic)
        used[semantic] = used.get(semantic, 0) + 1
        if semantic == "hair":
            hair_faces.append(poly.index)
        cu = (idx + 0.5) / len(cells)
        cv = 1.0 - (sheet["cell_height"] / 2) / sheet["size"]
        for li in poly.loop_indices:
            uv_layer.data[li].uv = (cu, cv)
    if unmapped:
        log(f"WARNING {obj.name}: unmapped atlas cells (painted primary): {unmapped}")
    if origin:
        mesh.attributes.remove(origin)
    return {"used": used, "unmapped": unmapped, "hair_faces": hair_faces}


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


def sheet_material(name: str, img: bpy.types.Image) -> bpy.types.Material:
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
    return mat


def load_sheet(sheet_path: Path) -> bpy.types.Image:
    img = bpy.data.images.load(str(sheet_path))
    img.colorspace_settings.name = "sRGB"
    return img


def apply_materials(
    obj: bpy.types.Object, img: bpy.types.Image, stem: str, hair_faces: list[int]
) -> None:
    obj.data.materials.clear()
    obj.data.materials.append(sheet_material(f"{stem}-sheet", img))
    if hair_faces:
        # A second slot, same sheet: exports as its own primitive so headgear can hide it.
        obj.data.materials.append(sheet_material(f"{stem}-hair", img))
        for index in hair_faces:
            obj.data.polygons[index].material_index = 1


def add_forward_marker(arm: bpy.types.Object) -> bpy.types.Object:
    fwd = bpy.data.objects.new("FWD", None)
    fwd.empty_display_type = "ARROWS"
    fwd.empty_display_size = 0.2
    # glTF (0, 0.5, 1) == Blender (0, -1, 0.5): the exporter maps Blender -Y to glTF +Z.
    fwd.location = (0.0, -1.0, 0.5)
    fwd.parent = arm
    bpy.context.scene.collection.objects.link(fwd)
    return fwd


def export_glb(path: Path, selection: list[bpy.types.Object], animations: bool) -> None:
    select_only(selection, selection[0])
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
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
        export_animations=animations,
        export_animation_mode="ACTIONS",
        export_force_sampling=True,
        export_frame_step=1,
        export_optimize_animation_size=True,
        export_leaf_bone=False,
    )


def skin_part_to_bone(part: bpy.types.Object, arm: bpy.types.Object, bone: str) -> None:
    """Unparent the accessory at rest and bind every vertex to its attach bone."""
    arm.data.pose_position = "REST"
    bpy.context.view_layer.update()
    with bpy.context.temp_override(
        active_object=part, object=part, selected_editable_objects=[part], selected_objects=[part]
    ):
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    select_only([part], part)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for group in list(part.vertex_groups):
        part.vertex_groups.remove(group)
    vg = part.vertex_groups.new(name=bone)
    vg.add([v.index for v in part.data.vertices], 1.0, "REPLACE")
    for mod in list(part.modifiers):
        part.modifiers.remove(mod)
    mod = part.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    part.parent = arm
    part.matrix_parent_inverse.identity()


# ---------------------------------------------------------------------------
# finish (stdlib): extras, samplers, pruning
# ---------------------------------------------------------------------------


def finish_common(glb: gt.Glb) -> gt.Glb:
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
    gt.collapse_constant_channels(glb)
    return gt.prune_unused(glb)


def finish_base(
    glb_path: Path, target: dict, source: dict, archetype: dict, uv_report: dict
) -> dict:
    glb = finish_common(gt.read_glb(glb_path))
    doc = glb.doc
    skinned = gt.skinned_mesh_nodes(doc)
    lo = [1e9] * 3
    hi = [-1e9] * 3
    tris = 0
    for n in skinned:
        mesh = doc["meshes"][doc["nodes"][n]["mesh"]]
        mlo, mhi = gt.mesh_bounds(doc, mesh)
        lo = [min(a, b) for a, b in zip(lo, mlo, strict=True)]
        hi = [max(a, b) for a, b in zip(hi, mhi, strict=True)]
        tris += gt.triangle_count(doc, mesh)
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
    extras = {
        "contract": CONTRACT["contract"],
        "archetype": target["archetype"],
        "variant": target["variant"],
        "base": target["base"],
        "forward": "+Z",
        "height_m": round(hi[1] - lo[1], 4),
        "target_height_m": archetype["variants"][target["variant"]]["height_m"],
        "clips": clips,
        "palette_cells": uv_report["used"],
        "hair_primitive": bool(uv_report["hair_faces"]),
        "source": f"{source['name']} ({source['license']}) / {target['character']}",
    }
    doc.setdefault("asset", {})["extras"] = {"jarvis_figure": extras}
    doc["asset"]["generator"] = "Personal Jarvis build_figures.py"
    gt.write_glb(glb_path, doc, glb.blob)
    log(
        f"finished {glb_path.name}: {glb_path.stat().st_size // 1024} KB, {tris} tris, clips {sorted(clips)}"
    )
    return {"triangles": tris, "clips": sorted(clips), "height_m": extras["height_m"]}


def finish_part(glb_path: Path, spec: dict, target: dict, source: dict, attach: str) -> dict:
    glb = finish_common(gt.read_glb(glb_path))
    doc = glb.doc
    doc["animations"] = []
    glb = gt.prune_unused(glb)
    doc = glb.doc
    tris = sum(
        gt.triangle_count(doc, doc["meshes"][doc["nodes"][n]["mesh"]])
        for n in gt.skinned_mesh_nodes(doc)
    )
    extras = {
        "contract": CONTRACT["contract"],
        "archetype": target["archetype"],
        "slot": spec["slot"],
        "attach": attach,
        "hides": spec.get("hides", []),
        "label": spec["label"],
        "styles": spec.get("styles", []),
        "source": f"{source['name']} ({source['license']}) / {spec['object']}",
    }
    doc.setdefault("asset", {})["extras"] = {"jarvis_part": extras}
    doc["asset"]["generator"] = "Personal Jarvis build_figures.py"
    gt.write_glb(glb_path, doc, glb.blob)
    log(f"finished part {glb_path.name}: {glb_path.stat().st_size // 1024} KB, {tris} tris")
    return {"triangles": tris}


_GATE = None


def run_gate(glb_path: Path, sources_md: Path) -> None:
    global _GATE
    gate = REPO / "scripts" / "ci" / "check_society_figures.py"
    if not gate.exists():
        log("gate not present yet — skipping validation")
        return
    if _GATE is None:
        spec = importlib.util.spec_from_file_location("check_society_figures", gate)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules["check_society_figures"] = mod
        spec.loader.exec_module(mod)
        _GATE = mod
    problems = _GATE.check_file(glb_path, sources_md=sources_md)
    if problems:
        for p in problems:
            log(f"GATE {glb_path.name}: {p}")
        raise SystemExit(f"{glb_path.name} violates the figure contract")
    log(f"gate OK for {glb_path.name}")


# ---------------------------------------------------------------------------
# one target = one base + its parts
# ---------------------------------------------------------------------------


def build_target(target: dict, out_dir: Path, ledger: Path) -> tuple[dict, list[dict]]:
    archetype = CONTRACT["archetypes"][target["archetype"]]
    source = load_source(target["source"])
    character = source["characters"][target["character"]]
    part_specs = character.get("parts", [])
    src_file = fetch_source_file(source, f"{target['character']}.glb")
    log(f"building {target['id']} from {src_file.name}")

    reset_scene()
    import_glb(src_file, int(source.get("fps", 30)))
    arm = find_armature()
    wanted = set(character["body_meshes"]) | {p["object"] for p in part_specs}
    objects = keep_objects(arm, wanted)
    bodies = [objects[n] for n in character["body_meshes"]]
    source_names = tag_origin(bodies)

    dropped = drop_bones(arm, source["drop_bone_patterns"])
    log(f"dropped {len(dropped)} control bones")
    rename_bones(arm, source["bone_map"], archetype["bones"])
    kept = filter_clips(source["clip_map"], archetype["clips"])
    for name, action in kept.items():
        normalize_clip(action, archetype["clips"][name]["loop"])

    sheet_path = CACHE / f"{target['id']}-sheet.png"
    write_sheet(sheet_path, character["default_palette"])
    img = load_sheet(sheet_path)

    body = join_bodies(bodies)
    uv_report = reuv_to_palette(body, source_names, character["cell_map"], source["atlas_grid"])
    apply_materials(body, img, target["id"], uv_report["hair_faces"])
    fwd = add_forward_marker(arm)

    out_dir.mkdir(parents=True, exist_ok=True)
    base_path = out_dir / f"{target['id']}.glb"
    export_glb(base_path, [body, arm, fwd], animations=True)
    facts = finish_base(base_path, target, source, archetype, uv_report)
    base_entry = {
        "id": f"{target['archetype']}/{target['base']}",
        "file": base_path.name,
        "archetype": target["archetype"],
        "variant": target["variant"],
        "base": target["base"],
        "label": target["label"],
        "styles": target.get("styles", []),
        "heightM": archetype["variants"][target["variant"]]["height_m"],
        "palette": character["default_palette"],
        "source": f"{source['name']} / {target['character']}",
        "license": source["license"],
        **facts,
    }

    # Parts: the clips are gone, the FWD stays out of the selection.
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    part_entries = []
    parts_dir = out_dir / "parts" / target["archetype"]
    parts_dir.mkdir(parents=True, exist_ok=True)
    for spec in part_specs:
        part = objects[spec["object"]]
        attach = archetype["slots"][spec["slot"]]["attach"]
        skin_part_to_bone(part, arm, attach)
        report = reuv_to_palette(
            part,
            {},
            {"*": character["cell_map"].get("*", {})},
            source["atlas_grid"],
            fallback=source.get("part_cell_map", {}),
        )
        apply_materials(part, img, spec["id"], [])
        part_path = parts_dir / f"{spec['id']}.glb"
        export_glb(part_path, [part, arm], animations=False)
        pfacts = finish_part(part_path, spec, target, source, attach)
        part_entries.append(
            {
                "id": spec["id"],
                "file": f"parts/{target['archetype']}/{part_path.name}",
                "archetype": target["archetype"],
                "slot": spec["slot"],
                "attach": attach,
                "label": spec["label"],
                "styles": spec.get("styles", []),
                "hides": spec.get("hides", []),
                "from_base": target["base"],
                "source": f"{source['name']} / {spec['object']}",
                "license": source["license"],
                "palette_cells": report["used"],
                **pfacts,
            }
        )
    return base_entry, part_entries


def write_ledger(
    path: Path, bases: list[dict], parts: list[dict], sources: dict[str, dict]
) -> None:
    lines = [
        "# Figure assets — provenance ledger",
        "",
        "GENERATED by `scripts/figures/build_figures.py` — one row per shipped file",
        "(docs/agent-society/character-pipeline.md §11, gate check 15). Every GLB here is",
        "produced from the recorded source; nothing is hand-exported. Sources are fetched by URL",
        "into `scripts/figures/cache/` and verified by sha256 before the build touches them",
        "(`scripts/figures/sources/*.json`).",
        "",
        "| File | Source | License | What the build changed |",
        "|---|---|---|---|",
    ]
    for b in bases:
        lines.append(
            f"| `{b['file']}` | {b['source']} | {b['license']} | body meshes joined; IK/control bones "
            f"dropped; 23 deform bones renamed; clips kept/renamed, root XZ zeroed, loops closed; "
            f"faces re-UV'd onto the palette strip; hair as its own primitive; FWD marker; extras |"
        )
    for p in parts:
        lines.append(
            f"| `{p['file'].split('/')[-1]}` | {p['source']} | {p['license']} | unparented at rest, "
            f"skinned to `{p['attach']}` with weight 1; re-UV'd onto the palette strip; no clips; extras |"
        )
    lines.append("")
    for name, src in sources.items():
        lines.append(
            f"- **{src['name']}** — {src['author']}, {src['license']} ({src['license_url']}); files:"
        )
        for fname, sha in src["files"].items():
            lines.append(f"  - `{fname}` sha256 `{sha}`")
    lines.append("")
    lines.append(
        "Licenses recorded at fetch time. CC0 needs no attribution; credit is given anyway."
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--target", default=None)
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    targets = [t for t in CONTRACT["targets"] if args.target in (None, t["id"])]
    if not targets:
        raise SystemExit(f"no target named {args.target!r}")

    catalog: dict = {"contract": CONTRACT["contract"], "styles": {}, "bases": [], "parts": []}
    if CATALOG_PATH.exists() and args.target:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog["styles"] = {k: v for k, v in CONTRACT["styles"].items() if not k.startswith("$")}

    ledger = out_dir / "SOURCES.md"
    built_bases: list[dict] = []
    built_parts: list[dict] = []
    for target in targets:
        base, parts = build_target(target, out_dir, ledger)
        built_bases.append(base)
        built_parts.extend(parts)

    # Merge into the catalog (a single --target rebuild keeps the other entries).
    bases = {b["id"]: b for b in catalog.get("bases", [])}
    for b in built_bases:
        bases[b["id"]] = b
    parts = {p["id"]: p for p in catalog.get("parts", [])}
    for p in built_parts:
        parts[p["id"]] = p
    catalog["bases"] = sorted(bases.values(), key=lambda b: b["id"])
    catalog["parts"] = sorted(parts.values(), key=lambda p: p["id"])
    sources = {t["source"]: load_source(t["source"]) for t in CONTRACT["targets"]}
    write_ledger(ledger, catalog["bases"], catalog["parts"], sources)
    CATALOG_PATH.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    log(f"catalog: {len(catalog['bases'])} bases, {len(catalog['parts'])} parts")

    # The gate runs last, against the regenerated ledger.
    for b in built_bases:
        run_gate(out_dir / b["file"], ledger)
    for p in built_parts:
        run_gate(out_dir / p["file"], ledger)


if __name__ == "__main__":
    main()
