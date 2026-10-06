"""Author the 3D Jarvis pets that follow the person in the Jarvis Verse.

Run in Blender:

    blender --background --factory-startup --disable-autoexec \
        --python scripts/art/build_pet_companions.py [-- --render <dir>]

It saves the editable master to ``art/studies/jarvis-pet-companions/source/pets.blend``
and exports one ``exports/<pet>.glb`` per pet. ``--render`` also writes one review image per pet.

Conventions (see the study brief): metres, Blender Z up, -Y forward (glTF +Z
forward), every pet bottom-centred on its own root ``Pet_<id>``. Parts the
runtime animates hang under pivot empties named ``<id>_<Part>``; names use
underscores because three.js strips dots from glTF node names. Colours come
from the pixel pets in ``scripts/pets/build_pets.py``.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/jarvis-pet-companions"
TAU = 2 * math.pi
COLLECTION = "PetsExport"

_materials: dict[str, bpy.types.Material] = {}


def hex_rgb(value: str) -> tuple[float, float, float]:
    """sRGB hex to the linear floats Blender stores."""

    def channel(byte: int) -> float:
        c = byte / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    value = value.lstrip("#")
    return tuple(channel(int(value[i : i + 2], 16)) for i in (0, 2, 4))  # type: ignore[return-value]


def material(name: str, colour: str, roughness: float = 0.78, emission: float = 0.0):
    if name in _materials:
        return _materials[name]
    rgb = hex_rgb(colour)
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*rgb, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*rgb, 1)
    shader.inputs["Metallic"].default_value = 0.0
    shader.inputs["Roughness"].default_value = roughness
    if emission:
        shader.inputs["Emission Color"].default_value = (*rgb, 1)
        shader.inputs["Emission Strength"].default_value = emission
    _materials[name] = mat
    return mat


def empty(name: str, location=(0, 0, 0), parent=None):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    obj.location = location
    obj.empty_display_size = 0.03
    return obj


def finish(obj, name: str, mat, parent):
    obj.name = name
    obj.data.name = name
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.parent = parent
    for face in obj.data.polygons:
        face.use_smooth = True
    return obj


def ellipsoid(name, centre, radii, mat, parent, segments=22, rings=11):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, location=centre)
    obj = bpy.context.object
    obj.scale = radii
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(obj, name, mat, parent)


def cone(name, base, tip, radius, mat, parent, vertices=20):
    """A round cone standing on ``base`` and pointing at ``tip``."""
    a, b = Vector(base), Vector(tip)
    axis = b - a
    bpy.ops.mesh.primitive_cone_add(
        vertices=vertices,
        radius1=radius,
        radius2=radius * 0.12,
        depth=axis.length,
        location=(a + b) / 2,
    )
    obj = bpy.context.object
    obj.rotation_euler = axis.to_track_quat("Z", "Y").to_euler()
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    return finish(obj, name, mat, parent)


def rounded_box(name, centre, size, radius, mat, parent):
    bpy.ops.mesh.primitive_cube_add(size=1, location=centre)
    obj = bpy.context.object
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = obj.modifiers.new("Soft edge", "BEVEL")
    bevel.width = radius
    bevel.segments = 4
    bevel.limit_method = "NONE"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    return finish(obj, name, mat, parent)


def tube(name, points, radius, mat, parent, closed=False, taper: float | None = None):
    """A round tube through ``points``; ``taper`` scales the radius at the far end."""
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 2
    curve.use_fill_caps = True
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    count = len(points)
    for index, (point, position) in enumerate(zip(spline.points, points, strict=True)):
        point.co = (*position, 1)
        if taper is not None and count > 1:
            point.radius = 1 + (taper - 1) * index / (count - 1)
    spline.use_cyclic_u = closed
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.convert(target="MESH")
    obj.select_set(False)
    return finish(obj, name, mat, parent)


def smooth_points(points, steps=4):
    """Catmull-Rom resample so tubes read as soft curves, not polylines."""
    pts = [Vector(p) for p in points]
    out = []
    for i in range(len(pts) - 1):
        p0 = pts[max(i - 1, 0)]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[min(i + 2, len(pts) - 1)]
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            out.append(
                0.5
                * (
                    (2 * p1)
                    + (-p0 + p2) * t
                    + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                    + (-p0 + 3 * p1 - 3 * p2 + p3) * t3
                )
            )
    out.append(pts[-1])
    return [tuple(v) for v in out]


def slab(name, outline, thickness, mat, parent):
    """A flat shape in the local XZ plane (outline of (x, z) pairs), ``thickness`` along Y."""
    count = len(outline)
    half = thickness / 2
    vertices = [(x, y, z) for y in (-half, half) for x, z in outline]
    faces = [tuple(range(count))[::-1], tuple(range(count, 2 * count))]
    faces += [(i, (i + 1) % count, (i + 1) % count + count, i + count) for i in range(count)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bevel = obj.modifiers.new("Soft edge", "BEVEL")
    bevel.width = min(0.006, half * 0.9)
    bevel.segments = 2
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    obj = finish(obj, name, mat, parent)
    for face in obj.data.polygons:
        face.use_smooth = False
    return obj


def dot_eye(prefix, side, pivot_at, parent, radii, iris, pupil=None, glint="#f7f7fb"):
    """A pivot ``<prefix>_Eye_<side>`` (the runtime blinks it by scaling z) with a dot eye."""
    eye = empty(f"{prefix}_Eye_{side}", pivot_at, parent)
    ellipsoid(
        f"{prefix}_Iris_{side}",
        (0, 0, 0),
        radii,
        material(f"{prefix}.Iris", iris, 0.35),
        eye,
        20,
        10,
    )
    if pupil:
        ellipsoid(
            f"{prefix}_Pupil_{side}",
            (0, -radii[1] * 0.55, -radii[2] * 0.15),
            (radii[0] * 0.5, radii[1] * 0.5, radii[2] * 0.62),
            material(f"{prefix}.Pupil", pupil, 0.3),
            eye,
            16,
            8,
        )
    ellipsoid(
        f"{prefix}_Glint_{side}",
        (radii[0] * 0.32, -radii[1] * 0.9, radii[2] * 0.38),
        (radii[0] * 0.3, radii[1] * 0.3, radii[2] * 0.26),
        material("Pet.Glint", glint, 0.2, 0.4),
        eye,
        12,
        6,
    )
    return eye


def floppy_ear(name, sign, length, width, thickness, splay, curl, mat, parent):
    """A soft ear flap hanging from its pivot, widest near the top, its tip curling out."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=18, ring_count=12, location=(0, 0, 0))
    obj = bpy.context.object
    for vertex in obj.data.vertices:
        x, y, z = vertex.co
        down = (1 - z) / 2  # 0 at the root .. 1 at the tip
        taper = 1 - 0.45 * down**1.5
        vertex.co = (
            sign * (thickness * (x * sign + 0.6) * taper + splay * down + curl * down**2.4),
            y * width * taper - 0.25 * width * down**2,
            -length * down,
        )
    obj.data.update()
    return finish(obj, name, mat, parent)


