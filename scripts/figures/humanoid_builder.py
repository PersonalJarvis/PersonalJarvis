"""Blocky bipeds modelled by script onto the contract's 23-bone rig.

Route B of the character pipeline (character-pipeline.md §8.2), for the styles
the CC0 adventurer pack has no body for: an office worker and a casual person
(``modern``), a big-headed chibi (``cartoon``) and an android (``scifi``).

Why boxes: the island renders through a pixel pass at ~240 px wide, so what
reads at that size is silhouette and colour, not topology. Every piece is one
axis-aligned box, rigidly weighted to exactly ONE bone — no skinning weights
to author, no joint that pinches, and 12 triangles a piece against a 4 500
budget. The rig and its nine clips come from the same CC0 donor the fantasy
bases use, so a new body walks, sits, sleeps and waves on day one.

Proportions are the rig's, not ours: a box spans the rest positions of the
bone it hangs on, so a knee meets a shin whatever the profile does with
widths. Only the head — a free-floating box on the ``head`` bone — changes
size between profiles, which is exactly what separates a person from a chibi.

Coordinates are Blender's: Z up, the figure faces −Y, +X is its left.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

# Rest-pose landmarks of the normalized rig, in Blender units. Read off the
# donor once (`scripts/figures/README.md`); asserted against the live armature
# at build time so a donor change fails loudly instead of deforming quietly.
GROUND = 0.0
HIP_Z = 0.4057
WAIST_Z = 0.5976
CHEST_Z = 0.9726
NECK_Z = 1.2235
HEAD_Z = 1.2414
ARM_Z = 1.1068

#: Every profile's crown sits here, and every head is the same width.
#:
#: A headgear part is ONE file worn by any base of its style: a cap modelled
#: for a crown at 2.15 slides off a head that ends at 1.94 and sinks into one
#: that ends at 2.30. So the head hangs DOWNWARD from a fixed crown and only
#: its height varies — which is what tells a chibi from an android anyway,
#: since a taller head simply swallows the neck. The KayKit bodies crown at
#: 2.17 with a wider skull, so their hats stay tagged `fantasy` and these
#: caps stay tagged to the styles built around this skull.
HEAD_TOP = 2.15
HEAD_W = 0.42
HEAD_D = 0.40
SHOULDER_X = 0.212
ELBOW_X = 0.4535
WRIST_X = 0.7132
HAND_X = 0.787
FINGER_X = 0.899
LEG_X = 0.1709
KNEE_Z = 0.2923
ANKLE_Z = 0.1452
TOE_Y = -0.0964
TOE_TIP_Y = -0.262

LANDMARKS = {
    "hips": HIP_Z,
    "spine": WAIST_Z,
    "chest": CHEST_Z,
    "head": HEAD_Z,
    "upper_arm_l": ARM_Z,
    "upper_leg_l": 0.5193,
    "lower_leg_l": KNEE_Z,
    "foot_l": ANKLE_Z,
}


@dataclass(frozen=True)
class Profile:
    """Everything one look decides. The rig decides the rest."""

    #: How far the head reaches DOWN from `HEAD_TOP`; the width is shared.
    head_h: float = 0.78
    eye_w: float = 0.08
    eye_h: float = 0.16
    #: Palette cell of the skull; a robot's head is metal, a person's is skin.
    head_cell: str = "skin"
    #: Cell of the torso, the legs and the shoes.
    torso_cell: str = "primary"
    leg_cell: str = "secondary"
    shoe_cell: str = "shoes"
    sleeve_cell: str = "primary"
    #: Forearms and hands: bare skin on a person, chassis on an android.
    limb_cell: str = "skin"
    belt_cell: str = "leather"
    #: Widths, as half-extents.
    torso_w: float = 0.30
    torso_d: float = 0.20
    arm_r: float = 0.105
    leg_r: float = 0.125
    # features
    hair: bool = True
    hair_long: bool = False
    hood: bool = False
    tie: bool = False
    visor: bool = False
    antenna: bool = False
    pocket: bool = False
    #: Cells that belong to the hidable hair primitive.
    hair_cells: tuple[str, ...] = field(default=("hair",))


PROFILES: dict[str, Profile] = {
    # An everyday person in a shirt: the plain silhouette every other look
    # departs from, and the one the "modern" style was missing entirely.
    "office": Profile(tie=True, sleeve_cell="primary", leg_cell="secondary"),
    # Same body, softer clothes: a hood behind the head reads at 20 px where a
    # drawstring does not.
    "casual": Profile(
        head_h=0.80,
        hair_long=True,
        hood=True,
        pocket=True,
        sleeve_cell="primary",
        leg_cell="secondary",
    ),
    # Kart-racer proportions: the head is nearly half the figure, the eyes are
    # a third of the face, and the body is a stub under it.
    "chibi": Profile(
        head_h=1.06,
        eye_w=0.13,
        eye_h=0.24,
        torso_w=0.27,
        arm_r=0.115,
        leg_r=0.135,
        hair=True,
    ),
    # No skin, no hair: a metal shell with a lit visor and a panelled chest.
    "android": Profile(
        head_h=0.70,
        head_cell="metal",
        torso_cell="metal",
        leg_cell="metal",
        sleeve_cell="metal",
        limb_cell="metal",
        belt_cell="primary",
        shoe_cell="primary_shade",
        hair=False,
        visor=True,
        antenna=True,
    ),
}


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


@dataclass
class Piece:
    """One box: where it sits, which bone carries it, which colour it takes."""

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


def mirrored(piece: Piece) -> Piece:
    """The same box on the other side: X negated, the bone's side swapped.

    The side lives in the LAST character of both names — a plain
    ``replace("_l", "_r")`` turns ``upper_leg_l`` into ``upper_reg_r``, and a
    vertex group named after a bone that does not exist weighs its vertices to
    nothing. The glTF exporter then invents a ``neutral_bone`` for them and the
    contract gate rejects the file, which is how this was caught.
    """
    if not piece.bone.endswith("_l") or not piece.name.endswith("L"):
        raise SystemExit(f"mirrored() wants a left-side piece, got {piece.name}/{piece.bone}")
    return Piece(
        piece.name[:-1] + "R",
        piece.bone[:-1] + "r",
        piece.cell,
        (-piece.hi[0], piece.lo[1], piece.lo[2]),
        (-piece.lo[0], piece.hi[1], piece.hi[2]),
    )


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
# the body, piece by piece
# ---------------------------------------------------------------------------


def head_pieces(p: Profile) -> list[Piece]:
    """Skull, face and hair — everything the `head` bone carries."""
    top = HEAD_TOP
    chin = HEAD_TOP - p.head_h
    w, d = HEAD_W, HEAD_D
    face = -d - 0.005  # a hair's breadth in front of the skull, never inside it
    eye_z = chin + p.head_h * 0.52
    pieces = [
        box("Head", "head", p.head_cell, (-w, w), (-d, d), (chin, top)),
        # The neck reaches from the collar up to the chin, however far that is:
        # a tall chibi head swallows it, a short android head leaves it showing.
        box(
            "Neck",
            "head",
            p.head_cell,
            (-0.13, 0.13),
            (-0.12, 0.12),
            (NECK_Z - 0.05, chin + 0.06),
        ),
    ]
    if p.visor:
        # One lit band instead of eyes: an android reads as a machine from the
        # silhouette down to 20 px, which two dots never manage.
        pieces.append(
            box(
                "Visor",
                "head",
                "emissive",
                (-w * 0.9, w * 0.9),
                (face - 0.03, -d + 0.02),
                (eye_z - 0.09, eye_z + 0.09),
            )
        )
        pieces.append(
            box(
                "VisorRim",
                "head",
                "accent",
                (-w * 0.95, w * 0.95),
                (face - 0.02, -d + 0.02),
                (eye_z + 0.09, eye_z + 0.13),
            )
        )
    else:
        for side, sign in (("L", 1.0), ("R", -1.0)):
            inner = sign * 0.09
            outer = sign * (0.09 + p.eye_w)
            xs = (min(inner, outer), max(inner, outer))
            pieces.append(
                box(
                    f"Eye{side}",
                    "head",
                    "eye_white",
                    xs,
                    (face - 0.02, -d + 0.02),
                    (eye_z, eye_z + p.eye_h),
                )
            )
            px = (xs[0] + 0.015, xs[1] - 0.015)
            pieces.append(
                box(
                    f"Pupil{side}",
                    "head",
                    "eyes",
                    px,
                    (face - 0.035, face - 0.005),
                    (eye_z + p.eye_h * 0.18, eye_z + p.eye_h * 0.78),
                )
            )
        pieces.append(
            box(
                "Nose",
                "head",
                "skin_shade",
                (-0.045, 0.045),
                (face - 0.04, -d + 0.02),
                (eye_z - 0.12, eye_z - 0.02),
            )
        )
        pieces.append(
            box(
                "Mouth",
                "head",
                "skin_shade",
                (-0.09, 0.09),
                (face - 0.02, -d + 0.02),
                (eye_z - 0.26, eye_z - 0.21),
            )
        )
    if p.antenna:
        pieces.append(
            box("Antenna", "head", "metal", (-0.025, 0.025), (-0.025, 0.025), (top, top + 0.16))
        )
        pieces.append(
            box(
                "AntennaTip",
                "head",
                "emissive",
                (-0.05, 0.05),
                (-0.05, 0.05),
                (top + 0.16, top + 0.24),
            )
        )
    if p.hair:
        cap = w + 0.02
        pieces.append(
            box(
                "HairCap",
                "head",
                "hair",
                (-cap, cap),
                (-d - 0.02, d + 0.02),
                (top - 0.14, top + 0.03),
            )
        )
        pieces.append(
            box("HairBack", "head", "hair", (-cap, cap), (d - 0.04, d + 0.03), (chin + 0.16, top))
        )
        if p.hair_long:
            pieces.append(
                box(
                    "HairSideL",
                    "head",
                    "hair",
                    (w - 0.02, cap),
                    (-d * 0.4, d + 0.03),
                    (chin + 0.05, top),
                )
            )
            pieces.append(
                box(
                    "HairSideR",
                    "head",
                    "hair",
                    (-cap, -w + 0.02),
                    (-d * 0.4, d + 0.03),
                    (chin + 0.05, top),
                )
            )
    if p.hood:
        # The hood rides the chest, not the head: it stays put when the head turns.
        pieces.append(
            box(
                "Hood",
                "chest",
                "primary_shade",
                (-w - 0.04, w + 0.04),
                (d - 0.01, d + 0.10),
                (chin - 0.04, chin + p.head_h * 0.62),
            )
        )
    return pieces


def torso_pieces(p: Profile) -> list[Piece]:
    w, d = p.torso_w, p.torso_d
    pieces = [
        box("Chest", "chest", p.torso_cell, (-w, w), (-d, d), (CHEST_Z - 0.02, NECK_Z + 0.03)),
        box(
            "Waist",
            "spine",
            p.torso_cell,
            (-w + 0.02, w - 0.02),
            (-d + 0.01, d - 0.01),
            (WAIST_Z + 0.02, CHEST_Z + 0.01),
        ),
        box("Belt", "hips", p.belt_cell, (-w, w), (-d, d), (WAIST_Z - 0.05, WAIST_Z + 0.04)),
        box(
            "Pelvis",
            "hips",
            p.leg_cell,
            (-w + 0.01, w - 0.01),
            (-d + 0.01, d - 0.01),
            (HIP_Z - 0.02, WAIST_Z - 0.03),
        ),
    ]
    if p.tie:
        pieces.append(
            box(
                "Collar",
                "chest",
                "secondary",
                (-0.19, 0.19),
                (-d - 0.02, -d + 0.05),
                (NECK_Z - 0.10, NECK_Z + 0.03),
            )
        )
        pieces.append(
            box(
                "Tie",
                "chest",
                "accent",
                (-0.05, 0.05),
                (-d - 0.025, -d + 0.02),
                (CHEST_Z - 0.02, NECK_Z - 0.08),
            )
        )
    if p.pocket:
        pieces.append(
            box(
                "Pocket",
                "spine",
                "primary_shade",
                (-0.16, 0.16),
                (-d - 0.02, -d + 0.03),
                (WAIST_Z + 0.06, WAIST_Z + 0.20),
            )
        )
    if p.visor:
        pieces.append(
            box(
                "CoreLight",
                "chest",
                "emissive",
                (-0.07, 0.07),
                (-d - 0.02, -d + 0.02),
                (CHEST_Z + 0.10, CHEST_Z + 0.24),
            )
        )
        pieces.append(
            box(
                "PanelL",
                "chest",
                "primary",
                (0.12, w - 0.01),
                (-d - 0.01, -d + 0.03),
                (CHEST_Z + 0.02, NECK_Z - 0.02),
            )
        )
        pieces.append(
            box(
                "PanelR",
                "chest",
                "primary",
                (-w + 0.01, -0.12),
                (-d - 0.01, -d + 0.03),
                (CHEST_Z + 0.02, NECK_Z - 0.02),
            )
        )
    return pieces


def limb_pieces(p: Profile) -> list[Piece]:
    """One side's arm and leg; the other side is the mirror of these."""
    r = p.arm_r
    lr = p.leg_r
    left = [
        box(
            "UpperArmL",
            "upper_arm_l",
            p.sleeve_cell,
            (SHOULDER_X - 0.02, ELBOW_X),
            (-r, r),
            (ARM_Z - r, ARM_Z + r),
        ),
        box(
            "LowerArmL",
            "lower_arm_l",
            p.limb_cell,
            (ELBOW_X, WRIST_X),
            (-r + 0.01, r - 0.01),
            (ARM_Z - r + 0.01, ARM_Z + r - 0.01),
        ),
        box(
            "HandL",
            "hand_l",
            p.limb_cell,
            (HAND_X - 0.02, FINGER_X),
            (-r, r),
            (ARM_Z - r, ARM_Z + r),
        ),
        box(
            "UpperLegL",
            "upper_leg_l",
            p.leg_cell,
            (LEG_X - lr, LEG_X + lr),
            (-lr - 0.02, lr + 0.01),
            (KNEE_Z - 0.01, 0.5193 + 0.02),
        ),
        box(
            "LowerLegL",
            "lower_leg_l",
            p.leg_cell,
            (LEG_X - lr + 0.01, LEG_X + lr - 0.01),
            (-lr, lr),
            (ANKLE_Z - 0.01, KNEE_Z + 0.01),
        ),
        box(
            "FootL",
            "foot_l",
            p.shoe_cell,
            (LEG_X - lr, LEG_X + lr),
            (TOE_Y, lr + 0.01),
            (GROUND, ANKLE_Z + 0.02),
        ),
        box(
            "ToeL",
            "toes_l",
            p.shoe_cell,
            (LEG_X - lr, LEG_X + lr),
            (TOE_TIP_Y, TOE_Y + 0.01),
            (GROUND, ANKLE_Z - 0.05),
        ),
    ]
    return left + [mirrored(piece) for piece in left]


