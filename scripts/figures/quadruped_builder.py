"""A four-legged companion, modelled and animated by script.

The ``animal`` style had no body at all: the CC0 adventurer pack is humanoid
and there is no donor rig for a quadruped, so this module builds the whole
character — the contract's 21-bone skeleton (§4.3), a blocky fox on it, and
its seven clips keyed frame by frame — the way ``gigi_builder`` builds the
mascot. Nothing here is downloaded.

Same box discipline as ``humanoid_builder``: one axis-aligned box per bone,
weight 1, UV'd onto the 16-cell palette strip. A dog, a cat or a wolf is a
different ``Shape`` in ``SHAPES`` and nothing else — the rig and every clip
are shared, so a second animal costs a colour scheme and a few numbers.

Coordinates are Blender's: Z up, the animal faces −Y, +X is its left. Nose at
−Y, tail at +Y, all four feet on z = 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

FPS = 30

# --- skeleton, in metres ----------------------------------------------------
# The animal is built at its final size: the contract's quadruped variant is
# 0.9 m, ear tips included, and the runtime scales from the measured height.
HIP_Y, HIP_Z = 0.30, 0.455
WAIST_Y = 0.10
CHEST_Y = -0.12
SHOULDER_Y = -0.32
NECK_TOP_Y, NECK_TOP_Z = -0.44, 0.62
HEAD_Y, HEAD_Z = -0.44, 0.62
MUZZLE_Y = -0.72
BACK_Z = 0.50
FRONT_LEG_Y = -0.22
BACK_LEG_Y = 0.26
LEG_X = 0.115
KNEE_Z = 0.24
ANKLE_Z = 0.09
EAR_Z = 0.88

#: (name, parent, head, tail). Order is the export order; `root` must be first
#: and the only bone without a parent — the gate insists on exactly one top.
BONES: list[tuple[str, str | None, tuple[float, float, float], tuple[float, float, float]]] = [
    ("root", None, (0.0, 0.0, 0.0), (0.0, 0.0, 0.16)),
    ("hips", "root", (0.0, HIP_Y, HIP_Z), (0.0, WAIST_Y, BACK_Z)),
    ("spine", "hips", (0.0, WAIST_Y, BACK_Z), (0.0, CHEST_Y, BACK_Z + 0.01)),
    ("chest", "spine", (0.0, CHEST_Y, BACK_Z + 0.01), (0.0, SHOULDER_Y, BACK_Z + 0.02)),
    ("neck", "chest", (0.0, SHOULDER_Y, BACK_Z + 0.02), (0.0, NECK_TOP_Y, NECK_TOP_Z)),
    ("head", "neck", (0.0, HEAD_Y, HEAD_Z), (0.0, MUZZLE_Y, HEAD_Z - 0.02)),
    ("jaw", "head", (0.0, HEAD_Y - 0.10, HEAD_Z - 0.06), (0.0, MUZZLE_Y, HEAD_Z - 0.09)),
    ("ear_l", "head", (0.075, HEAD_Y + 0.02, HEAD_Z + 0.11), (0.10, HEAD_Y + 0.03, EAR_Z)),
    ("ear_r", "head", (-0.075, HEAD_Y + 0.02, HEAD_Z + 0.11), (-0.10, HEAD_Y + 0.03, EAR_Z)),
    ("tail_1", "hips", (0.0, HIP_Y + 0.04, HIP_Z + 0.02), (0.0, HIP_Y + 0.26, HIP_Z + 0.02)),
    ("tail_2", "tail_1", (0.0, HIP_Y + 0.26, HIP_Z + 0.02), (0.0, HIP_Y + 0.50, HIP_Z - 0.06)),
]
for _side, _sx in (("l", 1.0), ("r", -1.0)):
    BONES += [
        (
            f"upper_leg_f{_side}",
            "chest",
            (_sx * LEG_X, FRONT_LEG_Y, BACK_Z - 0.05),
            (_sx * LEG_X, FRONT_LEG_Y - 0.01, KNEE_Z),
        ),
        (
            f"lower_leg_f{_side}",
            f"upper_leg_f{_side}",
            (_sx * LEG_X, FRONT_LEG_Y - 0.01, KNEE_Z),
            (_sx * LEG_X, FRONT_LEG_Y + 0.01, ANKLE_Z),
        ),
        (
            f"foot_f{_side}",
            f"lower_leg_f{_side}",
            (_sx * LEG_X, FRONT_LEG_Y + 0.01, ANKLE_Z),
            (_sx * LEG_X, FRONT_LEG_Y - 0.06, 0.0),
        ),
        (
            f"upper_leg_b{_side}",
            "hips",
            (_sx * LEG_X, BACK_LEG_Y, HIP_Z - 0.02),
            (_sx * LEG_X, BACK_LEG_Y + 0.04, KNEE_Z),
        ),
        (
            f"lower_leg_b{_side}",
            f"upper_leg_b{_side}",
            (_sx * LEG_X, BACK_LEG_Y + 0.04, KNEE_Z),
            (_sx * LEG_X, BACK_LEG_Y - 0.03, ANKLE_Z),
        ),
        (
            f"foot_b{_side}",
            f"lower_leg_b{_side}",
            (_sx * LEG_X, BACK_LEG_Y - 0.03, ANKLE_Z),
            (_sx * LEG_X, BACK_LEG_Y - 0.10, 0.0),
        ),
    ]

FRONT_LEGS = ("fl", "fr")
BACK_LEGS = ("bl", "br")
ALL_LEGS = FRONT_LEGS + BACK_LEGS


@dataclass(frozen=True)
class Shape:
    """What tells one animal from the next; the rig and the clips are shared."""

    #: Palette cell of the coat, the underside, the paws and the tail tip.
    coat: str = "fur"
    belly: str = "fur_shade"
    paw: str = "secondary"
    tail_tip: str = "secondary"
    ear_inner: str = "skin"
    nose: str = "eyes"
    #: Half-widths: the barrel, the head, a leg.
    body_w: float = 0.135
    head_w: float = 0.105
    leg_r: float = 0.043
    #: How bushy the tail is, as a half-width.
    tail_r: float = 0.075


SHAPES: dict[str, Shape] = {
    # A fox: narrow muzzle, big ears, a tail almost as thick as the body.
    "fox": Shape(),
}


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


@dataclass
class Piece:
    name: str
    bone: str
    cell: str
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]


def box(
    name: str,
    bone: str,
    cell: str,
    x: tuple[float, float],
    y: tuple[float, float],
    z: tuple[float, float],
) -> Piece:
    return Piece(name, bone, cell, (x[0], y[0], z[0]), (x[1], y[1], z[1]))


def leg_pieces(s: Shape) -> list[Piece]:
    """Three boxes per leg, each on its own bone so the knee never pinches."""
    pieces: list[Piece] = []
    r = s.leg_r
    for leg in ALL_LEGS:
        side = 1.0 if leg.endswith("l") else -1.0
        y = FRONT_LEG_Y if leg.startswith("f") else BACK_LEG_Y
        xs = (side * LEG_X - r, side * LEG_X + r)
        xs = (min(xs), max(xs))
        pieces += [
            box(
                f"UpperLeg_{leg}",
                f"upper_leg_{leg}",
                s.coat,
                xs,
                (y - r, y + r),
                (KNEE_Z, BACK_Z - 0.02),
            ),
            box(
                f"LowerLeg_{leg}",
                f"lower_leg_{leg}",
                s.coat,
                (xs[0] + 0.006, xs[1] - 0.006),
                (y - r + 0.004, y + r - 0.004),
                (ANKLE_Z, KNEE_Z + 0.01),
            ),
            box(
                f"Paw_{leg}",
                f"foot_{leg}",
                s.paw,
                (xs[0] + 0.002, xs[1] - 0.002),
                (y - r - 0.03, y + r - 0.01),
                (0.0, ANKLE_Z + 0.01),
            ),
        ]
    return pieces


def head_pieces(s: Shape) -> list[Piece]:
    w = s.head_w
    skull_y = (HEAD_Y - 0.20, HEAD_Y + 0.06)
    pieces = [
        box("Skull", "head", s.coat, (-w, w), skull_y, (HEAD_Z - 0.10, HEAD_Z + 0.12)),
        box(
            "Muzzle",
            "head",
            s.coat,
            (-w * 0.52, w * 0.52),
            (MUZZLE_Y, HEAD_Y - 0.18),
            (HEAD_Z - 0.09, HEAD_Z + 0.02),
        ),
        box(
            "Cheeks",
            "head",
            s.belly,
            (-w - 0.012, w + 0.012),
            (HEAD_Y - 0.12, HEAD_Y + 0.05),
            (HEAD_Z - 0.10, HEAD_Z - 0.01),
        ),
        box(
            "Nose",
            "head",
            s.nose,
            (-0.026, 0.026),
            (MUZZLE_Y - 0.022, MUZZLE_Y + 0.01),
            (HEAD_Z - 0.05, HEAD_Z - 0.01),
        ),
        box(
            "Jaw",
            "jaw",
            s.belly,
            (-w * 0.5, w * 0.5),
            (MUZZLE_Y + 0.01, HEAD_Y - 0.08),
            (HEAD_Z - 0.115, HEAD_Z - 0.075),
        ),
    ]
    for side, sx in (("L", 1.0), ("R", -1.0)):
        # An animal's eyes sit on the SIDES of the skull, not on its front:
        # placed like a person's they end up buried inside the head, which is
        # exactly how they rendered first time — one dark smear, no face.
        ex = sorted((sx * (w - 0.004), sx * (w + 0.016)))
        pieces.append(
            box(
                f"Eye{side}",
                "head",
                "eye_white",
                ex,
                (HEAD_Y - 0.155, HEAD_Y - 0.10),
                (HEAD_Z + 0.010, HEAD_Z + 0.056),
            )
        )
        px = sorted((sx * (w + 0.012), sx * (w + 0.022)))
        pieces.append(
            box(
                f"Pupil{side}",
                "head",
                "eyes",
                px,
                (HEAD_Y - 0.146, HEAD_Y - 0.112),
                (HEAD_Z + 0.019, HEAD_Z + 0.047),
            )
        )
        bone = f"ear_{'l' if sx > 0 else 'r'}"
        ox = sorted((sx * 0.030, sx * 0.115))
        pieces.append(
            box(
                f"Ear{side}",
                bone,
                s.coat,
                ox,
                (HEAD_Y - 0.02, HEAD_Y + 0.05),
                (HEAD_Z + 0.10, EAR_Z),
            )
        )
        ix = sorted((sx * 0.046, sx * 0.099))
        pieces.append(
            box(
                f"EarIn{side}",
                bone,
                s.ear_inner,
                ix,
                (HEAD_Y - 0.032, HEAD_Y - 0.015),
                (HEAD_Z + 0.12, EAR_Z - 0.03),
            )
        )
    return pieces


def body_pieces(s: Shape) -> list[Piece]:
    w = s.body_w
    return [
        box(
            "Rump",
            "hips",
            s.coat,
            (-w, w),
            (WAIST_Y - 0.01, HIP_Y + 0.10),
            (HIP_Z - 0.16, BACK_Z + 0.03),
        ),
        box(
            "Barrel",
            "spine",
            s.coat,
            (-w, w),
            (CHEST_Y - 0.01, WAIST_Y + 0.01),
            (HIP_Z - 0.17, BACK_Z + 0.03),
        ),
        box(
            "Ribs",
            "chest",
            s.coat,
            (-w, w),
            (SHOULDER_Y - 0.01, CHEST_Y + 0.01),
            (HIP_Z - 0.16, BACK_Z + 0.04),
        ),
        box(
            "Belly",
            "spine",
            s.belly,
            (-w + 0.012, w - 0.012),
            (SHOULDER_Y, HIP_Y + 0.06),
            (HIP_Z - 0.185, HIP_Z - 0.13),
        ),
        box(
            "Neck",
            "neck",
            s.coat,
            (-w * 0.72, w * 0.72),
            (NECK_TOP_Y - 0.02, SHOULDER_Y + 0.03),
            (BACK_Z - 0.06, NECK_TOP_Z + 0.06),
        ),
        box(
            "Bib",
            "neck",
            s.belly,
            (-w * 0.55, w * 0.55),
            (NECK_TOP_Y - 0.03, SHOULDER_Y),
            (BACK_Z - 0.08, BACK_Z + 0.06),
        ),
        box(
            "Tail1",
            "tail_1",
            s.coat,
            (-s.tail_r, s.tail_r),
            (HIP_Y + 0.04, HIP_Y + 0.28),
            (HIP_Z - 0.05, HIP_Z + 0.09),
        ),
        box(
            "Tail2",
            "tail_2",
            s.tail_tip,
            (-s.tail_r + 0.012, s.tail_r - 0.012),
            (HIP_Y + 0.28, HIP_Y + 0.52),
            (HIP_Z - 0.12, HIP_Z + 0.04),
        ),
    ]


def all_pieces(shape: Shape) -> list[Piece]:
    return body_pieces(shape) + head_pieces(shape) + leg_pieces(shape)


def _mesh_from_box(piece: Piece) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    size = Vector(tuple(hi - lo for hi, lo in zip(piece.hi, piece.lo, strict=True)))
    centre = Vector(tuple((hi + lo) / 2 for hi, lo in zip(piece.hi, piece.lo, strict=True)))
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bmesh.ops.translate(bm, vec=centre, verts=bm.verts)
    mesh = bpy.data.meshes.new(piece.name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(piece.name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


# ---------------------------------------------------------------------------
# rig
# ---------------------------------------------------------------------------


def build_armature() -> bpy.types.Object:
    data = bpy.data.armatures.new("QuadrupedRig")
    arm = bpy.data.objects.new("Rig", data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for name, parent, head, tail in BONES:
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = Vector(head), Vector(tail)
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


# ---------------------------------------------------------------------------
# clips
# ---------------------------------------------------------------------------


def _key(arm, bone: str, frame: int, *, loc=None, rot=None) -> None:
    pb = arm.pose.bones[bone]
    if loc is not None:
        pb.location = Vector(loc)
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
        slot = action.slots[0] if action.slots else action.slots.new("OBJECT", arm.name)
        arm.animation_data.action_slot = slot
    except AttributeError:
        pass
    frames = max(2, round(seconds * FPS))
    action.frame_range = (1, frames + 1)
    return action, frames


def _bake(arm, name: str, seconds: float, fn, *, loop: bool) -> None:
    """Key every frame from ``fn(t)`` → {bone: (loc, rot)}.

    A looping clip is keyed with t wrapped back to 0 on the last frame, so the
    first and last key are byte-identical and the gate's loop check passes;
    a one-shot runs the full range and simply ends where it ends.
    """
    action, frames = _action(arm, name, seconds)
    for f in range(frames + 1):
        t = f / frames
        if loop and f == frames:
            t = 0.0
        for bone, (loc, rot) in fn(t).items():
            _key(arm, bone, f + 1, loc=loc, rot=rot)
    action.use_frame_range = True
    action.frame_range = (1, frames + 1)


def _lift(dz: float) -> tuple:
    """Raise or lower the whole animal, in metres.

    Through `root`, whose local Y runs along world Z, and never through the
    root's world X/Z — clips are played in place and the gate rejects root
    motion in the ground plane.
    """
    return ((0.0, dz, 0.0), (0.0, 0.0, 0.0))


# Which way a bone actually turns, measured off the rig rather than guessed:
# +X pitches the body chain's far end DOWN (hips, spine, neck, head) and
# swings a leg's knee BACKWARD; +X lifts the tail.
def _gait(t: float, reach: float, lift: float, bob: float) -> dict:
    """A diagonal four-beat: each leg a half cycle out of phase with its pair."""
    two_pi = math.tau
    pose: dict[str, tuple] = {
        # The barrel rises twice a cycle and the head counter-nods, which is
        # what makes a box on legs read as an animal rather than a table.
        "root": _lift(bob * math.sin(t * two_pi * 2)),
        "spine": (None, (bob * 40 * math.sin(t * two_pi * 2), 0.0, 0.0)),
        "neck": (None, (-bob * 30 * math.sin(t * two_pi * 2), 0.0, 0.0)),
        "head": (None, (bob * 26 * math.sin(t * two_pi * 2), 0.0, 0.0)),
        "tail_1": (None, (12.0, 0.0, 6 * math.sin(t * two_pi * 2))),
        "tail_2": (None, (6.0, 0.0, 9 * math.sin(t * two_pi * 2 + 0.8))),
    }
    phases = {"fl": 0.0, "br": 0.0, "fr": 0.5, "bl": 0.5}
    for leg, phase in phases.items():
        a = (t + phase) * two_pi
        swing = reach * math.sin(a)
        knee = lift * max(0.0, math.sin(a + 0.6))
        pose[f"upper_leg_{leg}"] = (None, (swing, 0.0, 0.0))
        pose[f"lower_leg_{leg}"] = (None, (knee, 0.0, 0.0))
        pose[f"foot_{leg}"] = (None, (-knee * 0.5 - swing * 0.35, 0.0, 0.0))
    return pose


def key_clips(arm: bpy.types.Object) -> None:
    two_pi = math.tau

    def idle(t: float) -> dict:
        breath = math.sin(t * two_pi)
        return {
            "root": _lift(0.004 * breath),
            "spine": (None, (1.2 * breath, 0.0, 0.0)),
            "neck": (None, (-1.0 * breath, 0.0, 0.0)),
            "head": (None, (0.8 * breath, 0.0, 2.5 * math.sin(t * two_pi + 1.1))),
            "ear_l": (None, (0.0, 0.0, -6 * math.sin(t * two_pi * 2))),
            "ear_r": (None, (0.0, 0.0, 6 * math.sin(t * two_pi * 2))),
            "tail_1": (None, (14.0, 0.0, 10 * math.sin(t * two_pi))),
            "tail_2": (None, (8.0, 0.0, 13 * math.sin(t * two_pi + 0.7))),
        }

    def walk(t: float) -> dict:
        return _gait(t, reach=17.0, lift=22.0, bob=0.012)

    def run(t: float) -> dict:
        return _gait(t, reach=30.0, lift=38.0, bob=0.024)

    def talk(t: float) -> dict:
        return {
            "head": (None, (5 * math.sin(t * two_pi), 0.0, 4 * math.sin(t * two_pi * 2))),
            "jaw": (None, (14 * abs(math.sin(t * two_pi * 3)), 0.0, 0.0)),
            "ear_l": (None, (0.0, 0.0, -10 * math.sin(t * two_pi * 2))),
            "ear_r": (None, (0.0, 0.0, 10 * math.sin(t * two_pi * 2))),
            "tail_1": (None, (16.0, 0.0, 16 * math.sin(t * two_pi * 2))),
        }

    def sit(t: float) -> dict:
        """Rump on the ground, chest up, hind legs folded, front legs straight."""
        breath = math.sin(t * two_pi)
        pose = {
            "root": _lift(-0.055),
            "hips": (None, (-7.0, 0.0, 0.0)),
            "spine": (None, (-5.0 + 1.0 * breath, 0.0, 0.0)),
            "chest": (None, (-4.0, 0.0, 0.0)),
            "neck": (None, (-5.0 - 1.0 * breath, 0.0, 0.0)),
            "head": (None, (4.0, 0.0, 2 * math.sin(t * two_pi))),
            "tail_1": (None, (20.0, 0.0, 7 * math.sin(t * two_pi))),
            "tail_2": (None, (10.0, 0.0, 9 * math.sin(t * two_pi + 0.6))),
        }
        for leg in BACK_LEGS:
            pose[f"upper_leg_{leg}"] = (None, (-32.0, 0.0, 0.0))
            pose[f"lower_leg_{leg}"] = (None, (46.0, 0.0, 0.0))
            pose[f"foot_{leg}"] = (None, (-12.0, 0.0, 0.0))
        return pose

    def sleep(t: float) -> dict:
        """Lying down: body on the ground, legs tucked under, muzzle resting.

        No sideways twist. A curl reads as a knot at 20 px and, keyed on the
        spine, it dragged every leg out sideways with it.
        """
        breath = math.sin(t * two_pi)
        pose = {
            "root": _lift(-0.195),
            "spine": (None, (2.0 * breath, 0.0, 0.0)),
            "neck": (None, (20.0, 0.0, 0.0)),
            "head": (None, (-14.0 + 1.5 * breath, 0.0, 0.0)),
            "ear_l": (None, (0.0, 0.0, -18.0)),
            "ear_r": (None, (0.0, 0.0, 18.0)),
            "tail_1": (None, (2.0, 0.0, 18.0)),
            "tail_2": (None, (0.0, 0.0, 22.0)),
        }
        for leg in ALL_LEGS:
            pose[f"upper_leg_{leg}"] = (None, (-58.0, 0.0, 0.0))
            pose[f"lower_leg_{leg}"] = (None, (72.0, 0.0, 0.0))
            pose[f"foot_{leg}"] = (None, (-22.0, 0.0, 0.0))
        return pose

    def celebrate(t: float) -> dict:
        """A hop with the front paws off the ground and the tail going hard."""
        hop = math.sin(min(t * 1.6, 1.0) * math.pi)
        wag = math.sin(t * two_pi * 4)
        pose = {
            "root": _lift(0.13 * hop),
            "spine": (None, (-12.0 * hop, 0.0, 0.0)),
            "neck": (None, (-16.0 * hop, 0.0, 0.0)),
            "head": (None, (-8.0 * hop, 0.0, 6 * wag)),
            "jaw": (None, (12.0 * hop, 0.0, 0.0)),
            "ear_l": (None, (0.0, 0.0, -14 * wag)),
            "ear_r": (None, (0.0, 0.0, 14 * wag)),
            "tail_1": (None, (22.0 * hop, 0.0, 24 * wag)),
            "tail_2": (None, (12.0 * hop, 0.0, 20 * wag)),
        }
        for leg in FRONT_LEGS:
            pose[f"upper_leg_{leg}"] = (None, (-44.0 * hop, 0.0, 0.0))
            pose[f"lower_leg_{leg}"] = (None, (34.0 * hop, 0.0, 0.0))
        return pose

    _bake(arm, "idle", 2.6, idle, loop=True)
    _bake(arm, "walk", 0.9, walk, loop=True)
    _bake(arm, "run", 0.55, run, loop=True)
    _bake(arm, "talk", 1.4, talk, loop=True)
    _bake(arm, "sit", 3.0, sit, loop=True)
    _bake(arm, "sleep", 4.0, sleep, loop=True)
    _bake(arm, "celebrate", 1.4, celebrate, loop=False)
    arm.animation_data.action = None


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def build_quadruped(
    shape_name: str, sheet: dict, img, sheet_material
) -> tuple[bpy.types.Object, bpy.types.Object, dict]:
    """Build the whole animal into the current (empty) scene."""
    shape = SHAPES.get(shape_name)
    if shape is None:
        raise SystemExit(f"unknown quadruped shape {shape_name!r}")
    arm = build_armature()
    bones = {name for name, *_ in BONES}
    pieces = all_pieces(shape)
    stray = sorted({p.bone for p in pieces} - bones)
    if stray:
        raise SystemExit(f"quadruped_builder: pieces bound to bones the rig has not: {stray}")

    cells = sheet["cells"]
    cv = 1.0 - (sheet["cell_height"] / 2) / sheet["size"]
    objects: list[bpy.types.Object] = []
    used: dict[str, int] = {}
    for piece in pieces:
        obj = _mesh_from_box(piece)
        mesh = obj.data
        mesh.uv_layers.new(name="UVMap")
        uv = mesh.uv_layers.active
        cu = (cells.index(piece.cell) + 0.5) / len(cells)
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                uv.data[li].uv = (cu, cv)
        used[piece.cell] = used.get(piece.cell, 0) + len(mesh.polygons)
        group = obj.vertex_groups.new(name=piece.bone)
        group.add([v.index for v in mesh.vertices], 1.0, "REPLACE")
        obj.data.materials.append(sheet_material("tmp-sheet", img))
        objects.append(obj)

    for obj in bpy.data.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    body = bpy.context.view_layer.objects.active
    body.name = "Body"
    body.data.name = "Body"
    while len(body.data.materials) > 1:
        body.data.materials.pop(index=len(body.data.materials) - 1)

    body.parent = arm
    body.matrix_parent_inverse.identity()
    modifier = body.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm
    key_clips(arm)
    return arm, body, {"used": used, "unmapped": {}, "hair_faces": []}


# ---------------------------------------------------------------------------
# parts
# ---------------------------------------------------------------------------

#: Accessories for the quadruped slots (contract §5). Like the humanoid ones
#: they hang on a bone every animal of this rig shares, so one file fits every
#: shape in `SHAPES`.
PART_SPECS: dict[str, dict] = {
    "collar-band": {
        "slot": "collar",
        "label": "Collar",
        "styles": ["animal"],
        "pieces": lambda: [
            box(
                "CollarBand",
                "neck",
                "leather",
                (-0.108, 0.108),
                (SHOULDER_Y - 0.08, SHOULDER_Y - 0.01),
                (BACK_Z - 0.05, NECK_TOP_Z + 0.02),
            ),
            box(
                "CollarTag",
                "neck",
                "accent",
                (-0.028, 0.028),
                (SHOULDER_Y - 0.10, SHOULDER_Y - 0.07),
                (BACK_Z - 0.09, BACK_Z - 0.03),
            ),
        ],
    },
}


def build_part(part_id: str, arm: bpy.types.Object, sheet: dict, img, sheet_material):
    """Build one accessory: boxes, UVs on the strip, weight 1 on its attach bone."""
    spec = PART_SPECS.get(part_id)
    if spec is None:
        raise SystemExit(f"unknown procedural quadruped part {part_id!r}")
    pieces = spec["pieces"]()
    bones = {name for name, *_ in BONES}
    stray = sorted({p.bone for p in pieces} - bones)
    if stray:
        raise SystemExit(f"part {part_id}: bound to bones the rig has not: {stray}")

    cells = sheet["cells"]
    cv = 1.0 - (sheet["cell_height"] / 2) / sheet["size"]
    objects: list[bpy.types.Object] = []
    used: dict[str, int] = {}
    for piece in pieces:
        obj = _mesh_from_box(piece)
        mesh = obj.data
        mesh.uv_layers.new(name="UVMap")
        uv = mesh.uv_layers.active
        cu = (cells.index(piece.cell) + 0.5) / len(cells)
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                uv.data[li].uv = (cu, cv)
        used[piece.cell] = used.get(piece.cell, 0) + len(mesh.polygons)
        group = obj.vertex_groups.new(name=piece.bone)
        group.add([v.index for v in mesh.vertices], 1.0, "REPLACE")
        obj.data.materials.append(sheet_material(f"{part_id}-tmp", img))
        objects.append(obj)

    for obj in bpy.data.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    part = bpy.context.view_layer.objects.active
    part.name = part_id
    part.data.name = part_id
    while len(part.data.materials) > 1:
        part.data.materials.pop(index=len(part.data.materials) - 1)

    part.parent = arm
    part.matrix_parent_inverse.identity()
    modifier = part.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm
    return part, {"used": used, "unmapped": {}, "hair_faces": []}