def smile(name, centre, width, depth, mat, parent, lift=0.0):
    cx, cy, cz = centre
    points = [
        (cx + width * math.cos(a), cy, cz - depth * math.sin(a) + lift)
        for a in [math.pi * (0.15 + 0.7 * i / 10) for i in range(11)]
    ]
    return tube(name, points, 0.0045, mat, parent)


# ---------------------------------------------------------------------------
# The pets
# ---------------------------------------------------------------------------

INK = "#1b1622"
BLUSH = "#ff8fa3"


def build_miso(root):
    fur = material("miso.Fur", "#f2a65a")
    cream = material("miso.Cream", "#fff0d9")
    stripe = material("miso.Stripe", "#c8743a")
    ear = material("miso.Ear", "#ff9fb2")
    nose = material("miso.Nose", "#ff7a8a", 0.5)
    ink = material("Pet.Ink", INK, 0.6)

    body = empty("miso_Body", (0, 0, 0.17), root)
    ellipsoid("miso_Torso", (0, 0.03, 0), (0.105, 0.16, 0.1), fur, body)
    ellipsoid("miso_Belly", (0, 0.0, -0.035), (0.08, 0.13, 0.065), cream, body)
    for i, y in enumerate((0.0, 0.06, 0.12)):
        ellipsoid(f"miso_BackStripe_{i}", (0, y, 0.088), (0.075, 0.013, 0.022), stripe, body, 16, 8)

    head = empty("miso_Head", (0, -0.13, 0.33), root)
    ellipsoid("miso_Skull", (0, 0, 0), (0.15, 0.125, 0.122), fur, head)
    ellipsoid("miso_Muzzle", (0, -0.1, -0.045), (0.065, 0.04, 0.042), cream, head)
    ellipsoid("miso_Nose", (0, -0.138, -0.02), (0.016, 0.01, 0.011), nose, head, 12, 6)
    smile("miso_Mouth", (0, -0.141, -0.042), 0.02, 0.012, ink, head)
    for i, x in enumerate((-0.035, 0.0, 0.035)):
        ellipsoid(
            f"miso_Stripe_{i}", (x, -0.045, 0.105), (0.012, 0.035, 0.016), stripe, head, 12, 6
        )
    for side, sign in (("L", -1), ("R", 1)):
        dot_eye(
            "miso",
            side,
            (sign * 0.058, -0.108, 0.018),
            head,
            (0.024, 0.012, 0.03),
            "#6fcf7f",
            "#123524",
        )
        ellipsoid(
            f"miso_Cheek_{side}",
            (sign * 0.09, -0.098, -0.03),
            (0.022, 0.01, 0.013),
            material("Pet.Blush", BLUSH, 0.7),
            head,
            12,
            6,
        )
        pinna = empty(f"miso_Ear_{side}", (sign * 0.085, 0.0, 0.085), head)
        cone(f"miso_EarOuter_{side}", (0, 0, 0), (sign * 0.025, 0.0, 0.1), 0.052, fur, pinna, 16)
        cone(
            f"miso_EarInner_{side}",
            (0, -0.016, 0.004),
            (sign * 0.022, -0.016, 0.085),
            0.033,
            ear,
            pinna,
            12,
        )
        for j, dz in enumerate((-0.005, 0.012)):
            tube(
                f"miso_Whisker_{side}_{j}",
                [(sign * 0.06, -0.125, -0.035 + dz), (sign * 0.13, -0.13, -0.03 + dz * 1.8)],
                0.0022,
                cream,
                head,
            )

    for name, x, y in (
        ("FL", -0.062, -0.085),
        ("FR", 0.062, -0.085),
        ("BL", -0.066, 0.13),
        ("BR", 0.066, 0.13),
    ):
        leg = empty(f"miso_Leg_{name}", (x, y, 0.13), root)
        ellipsoid(f"miso_LegFur_{name}", (0, 0, -0.05), (0.036, 0.038, 0.06), fur, leg, 16, 8)
        ellipsoid(f"miso_Paw_{name}", (0, -0.012, -0.108), (0.04, 0.047, 0.024), cream, leg, 16, 8)

    tail = empty("miso_Tail", (0, 0.185, 0.2), root)
    tube(
        "miso_TailFur",
        smooth_points(
            [(0, 0, 0), (0, 0.07, 0.03), (0, 0.11, 0.11), (0.02, 0.09, 0.2), (0.05, 0.05, 0.24)]
        ),
        0.023,
        fur,
        tail,
        taper=0.75,
    )
    ellipsoid("miso_TailTip", (0.05, 0.05, 0.24), (0.02, 0.02, 0.02), stripe, tail, 12, 6)