def body_pieces(profile: Profile) -> list[Piece]:
    return head_pieces(profile) + torso_pieces(profile) + limb_pieces(profile)


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def verify_rig(arm: bpy.types.Object) -> None:
    """The boxes are placed against the donor's rest pose; prove it is that pose."""
    problems = []
    for bone, z in LANDMARKS.items():
        found = arm.data.bones.get(bone)
        if found is None:
            problems.append(f"bone {bone!r} missing")
        elif abs(found.head_local.z - z) > 0.02:
            problems.append(f"{bone}: rest z {found.head_local.z:.4f} != expected {z:.4f}")
    if problems:
        raise SystemExit(
            "humanoid_builder: the donor rig moved — the box layout is measured "
            "against it and would deform: " + "; ".join(problems)
        )


def build_humanoid(
    profile_name: str, arm: bpy.types.Object, sheet: dict, img, sheet_material
) -> tuple[bpy.types.Object, dict]:
    """Build one body onto `arm` and return it joined, UV'd and rigged."""
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise SystemExit(f"unknown humanoid profile {profile_name!r}")
    verify_rig(arm)

    pieces = body_pieces(profile)
    # A vertex group named after a bone that does not exist weighs its box to
    # nothing, and the exporter quietly parks those vertices on an invented
    # `neutral_bone`. Catch the typo here, where the message can name it.
    bones = {b.name for b in arm.data.bones}
    stray = sorted({p.bone for p in pieces} - bones)
    if stray:
        raise SystemExit(f"humanoid_builder: pieces bound to bones the rig has not: {stray}")

    cells = sheet["cells"]
    cv = 1.0 - (sheet["cell_height"] / 2) / sheet["size"]
    objects: list[bpy.types.Object] = []
    hair_flags: list[bool] = []
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
        objects.append(obj)
        hair_flags.append(piece.cell in profile.hair_cells)

    # One material slot for now; `apply_materials` re-slots the hair faces
    # afterwards, exactly as it does for an imported body.
    for obj in objects:
        obj.data.materials.append(sheet_material("tmp-sheet", img))
    for obj in bpy.data.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    faces_per_object = [len(obj.data.polygons) for obj in objects]
    bpy.ops.object.join()
    body = bpy.context.view_layer.objects.active
    body.name = "Body"
    body.data.name = "Body"
    while len(body.data.materials) > 1:
        body.data.materials.pop(index=len(body.data.materials) - 1)

    # `join` appends meshes in selection order with the active object FIRST,
    # so face indices are the active object's, then the rest in list order.
    order = [0] + [i for i in range(1, len(objects))]
    hair_faces: list[int] = []
    cursor = 0
    for i in order:
        count = faces_per_object[i]
        if hair_flags[i]:
            hair_faces.extend(range(cursor, cursor + count))
        cursor += count

    body.parent = arm
    body.matrix_parent_inverse.identity()
    modifier = body.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm
    return body, {"used": used, "unmapped": {}, "hair_faces": hair_faces}


