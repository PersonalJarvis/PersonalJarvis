"""Gigi — the pixel-ghost mascot — modelled and animated by script, inside Blender.

The deck room already draws the mascot in 3D by extruding its SVG outline
(``components/deck/room/GigiFigure.tsx``); this module builds the same figure
as a contract figure of the ``spirit`` archetype (character-pipeline.md §3):
one mesh with two material slots (the lit body, the unlit marks — eyes,
mouth, scanlines, glitch pixels), a three-bone rig (``root`` → ``body`` →
``face``) and six clips keyed here, so nothing about Jarvis' own face depends
on a download.

Coordinates: the SVG box is 256 wide; the figure runs from y=36 (top) to
y=208 (hem). Blender is Z-up and the front faces −Y; the exporter maps that
to glTF +Y up / +Z forward (contract §4.1).
"""

from __future__ import annotations

import math

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

HEIGHT_M = 1.9
SVG_TOP, SVG_HEM = 36.0, 208.0
S = HEIGHT_M / (SVG_HEM - SVG_TOP)
DEPTH = 0.34
HOVER = 0.0  # geometry stands on the origin; the clips lift the ghost (HOVER_LIFT)
HOVER_LIFT = 0.12  # how high the body bone floats above its ground blob, in every clip
FPS = 30

# Cells of the 16-cell strip (contract sheet) each part is painted with.
CELL_BODY = "primary"
CELL_SLICE = "primary_shade"
CELL_MARK = "secondary"
CELL_MARK_DIM = "secondary_shade"
CELL_PUPIL = "eyes"
CELL_GLOW = "emissive"

BODY_OUTLINE = [
    # (x, y) in SVG units — the quadratic curves sampled, then the zigzag hem.
    *[
        (58 + (128 - 58) * t, 90 - (90 - 36) * (1 - (1 - t) ** 2))
        for t in [i / 12 for i in range(13)]
    ],
    *[(128 + (198 - 128) * t, 36 + (90 - 36) * t * t) for t in [i / 12 for i in range(1, 13)]],
    (198, 208),
    (180, 186),
    (160, 208),
    (140, 186),
    (120, 208),
    (100, 186),
    (80, 208),
    (58, 186),
]


def svg_to_blender(x: float, y: float, depth_y: float = 0.0) -> Vector:
    return Vector(((x - 128.0) * S, depth_y, (SVG_HEM - y) * S))


def _new_mesh_object(name: str, bm: bmesh.types.BMesh) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def build_body() -> bpy.types.Object:
    bm = bmesh.new()
    front = [bm.verts.new(svg_to_blender(x, y, -DEPTH / 2)) for x, y in BODY_OUTLINE]
    bm.verts.ensure_lookup_table()
    face = bm.faces.new(front)
    face.normal_update()
    if face.normal.y > 0:  # the front must face −Y
        bmesh.ops.reverse_faces(bm, faces=[face])
    res = bmesh.ops.extrude_face_region(bm, geom=[face])
    moved = [g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=moved, vec=Vector((0, DEPTH, 0)))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = _new_mesh_object("GigiBody", bm)
    bevel = obj.modifiers.new("Bevel", "BEVEL")
    bevel.width = 0.03
    bevel.segments = 2
    bevel.limit_method = "ANGLE"
    return obj


def build_disc(
    name: str, cx: float, cy: float, rx: float, ry: float, z_off: float
) -> bpy.types.Object:
    bm = bmesh.new()
    centre = svg_to_blender(cx, cy, -DEPTH / 2 - z_off)
    verts = []
    for i in range(20):
        a = i / 20 * math.tau
        verts.append(bm.verts.new(centre + Vector((math.cos(a) * rx * S, 0, math.sin(a) * ry * S))))
    face = bm.faces.new(verts)
    face.normal_update()
    if face.normal.y > 0:
        bmesh.ops.reverse_faces(bm, faces=[face])
    return _new_mesh_object(name, bm)


def build_box(
    name: str, x: float, y: float, w: float, h: float, z_off: float, thick: float = 0.04
) -> bpy.types.Object:
    bm = bmesh.new()
    centre = svg_to_blender(x + w / 2, y + h / 2, -DEPTH / 2 - z_off)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((w * S, thick, h * S)), verts=bm.verts)
    bmesh.ops.translate(bm, vec=centre, verts=bm.verts)
    return _new_mesh_object(name, bm)