def build_ember(root):
    scale = material("ember.Scale", "#e8505b")
    light = material("ember.ScaleLight", "#ff8a80")
    dark = material("ember.ScaleDark", "#b0303f")
    belly = material("ember.Belly", "#ffe2ad")
    band = material("ember.BellyBand", "#f0b871")
    wing = material("ember.Wing", "#ffad7a")
    wing_dark = material("ember.WingBone", "#e57a52")
    horn = material("ember.Horn", "#ffd45c", 0.55)
    ink = material("Pet.Ink", INK, 0.6)

    body = empty("ember_Body", (0, 0, 0.17), root)
    ellipsoid("ember_Torso", (0, 0.01, 0), (0.12, 0.11, 0.135), scale, body)
    ellipsoid("ember_BellyPlate", (0, -0.07, -0.005), (0.085, 0.05, 0.105), belly, body)
    for i, z in enumerate((-0.06, -0.015, 0.03)):
        ellipsoid(f"ember_Band_{i}", (0, -0.112, z), (0.07, 0.008, 0.01), band, body, 16, 6)
    for i, z in enumerate((0.09, 0.03, -0.03)):
        cone(f"ember_Spike_{i}", (0, 0.1, z), (0, 0.15, z + 0.03), 0.022, horn, body, 10)

    head = empty("ember_Head", (0, -0.02, 0.36), root)
    ellipsoid("ember_Skull", (0, 0, 0), (0.15, 0.13, 0.12), scale, head)
    ellipsoid("ember_Snout", (0, -0.105, -0.04), (0.085, 0.06, 0.052), light, head)
    for side, sign in (("L", -1), ("R", 1)):
        ellipsoid(
            f"ember_Nostril_{side}",
            (sign * 0.025, -0.16, -0.025),
            (0.009, 0.006, 0.007),
            material("ember.Nostril", "#7a1f2b", 0.6),
            head,
            10,
            6,
        )
        dot_eye(
            "ember",
            side,
            (sign * 0.062, -0.1, 0.03),
            head,
            (0.028, 0.013, 0.036),
            "#2a1830",
            None,
            "#ffb347",
        )
        ellipsoid(
            f"ember_Cheek_{side}",
            (sign * 0.098, -0.09, -0.02),
            (0.022, 0.01, 0.013),
            material("Pet.EmberBlush", "#ffb0c0", 0.7),
            head,
            12,
            6,
        )
        cone(
            f"ember_Horn_{side}",
            (sign * 0.075, 0.02, 0.085),
            (sign * 0.12, 0.06, 0.17),
            0.028,
            horn,
            head,
            14,
        )
        ellipsoid(
            f"ember_Fin_{side}", (sign * 0.145, 0.02, 0.02), (0.02, 0.04, 0.05), wing, head, 12, 6
        )
    for i, (x, z) in enumerate(((-0.025, 0.12), (0.02, 0.125), (0.0, 0.135))):
        ellipsoid(f"ember_Tuft_{i}", (x, -0.01, z), (0.03, 0.03, 0.026), belly, head, 12, 6)
    smile("ember_Mouth", (0, -0.163, -0.06), 0.03, 0.012, ink, head)

    for side, sign in (("L", -1), ("R", 1)):
        # Wings sit on the back, swept back a little; the runtime flaps them about the
        # pivot's local forward axis and keeps this sweep.
        pivot = empty(f"ember_Wing_{side}", (sign * 0.055, 0.085, 0.235), root)
        pivot.rotation_euler.z = sign * math.radians(28)
        outline = [
            (0, 0),
            (0.05, 0.1),
            (0.17, 0.14),
            (0.23, 0.05),
            (0.19, -0.01),
            (0.13, 0.015),
            (0.085, -0.035),
        ]
        slab(f"ember_Membrane_{side}", [(sign * x, z) for x, z in outline], 0.016, wing, pivot)
        tube(
            f"ember_WingBone_{side}",
            smooth_points(
                [(0, 0, 0), (sign * 0.05, 0, 0.1), (sign * 0.17, 0, 0.14), (sign * 0.23, 0, 0.05)],
                4,
            ),
            0.011,
            wing_dark,
            pivot,
        )
        arm = empty(f"ember_Arm_{side}", (sign * 0.105, -0.05, 0.22), root)
        ellipsoid(
            f"ember_ArmScale_{side}",
            (sign * 0.012, -0.012, -0.03),
            (0.032, 0.032, 0.05),
            scale,
            arm,
            14,
            8,
        )
        leg = empty(f"ember_Leg_{side}", (sign * 0.07, -0.01, 0.085), root)
        ellipsoid(f"ember_Thigh_{side}", (0, 0, -0.025), (0.05, 0.055, 0.055), scale, leg, 16, 8)
        ellipsoid(f"ember_Foot_{side}", (0, -0.03, -0.065), (0.042, 0.05, 0.022), belly, leg, 14, 8)

    tail = empty("ember_Tail", (0, 0.1, 0.1), root)
    tube(
        "ember_TailScale",
        smooth_points([(0, 0, 0), (0, 0.09, -0.04), (0.04, 0.18, -0.02), (0.05, 0.23, 0.05)]),
        0.036,
        scale,
        tail,
        taper=0.35,
    )
    cone("ember_TailTip", (0.05, 0.23, 0.05), (0.06, 0.26, 0.11), 0.03, dark, tail, 12)