# ---------------------------------------------------------------------------
# parts
# ---------------------------------------------------------------------------

#: Handslot rest positions, from the same donor dump as the landmarks above.
HANDSLOT_L = (0.8831, 0.0, 1.0493)
HANDSLOT_R = (-0.8831, 0.0, 1.0493)

#: Parts modelled here, keyed by the catalog id the creator offers.
#:
#: Every one of these hangs on `chest` or a handslot — bones the donor rig
#: places identically for every biped it produces — so one file fits every
#: base of every style. `headgear-cap` is the exception that proves it: it is
#: cut for the crown and width THIS module builds (HEAD_TOP/HEAD_W), so it is
#: tagged only to the styles built on that skull, never to the wider KayKit
#: heads the fantasy hats were cut for.
PART_SPECS: dict[str, dict] = {
    "back-backpack": {
        "slot": "back",
        "label": "Backpack",
        "styles": ["modern", "cartoon", "scifi"],
        "pieces": lambda: [
            box("Pack", "chest", "leather", (-0.24, 0.24), (0.20, 0.42), (CHEST_Z - 0.06, NECK_Z)),
            box(
                "PackLid",
                "chest",
                "secondary",
                (-0.25, 0.25),
                (0.19, 0.43),
                (NECK_Z - 0.14, NECK_Z - 0.02),
            ),
            box(
                "StrapL",
                "chest",
                "leather",
                (0.10, 0.19),
                (-0.23, 0.21),
                (NECK_Z - 0.06, NECK_Z + 0.01),
            ),
            box(
                "StrapR",
                "chest",
                "leather",
                (-0.19, -0.10),
                (-0.23, 0.21),
                (NECK_Z - 0.06, NECK_Z + 0.01),
            ),
        ],
    },
    # Placed like the pack's own spellbook: hanging FROM the slot and reaching
    # forward, not standing on top of it. The rest pose holds the arms out
    # sideways, so a prop built "upright" at rest lies flat the moment the
    # idle clip brings the arm down — the pack's props are authored around
    # that and this one copies their footprint.
    "hand_l-tablet": {
        "slot": "hand_l",
        "label": "Tablet",
        "styles": ["modern", "scifi"],
        "pieces": lambda: [
            box(
                "TabletShell",
                "handslot_l",
                "metal",
                (HANDSLOT_L[0] - 0.04, HANDSLOT_L[0] + 0.04),
                (-0.46, -0.08),
                (HANDSLOT_L[2] - 0.27, HANDSLOT_L[2] + 0.25),
            ),
            box(
                "TabletScreen",
                "handslot_l",
                "emissive",
                (HANDSLOT_L[0] + 0.04, HANDSLOT_L[0] + 0.05),
                (-0.43, -0.11),
                (HANDSLOT_L[2] - 0.24, HANDSLOT_L[2] + 0.22),
            ),
        ],
    },
    "hand_r-wrench": {
        "slot": "hand_r",
        "label": "Wrench",
        "styles": ["modern", "cartoon", "scifi"],
        "pieces": lambda: [
            box(
                "WrenchShaft",
                "handslot_r",
                "metal",
                (HANDSLOT_R[0] - 0.035, HANDSLOT_R[0] + 0.035),
                (-0.30, 0.06),
                (HANDSLOT_R[2] - 0.03, HANDSLOT_R[2] + 0.03),
            ),
            box(
                "WrenchJaw",
                "handslot_r",
                "secondary_shade",
                (HANDSLOT_R[0] - 0.045, HANDSLOT_R[0] + 0.045),
                (-0.38, -0.28),
                (HANDSLOT_R[2] - 0.07, HANDSLOT_R[2] + 0.07),
            ),
        ],
    },
    "headgear-cap": {
        "slot": "headgear",
        "label": "Cap",
        "styles": ["modern", "cartoon", "scifi"],
        "hides": ["hair"],
        # A cap has to sit ON the skull, not balance on it: the crown wraps
        # 30 cm down the sides (it hides the hair, so what it swallows is
        # never seen) and is a touch wider than the head, and the peak hangs
        # from the crown's lower front rather than sticking out of its top.
        "pieces": lambda: [
            box(
                "CapCrown",
                "head",
                "primary",
                (-HEAD_W - 0.035, HEAD_W + 0.035),
                (-HEAD_D - 0.035, HEAD_D + 0.035),
                (HEAD_TOP - 0.30, HEAD_TOP + 0.02),
            ),
            box(
                "CapDome",
                "head",
                "primary",
                (-HEAD_W * 0.82, HEAD_W * 0.82),
                (-HEAD_D * 0.82, HEAD_D * 0.82),
                (HEAD_TOP + 0.02, HEAD_TOP + 0.07),
            ),
            box(
                "CapPeak",
                "head",
                "primary_shade",
                (-HEAD_W * 0.78, HEAD_W * 0.78),
                (-HEAD_D - 0.27, -HEAD_D - 0.02),
                (HEAD_TOP - 0.28, HEAD_TOP - 0.23),
            ),
            box(
                "CapButton",
                "head",
                "accent",
                (-0.045, 0.045),
                (-0.045, 0.045),
                (HEAD_TOP + 0.07, HEAD_TOP + 0.11),
            ),
        ],
    },
}


def build_part(part_id: str, arm: bpy.types.Object, sheet: dict, img, sheet_material):
    """Build one accessory: boxes, UVs on the strip, weight 1 on its attach bone."""
    spec = PART_SPECS.get(part_id)
    if spec is None:
        raise SystemExit(f"unknown procedural part {part_id!r}")
    pieces = spec["pieces"]()
    bones = {b.name for b in arm.data.bones}
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