def build_arm(name: str, pts: tuple[float, ...]) -> bpy.types.Object:
    ax, ay, cx, cy, bx, by = pts
    curve = bpy.data.curves.new(f"{name}Curve", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 2.75 * S
    curve.bevel_resolution = 3
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(1)
    p0, p1 = spline.bezier_points
    a = svg_to_blender(ax, ay, 0.0)
    c = svg_to_blender(cx, cy, 0.0)
    b = svg_to_blender(bx, by, 0.0)
    p0.co, p1.co = a, b
    p0.handle_left, p0.handle_right = a, a + (c - a) * 0.66
    p1.handle_left, p1.handle_right = b + (c - b) * 0.66, b
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.convert(target="MESH")
    return bpy.context.view_layer.objects.active


def build_parts() -> tuple[list[tuple[bpy.types.Object, str, str]], list[str]]:
    """Every piece as (object, cell, bone). Returns the pieces and the mark-cell names."""
    front = 0.01
    pieces: list[tuple[bpy.types.Object, str, str]] = [(build_body(), CELL_BODY, "body")]
    # scanlines and chromatic slices
    pieces.append((build_box("Scan1", 58, 131, 140, 2.4, front), CELL_MARK, "body"))
    pieces.append((build_box("Scan2", 58, 159.5, 140, 1.4, front), CELL_MARK_DIM, "body"))
    pieces.append((build_box("SliceL", 64, 118, 18, 10, front), CELL_SLICE, "body"))
    pieces.append((build_box("SliceR", 170, 118, 18, 10, front), CELL_SLICE, "body"))
    # glitch pixels floating beside the body
    for i, (x, y, w, h) in enumerate(
        [(200, 104, 6, 6), (208, 128, 4, 4), (202, 146, 9, 3), (197, 168, 3, 5), (206, 176, 5, 3)]
    ):
        pieces.append((build_box(f"GlitchR{i}", x, y, w, h, -0.06, thick=0.05), CELL_GLOW, "body"))
    for i, (x, y, w, h) in enumerate(
        [(44, 96, 6, 4), (48, 124, 4, 6), (40, 148, 8, 3), (50, 170, 3, 5)]
    ):
        pieces.append(
            (build_box(f"GlitchL{i}", x, y, w, h, -0.06, thick=0.05), CELL_MARK_DIM, "body")
        )
    # eyes, pupils, highlights, mouth — the face bone moves these
    pieces.append((build_disc("EyeL", 102, 108, 10, 14, front), CELL_MARK, "face"))
    pieces.append((build_disc("EyeR", 154, 108, 10, 14, front), CELL_MARK, "face"))
    pieces.append((build_disc("PupilL", 104, 112, 4, 6, front + 0.012), CELL_PUPIL, "face"))
    pieces.append((build_disc("PupilR", 156, 112, 4, 6, front + 0.012), CELL_PUPIL, "face"))
    pieces.append((build_disc("ShineL", 106, 105, 2, 2, front + 0.02), CELL_GLOW, "face"))
    pieces.append((build_disc("ShineR", 158, 105, 2, 2, front + 0.02), CELL_GLOW, "face"))
    pieces.append((build_disc("Mouth", 128, 146, 7, 10, front), CELL_MARK, "face"))
    pieces.append((build_disc("MouthIn", 128, 146, 3, 5, front + 0.012), CELL_PUPIL, "face"))
    # arms
    pieces.append((build_arm("ArmL", (58, 140, 40, 148, 42, 162)), CELL_MARK, "body"))
    pieces.append((build_arm("ArmR", (198, 140, 216, 148, 214, 162)), CELL_MARK, "body"))
    marks = [CELL_MARK, CELL_MARK_DIM, CELL_PUPIL, CELL_GLOW]
    return pieces, marks


def build_armature() -> bpy.types.Object:
    arm_data = bpy.data.armatures.new("GigiRig")
    arm = bpy.data.objects.new("Rig", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root = arm_data.edit_bones.new("root")
    root.head, root.tail = Vector((0, 0, 0)), Vector((0, 0, 0.2))
    body = arm_data.edit_bones.new("body")
    body.head, body.tail = Vector((0, 0, 0.0)), Vector((0, 0, HEIGHT_M * 0.55))
    body.parent = root
    face = arm_data.edit_bones.new("face")
    face.head = svg_to_blender(128, 130, 0.0)
    face.tail = face.head + Vector((0, 0, 0.3))
    face.parent = body
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def assemble(
    pieces, cells: list[str], sheet: dict, img, sheet_material
) -> tuple[bpy.types.Object, dict]:
    """Join every piece into one mesh: UVs on the cell strip, material slots body/marks."""
    n_cells = len(sheet["cells"])
    cv = 1.0 - (sheet["cell_height"] / 2) / sheet["size"]
    body_cells = {CELL_BODY, CELL_SLICE}
    used: dict[str, int] = {}
    for obj, cell, bone in pieces:
        mesh = obj.data
        if not mesh.uv_layers:
            mesh.uv_layers.new(name="UVMap")
        uv = mesh.uv_layers.active
        cu = (cells.index(cell) + 0.5) / n_cells
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                uv.data[li].uv = (cu, cv)
            poly.material_index = 0 if cell in body_cells else 1
        used[cell] = used.get(cell, 0) + len(mesh.polygons)
        # Two slots on every piece so the join keeps indices aligned.
        mesh.materials.clear()
        mesh.materials.append(sheet_material("spirit-gigi-sheet", img))
        mesh.materials.append(sheet_material("spirit-gigi-marks", img))
        vg = obj.vertex_groups.new(name=bone)
        vg.add([v.index for v in mesh.vertices], 1.0, "REPLACE")
    for obj in bpy.data.objects:
        obj.select_set(False)
    objs = [p[0] for p in pieces]
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    body = bpy.context.view_layer.objects.active
    body.name = "Body"
    body.data.name = "Body"
    # Every material slot but the first two came in duplicated by the join.
    while len(body.data.materials) > 2:
        body.data.materials.pop(index=len(body.data.materials) - 1)
    return body, {"used": used, "unmapped": {}, "hair_faces": []}


def rig(body: bpy.types.Object, arm: bpy.types.Object) -> None:
    body.parent = arm
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm


# ---------------------------------------------------------------------------
# clips
# ---------------------------------------------------------------------------


def _key(arm, bone: str, frame: int, *, loc=None, rot=None) -> None:
    pb = arm.pose.bones[bone]
    if loc is not None:
        lifted = (loc[0], loc[1] + (HOVER_LIFT if bone == "body" else 0.0), loc[2])
        pb.location = Vector(lifted)
        pb.keyframe_insert("location", frame=frame)
    if rot is not None:
        pb.rotation_mode = "XYZ"
        pb.rotation_euler = tuple(math.radians(v) for v in rot)
        pb.keyframe_insert("rotation_euler", frame=frame)


def _action(arm, name: str, seconds: float):
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = action
    try:  # slotted actions (Blender 4.4+)
        if not action.slots:
            slot = action.slots.new("OBJECT", arm.name)
        else:
            slot = action.slots[0]
        arm.animation_data.action_slot = slot
    except AttributeError:
        pass
    frames = max(2, round(seconds * FPS))
    action.frame_range = (1, frames + 1)
    return action, frames


def _sampled(arm, name: str, seconds: float, fn) -> None:
    """Key every frame of a loop from ``fn(t)`` → {bone: (loc, rot)}, first == last."""
    action, frames = _action(arm, name, seconds)
    for f in range(frames + 1):
        t = f / frames
        for bone, (loc, rot) in fn(t if f < frames else 0.0).items():
            _key(arm, bone, f + 1, loc=loc, rot=rot)
    action.use_frame_range = True
    action.frame_range = (1, frames + 1)


def _oneshot(arm, name: str, seconds: float, fn) -> None:
    action, frames = _action(arm, name, seconds)
    for f in range(frames + 1):
        t = f / frames
        for bone, (loc, rot) in fn(t).items():
            _key(arm, bone, f + 1, loc=loc, rot=rot)
    action.use_frame_range = True
    action.frame_range = (1, frames + 1)


def key_clips(arm: bpy.types.Object) -> None:
    two_pi = math.tau

    def idle(t):
        return {
            "body": ((0, math.sin(t * two_pi) * 0.03, 0), (0, math.sin(t * two_pi + 1) * 1.5, 0)),
            "face": ((0, 0, 0), (0, 0, 0)),
        }

    def walk(t):  # the glide: leaning forward, bobbing quicker
        return {
            "body": ((0, math.sin(t * two_pi * 2) * 0.04, 0), (-9, math.sin(t * two_pi) * 2, 0)),
            "face": ((0, 0, 0), (0, 0, 0)),
        }

    def talk(t):
        return {
            "body": ((0, math.sin(t * two_pi) * 0.02, 0), (0, 0, 0)),
            "face": ((0, abs(math.sin(t * two_pi * 3)) * 0.03, 0), (0, 0, 0)),
        }

    def sleep(t):
        return {
            "body": ((0, -0.08 + math.sin(t * two_pi) * 0.012, 0), (10, 0, 6)),
            "face": ((0, -0.02, 0), (0, 0, 0)),
        }

    def celebrate(t):  # a hop and a full spin
        hop = math.sin(min(t * 2, 1.0) * math.pi) * 0.25
        return {
            "body": ((0, hop, 0), (0, t * 360, 0)),
            "face": ((0, 0, 0), (0, 0, 0)),
        }

    def wave(t):  # sway twice, like a friendly wobble
        return {
            "body": ((0, 0, 0), (0, 0, math.sin(t * two_pi * 2) * 14)),
            "face": ((0, 0, 0), (0, 0, 0)),
        }

    _sampled(arm, "idle", 2.0, idle)
    _sampled(arm, "walk", 1.0, walk)
    _sampled(arm, "talk", 1.2, talk)
    _sampled(arm, "sleep", 4.0, sleep)
    _oneshot(arm, "celebrate", 1.2, celebrate)
    _oneshot(arm, "wave", 1.0, wave)
    arm.animation_data.action = None


def build_gigi(sheet: dict, img, sheet_material) -> tuple[bpy.types.Object, bpy.types.Object, dict]:
    """Build the whole figure into the current (empty) scene.

    Returns (armature, body, uv_report).
    """
    pieces, _marks = build_parts()
    body, report = assemble(pieces, sheet["cells"], sheet, img, sheet_material)
    arm = build_armature()
    rig(body, arm)
    key_clips(arm)
    return arm, body, report