def build_shelly(root):
    skin = material("shelly.Skin", "#c3de7a")
    skin_light = material("shelly.SkinLight", "#e3f4a9")
    skin_dark = material("shelly.SkinDark", "#8eae4c")
    shell = material("shelly.Shell", "#e08a4f")
    shell_light = material("shelly.ShellLight", "#f5b884")
    spiral = material("shelly.Spiral", "#6b3417")
    white = material("Pet.EyeWhite", "#f7f7fb", 0.4)
    ink = material("Pet.Ink", INK, 0.6)

    body = empty("shelly_Body", (0, 0, 0), root)
    ellipsoid("shelly_Foot", (0, 0.05, 0.058), (0.09, 0.25, 0.058), skin, body)
    ellipsoid("shelly_Sole", (0, 0.05, 0.011), (0.096, 0.252, 0.011), skin_dark, body, 22, 6)
    head = empty("shelly_Head", (0, -0.15, 0.06), body)
    ellipsoid("shelly_Neck", (0, 0, 0.085), (0.085, 0.085, 0.12), skin, head)
    ellipsoid("shelly_Face", (0, -0.035, 0.1), (0.07, 0.06, 0.08), skin_light, head)
    smile("shelly_Mouth", (0, -0.09, 0.07), 0.022, 0.01, ink, head)
    for side, sign in (("L", -1), ("R", 1)):
        ellipsoid(
            f"shelly_Cheek_{side}",
            (sign * 0.05, -0.075, 0.075),
            (0.017, 0.009, 0.011),
            material("Pet.Blush", BLUSH, 0.7),
            head,
            12,
            6,
        )
        stalk = empty(f"shelly_Stalk_{side}", (sign * 0.035, -0.02, 0.18), head)
        tube(
            f"shelly_StalkSkin_{side}",
            smooth_points(
                [(0, 0, 0), (sign * 0.012, -0.01, 0.06), (sign * 0.022, -0.012, 0.11)], 4
            ),
            0.011,
            skin,
            stalk,
        )
        eye = empty(f"shelly_Eye_{side}", (sign * 0.022, -0.012, 0.13), stalk)
        ellipsoid(f"shelly_EyeBall_{side}", (0, 0, 0), (0.03, 0.03, 0.03), white, eye, 18, 10)
        ellipsoid(f"shelly_Pupil_{side}", (0, -0.025, 0), (0.014, 0.008, 0.016), ink, eye, 12, 6)
        ellipsoid(
            f"shelly_Glint_{side}",
            (0.006, -0.032, 0.007),
            (0.004, 0.002, 0.004),
            material("Pet.Glint", "#f7f7fb", 0.2, 0.4),
            eye,
            8,
            4,
        )

    house = empty("shelly_Shell", (0, 0.07, 0.1), body)
    centre = Vector((0, 0, 0.13))
    ellipsoid("shelly_ShellCase", tuple(centre), (0.085, 0.15, 0.15), shell, house)
    ellipsoid("shelly_ShellRim", (0, 0, 0.13), (0.07, 0.155, 0.155), shell_light, house)
    for side, sign in (("L", -1), ("R", 1)):
        points = []
        # A spiral that hugs the case: x follows the ellipsoid surface at each radius.
        for i in range(56):
            a = i / 55 * TAU * 2.2
            r = 0.125 * (1 - i / 62)
            points.append(
                (
                    sign * (0.085 * math.sqrt(max(0.0, 1 - (r / 0.15) ** 2)) + 0.002),
                    centre.y + r * math.cos(a),
                    centre.z + r * math.sin(a),
                )
            )
        tube(f"shelly_Spiral_{side}", points, 0.009, spiral, house)


