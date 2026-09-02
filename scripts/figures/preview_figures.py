"""Render a contact sheet of built figures — the human proof before they ship.

    blender -b --python scripts/figures/preview_figures.py -- --out <dir> [--glb a.glb b.glb]
    blender -b --python scripts/figures/preview_figures.py -- --out <dir> --parts

Four views per figure (front, three-quarter, side, back), flat-lit, on a light
ground — the Figure Lab's job (character-pipeline.md §10.3) reduced to
something a build step can run without a display. With ``--parts`` it renders
every base wearing every part of its archetype, which is the only way to see a
combination that renders wrong from one side and right from the other.

Reads only what is shipped; writes only PNGs. Never imports ``jarvis.*``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FIGURES = REPO / "jarvis/ui/web/frontend/src/assets/society/figures"
CATALOG = REPO / "jarvis/ui/web/frontend/src/components/society/figures/catalog.json"

#: yaw in degrees; 0 looks at the figure's front (it faces glTF +Z, so −Z of the camera)
VIEWS = {"front": 0.0, "three-quarter": 35.0, "side": 90.0, "back": 180.0}
RESOLUTION = 384


def log(msg: str) -> None:
    print(f"[preview] {msg}", flush=True)


def reset() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = RESOLUTION
    scene.render.resolution_y = RESOLUTION
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("preview")
    scene.world.use_nodes = False
    scene.world.color = (0.90, 0.87, 0.81)
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "TEXTURE"
    shading.show_object_outline = False
    shading.show_specular_highlight = False


def import_glb(path: Path) -> list[bpy.types.Object]:
    """Import and return the real objects — the importer's own placeholders go.

    Blender draws an imported empty (our ``FWD`` marker) as an icosphere MESH
    beside the empty itself; left in, it lands in the render and doubles the
    measured bounds. Nothing like it exists in the file.
    """
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path), import_shading="FLAT")
    fresh = [o for o in bpy.data.objects if o not in before]
    for obj in list(fresh):
        if obj.type == "MESH" and not obj.data.materials:
            fresh.remove(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
    return fresh


def world_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for obj in objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            v = obj.matrix_world @ Vector(corner)
            lo = Vector(min(lo[i], v[i]) for i in range(3))
            hi = Vector(max(hi[i], v[i]) for i in range(3))
    if lo.x > hi.x:
        return Vector((0, 0, 0)), Vector((1, 1, 1))
    return lo, hi


def render_views(objects: list[bpy.types.Object], out: Path, label: str) -> None:
    lo, hi = world_bounds(objects)
    centre = (lo + hi) / 2
    span = max(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z, 0.1)
    distance = span * 1.75
    camera_data = bpy.data.cameras.new("cam")
    camera_data.lens = 55
    camera = bpy.data.objects.new("cam", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    out.mkdir(parents=True, exist_ok=True)
    for name, yaw in VIEWS.items():
        a = math.radians(yaw)
        # The imported glTF faces +Z in Blender-after-import terms (Y up →
        # Z up conversion): the front camera sits on −Y and looks back.
        camera.location = centre + Vector(
            (math.sin(a) * distance, -math.cos(a) * distance, span * 0.22)
        )
        direction = centre - camera.location
        camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        bpy.context.scene.render.filepath = str(out / f"{label}-{name}.png")
        bpy.ops.render.render(write_still=True)
    log(f"{label}: {len(VIEWS)} views")


def hide_material_slot(objects: list[bpy.types.Object], suffix: str) -> None:
    """Delete the faces of a material slot — what a part's `hides` list means.

    The runtime hides a whole primitive; Blender's importer merges the file's
    two primitives back into ONE object with two material slots, so hiding by
    object name silently does nothing and a helmet renders over visible hair.
    """
    for obj in objects:
        if obj.type != "MESH":
            continue
        slots = [i for i, m in enumerate(obj.data.materials) if m and m.name.endswith(suffix)]
        if not slots:
            continue
        mesh = obj.data
        doomed = [p for p in mesh.polygons if p.material_index in slots]
        if not doomed:
            continue
        verts = {v for p in doomed for v in p.vertices}
        for vertex in mesh.vertices:
            vertex.select = vertex.index in verts
        for poly in mesh.polygons:
            poly.select = poly.material_index in slots
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.delete(type="FACE")
        bpy.ops.object.mode_set(mode="OBJECT")


def rebind_to(worn: list[bpy.types.Object], rig: bpy.types.Object | None) -> list[bpy.types.Object]:
    """Bind an accessory to the BODY's skeleton and drop the one it shipped.

    Every part file carries a copy of the rig so it can be skinned at all. Left
    as its own armature, the part holds the REST pose while the body plays a
    clip — a hat floats a head above a bowed head, a staff lies across the
    shoulders — and the preview reports bugs that do not exist. The runtime
    binds a part's mesh to the body's skeleton (`assembleFigure`); so does this.
    """
    if rig is None:
        return worn
    kept: list[bpy.types.Object] = []
    for obj in worn:
        if obj.type == "ARMATURE":
            bpy.data.objects.remove(obj, do_unlink=True)
            continue
        if obj.type == "MESH":
            for modifier in obj.modifiers:
                if modifier.type == "ARMATURE":
                    modifier.object = rig
            obj.parent = rig
            obj.matrix_parent_inverse.identity()
        kept.append(obj)
    return kept


def pose_to_clip(objects: list[bpy.types.Object], clip: str) -> None:
    """Hold one frame of a clip, so a part is judged in the pose it is worn in.

    The rest pose has the arms straight out sideways: a staff read from it
    looks like it is skewered through the ribs, which says nothing about how
    the figure actually stands.
    """
    action = bpy.data.actions.get(clip)
    if action is None:
        return
    for obj in objects:
        if obj.type != "ARMATURE":
            continue
        if obj.animation_data is None:
            obj.animation_data_create()
        obj.animation_data.action = action
        slots = getattr(action, "slots", None)
        if slots:
            obj.animation_data.action_slot = slots[0]
    bpy.context.scene.frame_set(int(action.frame_range[0]))


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--glb", nargs="*", default=None)
    parser.add_argument("--parts", action="store_true")
    parser.add_argument("--all-parts", action="store_true", help="ignore style tags")
    parser.add_argument("--base", default=None, help="with --parts: only this base")
    parser.add_argument("--dir", default=None, help="read GLBs from here, not the shipped tree")
    parser.add_argument("--clip", default="idle", help="hold frame 1 of this clip")
    args = parser.parse_args(argv)
    out = Path(args.out)
    figures = Path(args.dir) if args.dir else FIGURES

    if args.glb:
        for path in args.glb:
            reset()
            objects = import_glb(Path(path))
            pose_to_clip(objects, args.clip)
            render_views(objects, out, Path(path).stem)
        return

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    for base in catalog["bases"]:
        if args.base and base["base"] != args.base:
            continue
        reset()
        objects = import_glb(figures / base["file"])
        pose_to_clip(objects, args.clip)
        render_views(objects, out, base["base"])
        if not args.parts:
            continue
        for part in catalog["parts"]:
            if part["archetype"] != base["archetype"]:
                continue
            # Only what the creator would offer: a part and a base meet when
            # they share a style. Rendering a baseball cap on a wizard proves
            # nothing about a combination nobody can pick.
            if not args.all_parts and not set(part["styles"]) & set(base["styles"]):
                continue
            reset()
            objects = import_glb(figures / base["file"])
            body_rig = next((o for o in objects if o.type == "ARMATURE"), None)
            for hidden in part.get("hides", []):
                hide_material_slot(objects, f"-{hidden}")
            worn = import_glb(figures / part["file"])
            objects += rebind_to(worn, body_rig)
            pose_to_clip(objects, args.clip)
            render_views(objects, out, f"{base['base']}+{part['id']}")


if __name__ == "__main__":
    main()