def build_mochi(root):
    jelly = material("mochi.Jelly", "#ff9fcb", 0.35)
    shine = material("mochi.Shine", "#ffd3e8", 0.2, 0.15)
    blush = material("mochi.Blush", "#ff5f9a", 0.6)
    ink = material("mochi.Ink", "#3b1f2b", 0.5)

    body = empty("mochi_Body", (0, 0, 0), root)
    ellipsoid("mochi_Jelly", (0, 0, 0.125), (0.175, 0.155, 0.125), jelly, body, 30, 15)
    ellipsoid("mochi_Shine", (-0.07, -0.06, 0.215), (0.045, 0.03, 0.02), shine, body, 16, 8)
    ellipsoid("mochi_ShineDot", (-0.02, -0.08, 0.225), (0.014, 0.01, 0.009), shine, body, 10, 5)
    for side, sign in (("L", -1), ("R", 1)):
        dot_eye("mochi", side, (sign * 0.055, -0.143, 0.14), body, (0.016, 0.01, 0.022), "#3b1f2b")
        ellipsoid(
            f"mochi_Cheek_{side}",
            (sign * 0.098, -0.128, 0.11),
            (0.028, 0.01, 0.014),
            blush,
            body,
            12,
            6,
        )
    smile("mochi_Mouth", (0, -0.153, 0.118), 0.02, 0.01, ink, body)


def build_brew(root):
    glaze = material("brew.Glaze", "#6cc7b3", 0.45)
    glaze_light = material("brew.GlazeLight", "#a9ecdc", 0.35)
    rim = material("brew.Rim", "#3e8f80", 0.5)
    gold = material("brew.Gold", "#ffcf45", 0.4)
    ink = material("brew.Ink", "#22303a", 0.5)

    body = empty("brew_Body", (0, 0, 0), root)
    ellipsoid("brew_Pot", (0, 0, 0.14), (0.16, 0.15, 0.13), glaze, body, 30, 15)
    ellipsoid("brew_Base", (0, 0, 0.025), (0.1, 0.095, 0.025), rim, body, 24, 8)
    ellipsoid("brew_Shine", (-0.08, -0.1, 0.2), (0.03, 0.02, 0.045), glaze_light, body, 12, 6)
    ellipsoid("brew_Collar", (0, 0, 0.255), (0.1, 0.095, 0.018), rim, body, 24, 8)
    for side, sign in (("L", -1), ("R", 1)):
        dot_eye("brew", side, (sign * 0.05, -0.14, 0.16), body, (0.015, 0.01, 0.02), "#22303a")
        ellipsoid(
            f"brew_Cheek_{side}",
            (sign * 0.09, -0.125, 0.13),
            (0.022, 0.01, 0.012),
            material("Pet.Blush", BLUSH, 0.7),
            body,
            12,
            6,
        )
    smile("brew_Mouth", (0, -0.149, 0.135), 0.02, 0.01, ink, body)
    tube(
        "brew_Spout",
        smooth_points([(0.13, 0, 0.11), (0.2, 0, 0.14), (0.235, 0, 0.21), (0.26, 0, 0.245)], 4),
        0.026,
        glaze,
        body,
        taper=0.7,
    )
    ellipsoid("brew_SpoutLip", (0.262, 0, 0.248), (0.022, 0.022, 0.01), rim, body, 12, 6)
    tube(
        "brew_Handle",
        smooth_points(
            [
                (-0.13, 0, 0.22),
                (-0.2, 0, 0.22),
                (-0.235, 0, 0.16),
                (-0.2, 0, 0.09),
                (-0.14, 0, 0.08),
            ],
            5,
        ),
        0.02,
        glaze,
        body,
    )
    lid = empty("brew_Lid", (0, 0, 0.26), body)
    ellipsoid("brew_LidCap", (0, 0, 0.012), (0.088, 0.083, 0.035), glaze, lid, 24, 10)
    ellipsoid("brew_Knob", (0, 0, 0.055), (0.026, 0.026, 0.026), gold, lid, 16, 8)


def build_bolt(root):
    casing = material("bolt.Casing", "#3d9be0", 0.5)
    metal = material("bolt.Metal", "#cfd4de", 0.45)
    metal_dark = material("bolt.MetalDark", "#8d95a6", 0.55)
    window = material("bolt.Window", "#1c2733", 0.35)
    green = material("bolt.Charge", "#6ee07a", 0.4, 0.6)
    ink = material("Pet.Ink", INK, 0.6)

    body = empty("bolt_Body", (0, 0, 0.055), root)
    rounded_box("bolt_Case", (0, 0, 0.14), (0.2, 0.15, 0.28), 0.04, casing, body)
    rounded_box("bolt_Cap", (0, 0, 0.29), (0.09, 0.075, 0.04), 0.012, metal, body)
    rounded_box("bolt_Window", (0, -0.072, 0.115), (0.13, 0.012, 0.14), 0.012, window, body)
    for i, z in enumerate((0.07, 0.112, 0.154)):
        rounded_box(f"bolt_Bar_{i + 1}", (0, -0.08, z), (0.1, 0.008, 0.03), 0.004, green, body)
    for side, sign in (("L", -1), ("R", 1)):
        dot_eye("bolt", side, (sign * 0.04, -0.077, 0.235), body, (0.014, 0.008, 0.019), INK)
        arm = empty(f"bolt_Arm_{side}", (sign * 0.1, 0, 0.17), body)
        ellipsoid(
            f"bolt_ArmNub_{side}", (sign * 0.02, 0, -0.02), (0.026, 0.03, 0.036), metal, arm, 14, 8
        )
        foot = empty(f"bolt_Foot_{side}", (sign * 0.05, 0, 0.0), root)
        ellipsoid(
            f"bolt_FootShoe_{side}",
            (0, -0.012, 0.024),
            (0.04, 0.052, 0.026),
            metal_dark,
            foot,
            14,
            8,
        )
    smile("bolt_Mouth", (0, -0.078, 0.214), 0.018, 0.009, ink, body)


def build_cocoa(root):
    """A chocolate Labrador puppy: chunky body, big head, floppy ears, oversized paws."""
    fur = material("cocoa.Fur", "#6e3b22")
    light = material("cocoa.FurLight", "#9c5a36")
    dark = material("cocoa.FurDark", "#4a2414")
    nose = material("cocoa.Nose", "#2a1610", 0.22)
    tongue = material("cocoa.Tongue", "#ff7a8a", 0.4)
    ink = material("cocoa.Ink", "#2a1610", 0.5)

    body = empty("cocoa_Body", (0, 0, 0.195), root)
    ellipsoid("cocoa_Torso", (0, 0, 0), (0.13, 0.13, 0.118), fur, body)
    ellipsoid("cocoa_Belly", (0, -0.01, -0.048), (0.095, 0.1, 0.07), light, body, 18, 9)
    ellipsoid("cocoa_Chest", (0, -0.1, 0.035), (0.1, 0.08, 0.1), fur, body, 18, 9)
    ellipsoid("cocoa_Bib", (0, -0.145, 0.02), (0.062, 0.04, 0.072), light, body, 16, 8)

    head = empty("cocoa_Head", (0, -0.15, 0.355), root)
    ellipsoid("cocoa_Skull", (0, 0, 0), (0.145, 0.13, 0.128), fur, head, 26, 13)
    for side, sign in (("L", -1), ("R", 1)):
        ellipsoid(
            f"cocoa_Jowl_{side}",
            (sign * 0.068, -0.075, -0.05),
            (0.062, 0.06, 0.056),
            fur,
            head,
            18,
            9,
        )
    ellipsoid("cocoa_Muzzle", (0, -0.122, -0.045), (0.072, 0.062, 0.05), light, head)
    for side, sign in (("L", -1), ("R", 1)):
        ellipsoid(
            f"cocoa_Lip_{side}",
            (sign * 0.028, -0.16, -0.064),
            (0.034, 0.026, 0.026),
            light,
            head,
            16,
            8,
        )
        smile(
            f"cocoa_Mouth_{side}",
            (sign * 0.015, -0.183, -0.064),
            0.016,
            0.009,
            ink,
            head,
        )
    ellipsoid("cocoa_Nose", (0, -0.178, -0.018), (0.031, 0.019, 0.022), nose, head, 18, 9)
    ellipsoid(
        "cocoa_NoseShine",
        (-0.009, -0.195, -0.008),
        (0.009, 0.004, 0.006),
        material("Pet.Glint", "#f7f7fb", 0.2, 0.4),
        head,
        10,
        5,
    )
    tube("cocoa_Philtrum", [(0, -0.186, -0.036), (0, -0.185, -0.062)], 0.003, ink, head)

    # The lower jaw and tongue hinge so the puppy can pant; the mouth shows when it opens.
    ellipsoid(
        "cocoa_MouthInside",
        (0, -0.135, -0.08),
        (0.036, 0.034, 0.016),
        material("cocoa.Mouth", "#5a1f24", 0.6),
        head,
        14,
        7,
    )
    jaw = empty("cocoa_Jaw", (0, -0.1, -0.082), head)
    ellipsoid("cocoa_Chin", (0, -0.04, -0.012), (0.046, 0.042, 0.022), light, jaw, 16, 8)
    lick = empty("cocoa_Tongue", (0, -0.06, -0.01), jaw)
    ellipsoid("cocoa_TongueTip", (0, -0.012, -0.02), (0.022, 0.011, 0.028), tongue, lick, 16, 8)

    for side, sign in (("L", -1), ("R", 1)):
        eye = dot_eye(
            "cocoa",
            side,
            (sign * 0.062, -0.104, 0.03),
            head,
            (0.032, 0.014, 0.036),
            "#7a8a9a",
            "#1a1210",
        )
        ellipsoid(
            f"cocoa_Socket_{side}",
            (sign * 0.062, -0.097, 0.03),
            (0.036, 0.013, 0.04),
            dark,
            head,
            18,
            9,
        )
        ellipsoid(
            f"cocoa_Brow_{side}",
            (sign * 0.064, -0.096, 0.078),
            (0.036, 0.016, 0.011),
            fur,
            head,
            16,
            8,
        )
        ellipsoid(
            f"cocoa_Glint2_{side}",
            (-0.01, -0.0135, -0.013),
            (0.007, 0.004, 0.007),
            material("Pet.Glint", "#f7f7fb", 0.2, 0.4),
            eye,
            10,
            5,
        )
        ellipsoid(
            f"cocoa_Cheek_{side}",
            (sign * 0.1, -0.1, -0.03),
            (0.022, 0.01, 0.013),
            material("Pet.Blush", BLUSH, 0.7),
            head,
            12,
            6,
        )
        # Floppy ears hang beside the head, their tips curling out.
        ear = empty(f"cocoa_Ear_{side}", (sign * 0.142, -0.01, 0.05), head)
        floppy_ear(f"cocoa_EarFlap_{side}", sign, 0.19, 0.07, 0.02, 0.03, 0.04, dark, ear)

    for name, x, y, back in (
        ("FL", -0.072, -0.08, False),
        ("FR", 0.072, -0.08, False),
        ("BL", -0.078, 0.085, True),
        ("BR", 0.078, 0.085, True),
    ):
        leg = empty(f"cocoa_Leg_{name}", (x, y, 0.155), root)
        if back:
            ellipsoid(
                f"cocoa_Haunch_{name}", (0, 0.008, -0.022), (0.052, 0.062, 0.062), fur, leg, 16, 8
            )
        ellipsoid(f"cocoa_LegFur_{name}", (0, 0, -0.06), (0.042, 0.044, 0.074), fur, leg, 16, 8)
        ellipsoid(f"cocoa_Paw_{name}", (0, -0.018, -0.124), (0.05, 0.06, 0.031), light, leg, 18, 9)
        for i, dx in enumerate((-0.022, 0.0, 0.022)):
            ellipsoid(
                f"cocoa_Toe_{name}_{i}",
                (dx, -0.068, -0.124),
                (0.015, 0.014, 0.017),
                light,
                leg,
                8,
                4,
            )

    # A thick otter tail, carried high and happy.
    tail = empty("cocoa_Tail", (0, 0.12, 0.235), root)
    tube(
        "cocoa_TailFur",
        smooth_points([(0, 0, 0), (0, 0.055, 0.022), (0, 0.1, 0.06), (0, 0.128, 0.11)]),
        0.032,
        fur,
        tail,
        taper=0.35,
    )


PETS = {
    "miso": build_miso,
    "ember": build_ember,
    "shelly": build_shelly,
    "mochi": build_mochi,
    "brew": build_brew,
    "bolt": build_bolt,
    "cocoa": build_cocoa,
}


def measure(root) -> dict:
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    triangles = 0
    for obj in root.children_recursive:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, world))
            hi = Vector(map(max, hi, world))
        triangles += sum(len(p.vertices) - 2 for p in obj.data.polygons)
    return {
        "height_m": round(hi.z - lo.z, 3),
        "bottom_m": round(lo.z, 3),
        "width_m": round(hi.x - lo.x, 3),
        "length_m": round(hi.y - lo.y, 3),
        "triangles": triangles,
    }


def render_reviews(target: Path, roots: dict) -> None:
    scene = bpy.context.scene
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection
    world = bpy.data.worlds.new("Pet review studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.55, 0.6, 0.68, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.9
    scene.world = world
    bpy.ops.object.light_add(type="SUN", location=(1, -1, 2))
    sun = bpy.context.object
    sun.data.energy = 3.2
    sun.rotation_euler = (math.radians(50), 0, math.radians(35))
    bpy.ops.mesh.primitive_plane_add(size=6, location=(0, 0, 0))
    floor = bpy.context.object
    floor.data.materials.append(material("Review.Floor", "#e9e4da", 0.9))
    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 32
    scene.cycles.device = "CPU"
    scene.render.resolution_x = scene.render.resolution_y = 520
    scene.view_settings.view_transform = "Standard"
    target.mkdir(parents=True, exist_ok=True)
    for pet_id, root in roots.items():
        for other in roots.values():
            hide = other is not root
            for obj in [other, *other.children_recursive]:
                obj.hide_render = hide
        size = measure(root)
        focus = Vector((0, 0, size["height_m"] * 0.5))
        for view, (azimuth, elevation) in {"front": (-35, 22), "side": (-100, 12)}.items():
            a, e = math.radians(azimuth), math.radians(elevation)
            direction = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
            camera.location = focus + direction * 2
            camera.rotation_euler = (focus - camera.location).to_track_quat("-Z", "Y").to_euler()
            camera.data.ortho_scale = (
                max(size["height_m"], size["length_m"], size["width_m"]) * 1.45
            )
            scene.render.filepath = str(target / f"{pet_id}-{view}.png")
            bpy.ops.render.render(write_still=True)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    render_dir = Path(argv[argv.index("--render") + 1]) if "--render" in argv else None

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    collection = bpy.data.collections.new(COLLECTION)
    scene.collection.children.link(collection)
    bpy.context.view_layer.active_layer_collection = (
        bpy.context.view_layer.layer_collection.children[COLLECTION]
    )

    roots = {}
    report = {
        "blender": bpy.app.version_string,
        "forward": "glTF +Z",
        "pivot": "bottom centre",
        "pets": {},
    }
    for pet_id, build in PETS.items():
        # One collection per pet, so `scripts/art/export_study.py` can re-export a single pet.
        own = bpy.data.collections.new(f"Pet_{pet_id}")
        collection.children.link(own)
        bpy.context.view_layer.active_layer_collection = (
            bpy.context.view_layer.layer_collection.children[COLLECTION].children[own.name]
        )
        root = empty(f"Pet_{pet_id}")
        root["pet_id"] = pet_id
        build(root)
        roots[pet_id] = root
    bpy.context.view_layer.update()
    for pet_id, root in roots.items():
        report["pets"][pet_id] = measure(root)

    source = STUDY / "source/pets.blend"
    source.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0
    scene["production_recipe"] = "scripts/art/build_pet_companions.py"
    bpy.ops.wm.save_as_mainfile(
        filepath=str(source), check_existing=False, relative_remap=False, compress=False
    )

    # One GLB per pet: the runtime loads only the pet the person picked.
    exports = STUDY / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    for pet_id, root in roots.items():
        export = exports / f"{pet_id}.glb"
        bpy.ops.object.select_all(action="DESELECT")
        for obj in [root, *root.children_recursive]:
            obj.select_set(True)
        bpy.ops.export_scene.gltf(
            filepath=str(export),
            export_format="GLB",
            use_selection=True,
            export_yup=True,
            export_extras=True,
            export_texcoords=False,
            export_animations=False,
            export_materials="EXPORT",
        )
        report["pets"][pet_id]["glb_bytes"] = export.stat().st_size
    (STUDY / "source/build-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print("PET_REPORT", json.dumps(report))

    if render_dir is not None:
        render_reviews(render_dir, roots)


if __name__ == "__main__":
    main()
