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
    """Everything one look decides. The rig decides the rest.

    Adding a look is adding an entry to `PROFILES` — no geometry, no export
    step, ~45 KB of file, because the clips live in the shared library.
    """

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
    #: One multiplier on every width: a trooper is not a scientist.
    bulk: float = 1.0
    # --- head ---
    hair: bool = True
    hair_long: bool = False
    #: A beard is NOT part of the hidable hair primitive — a helmet covers the
    #: skull, not the chin.
    beard: bool = False
    pointed_ears: bool = False
    goggles: bool = False
    headband: bool = False
    visor: bool = False
    antenna: bool = False
    # --- torso ---
    hood: bool = False
    tie: bool = False
    pocket: bool = False
    #: The bib and straps of a pair of overalls.
    bib: bool = False
    #: A work apron down the front.
    apron: bool = False
    #: Two long panels down the sides — a lab coat, a duster.
    coat: bool = False
    #: A ring of cloth at the neck.
    scarf: bool = False
    #: Armour pads over the shoulders.
    shoulders: bool = False
    # --- limbs ---
    #: Bare arms from the shoulder down.
    sleeveless: bool = False
    #: Hands in their own cell instead of bare.
    glove_cell: str | None = None
    #: Boots climb the shin; shoes stop at the ankle.
    boots: bool = False
    #: One skirt from the hips down instead of two trouser legs.
    robe: bool = False
    #: Cells that belong to the hidable hair primitive.
    hair_cells: tuple[str, ...] = field(default=("hair",))


PROFILES: dict[str, Profile] = {
    # ---- modern -------------------------------------------------------------
    # An everyday person in a shirt: the plain silhouette every other look
    # departs from, and the one the "modern" style was missing entirely.
    "office": Profile(tie=True),
    # Same body, softer clothes: a hood behind the head reads at 20 px where a
    # drawstring does not.
    "casual": Profile(head_h=0.80, hair_long=True, hood=True, pocket=True),
    # Overalls over a work shirt, gloves and boots.
    "worker": Profile(
        head_h=0.76,
        bulk=1.12,
        bib=True,
        beard=True,
        boots=True,
        glove_cell="leather",
        leg_cell="secondary",
    ),
    # Bare arms, shorts, a headband: the silhouette does the talking.
    "athlete": Profile(
        head_h=0.76,
        bulk=0.92,
        sleeveless=True,
        headband=True,
        shoe_cell="secondary",
    ),
    # A long coat over the shirt, gloves, short hair.
    "medic": Profile(coat=True, glove_cell="secondary", tie=False, pocket=True),
    # ---- cartoon ------------------------------------------------------------
    # Kart-racer proportions: the head is nearly half the figure, the eyes are
    # a third of the face, and the body is a stub under it.
    "chibi": Profile(head_h=1.06, eye_w=0.13, eye_h=0.24, torso_w=0.27, bulk=1.1),
    # Rubber-hose cartoon: enormous eyes, white gloves, boots too big for it.
    "toon": Profile(
        head_h=1.14,
        eye_w=0.16,
        eye_h=0.30,
        bulk=0.95,
        glove_cell="secondary",
        boots=True,
        shoe_cell="shoes",
    ),
    # All corners and no neck; two dots for eyes.
    "blockhead": Profile(
        head_h=0.92,
        eye_w=0.05,
        eye_h=0.07,
        bulk=1.3,
        torso_w=0.33,
        torso_d=0.23,
        hair=False,
        scarf=True,
    ),
    # ---- sci-fi -------------------------------------------------------------
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
    # A flight suit under a visored helmet, with shoulder rigs and boots.
    "pilot": Profile(
        head_h=0.74,
        head_cell="secondary",
        visor=True,
        hair=False,
        shoulders=True,
        boots=True,
        glove_cell="leather",
        belt_cell="accent",
        shoe_cell="primary_shade",
    ),
    # Heavy plate: wide shoulders, a slab of chest, nothing human showing.
    "trooper": Profile(
        head_h=0.72,
        bulk=1.32,
        torso_w=0.33,
        torso_d=0.22,
        head_cell="secondary",
        torso_cell="secondary",
        leg_cell="secondary_shade",
        sleeve_cell="secondary",
        limb_cell="secondary_shade",
        glove_cell="metal",
        shoe_cell="metal",
        belt_cell="metal",
        hair=False,
        visor=True,
        shoulders=True,
        boots=True,
    ),
    # A lab coat and goggles pushed up on the forehead.
    "scientist": Profile(
        bulk=0.95,
        coat=True,
        goggles=True,
        glove_cell="secondary",
        leg_cell="secondary_shade",
    ),
    # ---- fantasy ------------------------------------------------------------
    # Short, broad and mostly beard.
    "dwarf": Profile(
        head_h=0.98,
        bulk=1.28,
        torso_w=0.31,
        beard=True,
        boots=True,
        belt_cell="leather",
        leg_cell="secondary",
    ),
    # Tall, slim, pointed ears and long hair.
    "elf": Profile(
        head_h=0.72,
        bulk=0.88,
        pointed_ears=True,
        hair_long=True,
        boots=True,
        leg_cell="secondary",
    ),
    # A hooded robe and nothing else to look at.
    "monk": Profile(
        head_h=0.76,
        hood=True,
        robe=True,
        hair=False,
        glove_cell=None,
        belt_cell="accent",
    ),
}


#: Half-extents a wrapping garment is cut to, per size class. Each clears the
#: widest torso in its class: slim covers up to bulk 1.05, broad up to 1.35.
#: A body picks its class from its own bulk (`Profile.fit_size`), and the
#: creator only ever offers the matching cut.
WRAP_SIZES: dict[str, tuple[float, float]] = {
    "slim": (0.335, 0.225),
    "broad": (0.450, 0.310),
}

#: Above this bulk a body wears the broad cut.
BROAD_FROM = 1.06


def fit_size(profile: Profile) -> str:
    """Which cut of a wrapping garment this body takes."""
    return "broad" if profile.bulk >= BROAD_FROM else "slim"


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
        # One lit band instead of eyes: a machine reads as a machine from the
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
        if not p.beard:
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
    if p.beard:
        # A CHIN beard: narrower than the face and starting below the mouth.
        # Run from under the eyes and across the full width, as it first was,
        # and it reads as a bar taped over the face instead of a beard.
        # Deliberately NOT in `hair_cells`: a helmet hides the skull, and a
        # beard under one still shows.
        pieces.append(
            box(
                "Beard",
                "head",
                "hair",
                (-w * 0.5, w * 0.5),
                (face - 0.025, d * 0.2),
                (chin - 0.13, eye_z - 0.20),
            )
        )
        pieces.append(
            box(
                "Moustache",
                "head",
                "hair",
                (-w * 0.30, w * 0.30),
                (face - 0.03, -d + 0.02),
                (eye_z - 0.22, eye_z - 0.16),
            )
        )
    if p.pointed_ears:
        for side, sx in (("L", 1.0), ("R", -1.0)):
            xs = sorted((sx * w, sx * (w + 0.075)))
            pieces.append(
                box(
                    f"Ear{side}",
                    "head",
                    p.head_cell,
                    xs,
                    (-d * 0.25, d * 0.3),
                    (eye_z - 0.02, eye_z + 0.20),
                )
            )
    if p.goggles:
        # Pushed up on the forehead, where a scientist actually keeps them.
        pieces.append(
            box(
                "GoggleBand",
                "head",
                "leather",
                (-w - 0.02, w + 0.02),
                (-d - 0.03, d + 0.03),
                (top - 0.24, top - 0.16),
            )
        )
        for side, sx in (("L", 1.0), ("R", -1.0)):
            xs = sorted((sx * 0.05, sx * 0.21))
            pieces.append(
                box(
                    f"Lens{side}",
                    "head",
                    "accent",
                    xs,
                    (-d - 0.05, -d + 0.01),
                    (top - 0.26, top - 0.14),
                )
            )
    if p.headband:
        pieces.append(
            box(
                "Headband",
                "head",
                "accent",
                (-w - 0.02, w + 0.02),
                (-d - 0.02, d + 0.02),
                (top - 0.26, top - 0.18),
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
        # Full width down to the ears, then a narrower nape: two steps is
        # enough shape for the back of a head to stop being a blank rectangle.
        pieces.append(
            box(
                "HairBack",
                "head",
                "hair",
                (-cap, cap),
                (d - 0.04, d + 0.03),
                (chin + p.head_h * 0.42, top),
            )
        )
        pieces.append(
            box(
                "HairNape",
                "head",
                "hair",
                (-cap * 0.62, cap * 0.62),
                (d - 0.03, d + 0.025),
                (chin + p.head_h * 0.18, chin + p.head_h * 0.44),
            )
        )
        if p.hair_long:
            for side, lo, hi in (("L", w - 0.02, cap), ("R", -cap, -w + 0.02)):
                pieces.append(
                    box(
                        f"HairSide{side}",
                        "head",
                        "hair",
                        (lo, hi),
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
    w, d = p.torso_w * p.bulk, p.torso_d * p.bulk
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
    if p.bib:
        # Overalls: a panel up the front and two straps over the shoulders.
        pieces.append(
            box(
                "Bib",
                "chest",
                p.leg_cell,
                (-w * 0.62, w * 0.62),
                (-d - 0.02, -d + 0.04),
                (CHEST_Z - 0.02, NECK_Z - 0.05),
            )
        )
        for side, sx in (("L", 1.0), ("R", -1.0)):
            xs = sorted((sx * w * 0.30, sx * w * 0.62))
            pieces.append(
                box(
                    f"Strap{side}",
                    "chest",
                    p.leg_cell,
                    xs,
                    (-d - 0.02, d + 0.02),
                    (NECK_Z - 0.07, NECK_Z + 0.02),
                )
            )
    if p.apron:
        pieces.append(
            box(
                "Apron",
                "spine",
                "secondary",
                (-w * 0.8, w * 0.8),
                (-d - 0.03, -d + 0.01),
                (HIP_Z - 0.16, CHEST_Z),
            )
        )
    if p.coat:
        # Two long panels, one per side, so the legs still read between them.
        for side, sx in (("L", 1.0), ("R", -1.0)):
            xs = sorted((sx * (w - 0.02), sx * (w + 0.035)))
            pieces.append(
                box(
                    f"Coat{side}",
                    "chest",
                    "secondary",
                    xs,
                    (-d - 0.02, d + 0.02),
                    (KNEE_Z + 0.04, NECK_Z + 0.02),
                )
            )
        pieces.append(
            box(
                "CoatBack",
                "chest",
                "secondary",
                (-w - 0.03, w + 0.03),
                (d - 0.01, d + 0.035),
                (KNEE_Z + 0.04, NECK_Z + 0.02),
            )
        )
    if p.scarf:
        pieces.append(
            box(
                "Scarf",
                "chest",
                "accent",
                (-0.20, 0.20),
                (-d - 0.03, d + 0.03),
                (NECK_Z - 0.04, NECK_Z + 0.06),
            )
        )
    if p.shoulders:
        for side, sx in (("L", 1.0), ("R", -1.0)):
            xs = sorted((sx * (w - 0.04), sx * (w + 0.10)))
            pieces.append(
                box(
                    f"Pad{side}",
                    "chest",
                    "metal",
                    xs,
                    (-d - 0.01, d + 0.01),
                    (NECK_Z - 0.13, NECK_Z + 0.05),
                )
            )
    if p.visor and not p.shoulders:
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
        for side, sx in (("L", 1.0), ("R", -1.0)):
            xs = sorted((sx * 0.12, sx * (w - 0.01)))
            pieces.append(
                box(
                    f"Panel{side}",
                    "chest",
                    "primary",
                    xs,
                    (-d - 0.01, -d + 0.03),
                    (CHEST_Z + 0.02, NECK_Z - 0.02),
                )
            )
    if p.robe:
        # One skirt on the hips: it does not bend with the knees, which is
        # exactly how a robe hangs.
        pieces.append(
            box(
                "Robe",
                "hips",
                p.torso_cell,
                (-w - 0.03, w + 0.03),
                (-d - 0.02, d + 0.02),
                (ANKLE_Z + 0.02, WAIST_Z + 0.02),
            )
        )
    return pieces


def limb_pieces(p: Profile) -> list[Piece]:
    """One side's arm and leg; the other side is the mirror of these."""
    r = p.arm_r * p.bulk
    lr = p.leg_r * p.bulk
    upper_arm_cell = p.limb_cell if p.sleeveless else p.sleeve_cell
    hand_cell = p.glove_cell or p.limb_cell
    boot_top = ANKLE_Z + 0.13 if p.boots else ANKLE_Z + 0.02
    left = [
        box(
            "UpperArmL",
            "upper_arm_l",
            upper_arm_cell,
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
            hand_cell,
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
            (GROUND, boot_top),
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
    # ---- headgear -----------------------------------------------------------
    "headgear-beanie": {
        "slot": "headgear",
        "fits_family": "jarvis-biped",
        "label": "Beanie",
        "styles": ["modern", "cartoon", "fantasy"],
        "hides": ["hair"],
        "pieces": lambda: [
            box(
                "BeanieBody",
                "head",
                "primary",
                (-HEAD_W - 0.03, HEAD_W + 0.03),
                (-HEAD_D - 0.03, HEAD_D + 0.03),
                (HEAD_TOP - 0.32, HEAD_TOP + 0.03),
            ),
            box(
                "BeanieBrim",
                "head",
                "primary_shade",
                (-HEAD_W - 0.045, HEAD_W + 0.045),
                (-HEAD_D - 0.045, HEAD_D + 0.045),
                (HEAD_TOP - 0.36, HEAD_TOP - 0.28),
            ),
            box(
                "BeanieBobble",
                "head",
                "accent",
                (-0.06, 0.06),
                (-0.06, 0.06),
                (HEAD_TOP + 0.03, HEAD_TOP + 0.12),
            ),
        ],
    },
    "headgear-hardhat": {
        "slot": "headgear",
        "fits_family": "jarvis-biped",
        "label": "Hard hat",
        "styles": ["modern", "scifi"],
        "hides": ["hair"],
        "pieces": lambda: [
            box(
                "HatShell",
                "head",
                "accent",
                (-HEAD_W - 0.04, HEAD_W + 0.04),
                (-HEAD_D - 0.03, HEAD_D + 0.03),
                (HEAD_TOP - 0.24, HEAD_TOP + 0.05),
            ),
            box(
                "HatBrim",
                "head",
                "accent",
                (-HEAD_W - 0.06, HEAD_W + 0.06),
                (-HEAD_D - 0.14, HEAD_D + 0.06),
                (HEAD_TOP - 0.26, HEAD_TOP - 0.21),
            ),
            box(
                "HatRidge",
                "head",
                "primary_shade",
                (-0.05, 0.05),
                (-HEAD_D - 0.03, HEAD_D + 0.03),
                (HEAD_TOP + 0.05, HEAD_TOP + 0.10),
            ),
        ],
    },
    "headgear-headset": {
        "slot": "headgear",
        "fits_family": "jarvis-biped",
        "label": "Headset",
        "styles": ["modern", "scifi", "cartoon"],
        "pieces": lambda: [
            box(
                "Band",
                "head",
                "primary_shade",
                (-HEAD_W - 0.03, HEAD_W + 0.03),
                (-0.05, 0.05),
                (HEAD_TOP - 0.06, HEAD_TOP + 0.05),
            ),
            box(
                "CupL",
                "head",
                "metal",
                (HEAD_W - 0.01, HEAD_W + 0.06),
                (-0.12, 0.12),
                (HEAD_TOP - 0.30, HEAD_TOP - 0.08),
            ),
            box(
                "CupR",
                "head",
                "metal",
                (-HEAD_W - 0.06, -HEAD_W + 0.01),
                (-0.12, 0.12),
                (HEAD_TOP - 0.30, HEAD_TOP - 0.08),
            ),
            box(
                "Mic",
                "head",
                "primary_shade",
                (HEAD_W * 0.30, HEAD_W + 0.02),
                (-HEAD_D - 0.10, -HEAD_D + 0.02),
                (HEAD_TOP - 0.34, HEAD_TOP - 0.30),
            ),
        ],
    },
    "headgear-crown": {
        "slot": "headgear",
        "fits_family": "jarvis-biped",
        "label": "Crown",
        "styles": ["fantasy", "cartoon"],
        "pieces": lambda: [
            box(
                "CrownBand",
                "head",
                "accent",
                (-HEAD_W - 0.03, HEAD_W + 0.03),
                (-HEAD_D - 0.03, HEAD_D + 0.03),
                (HEAD_TOP - 0.14, HEAD_TOP + 0.01),
            ),
            box(
                "SpikeC",
                "head",
                "accent",
                (-0.05, 0.05),
                (-HEAD_D - 0.03, -HEAD_D + 0.02),
                (HEAD_TOP + 0.01, HEAD_TOP + 0.11),
            ),
            box(
                "SpikeL",
                "head",
                "accent",
                (HEAD_W - 0.10, HEAD_W - 0.01),
                (-HEAD_D - 0.03, -HEAD_D + 0.02),
                (HEAD_TOP + 0.01, HEAD_TOP + 0.08),
            ),
            box(
                "SpikeR",
                "head",
                "accent",
                (-HEAD_W + 0.01, -HEAD_W + 0.10),
                (-HEAD_D - 0.03, -HEAD_D + 0.02),
                (HEAD_TOP + 0.01, HEAD_TOP + 0.08),
            ),
            box(
                "Gem",
                "head",
                "emissive",
                (-0.035, 0.035),
                (-HEAD_D - 0.04, -HEAD_D - 0.02),
                (HEAD_TOP - 0.11, HEAD_TOP - 0.04),
            ),
        ],
    },
    "headgear-space-helmet": {
        "slot": "headgear",
        "fits_family": "jarvis-biped",
        "label": "Space helmet",
        "styles": ["scifi"],
        "hides": ["hair"],
        "pieces": lambda: [
            box(
                "HelmShell",
                "head",
                "secondary",
                (-HEAD_W - 0.06, HEAD_W + 0.06),
                (-HEAD_D - 0.05, HEAD_D + 0.06),
                (HEAD_TOP - HEAD_D * 1.9, HEAD_TOP + 0.06),
            ),
            box(
                "HelmGlass",
                "head",
                "emissive",
                (-HEAD_W * 0.85, HEAD_W * 0.85),
                (-HEAD_D - 0.075, -HEAD_D - 0.04),
                (HEAD_TOP - 0.52, HEAD_TOP - 0.16),
            ),
            box(
                "HelmCollar",
                "head",
                "metal",
                (-HEAD_W - 0.07, HEAD_W + 0.07),
                (-HEAD_D - 0.06, HEAD_D + 0.07),
                (HEAD_TOP - HEAD_D * 2.0, HEAD_TOP - HEAD_D * 1.85),
            ),
        ],
    },
    "headgear-top-hat": {
        "slot": "headgear",
        "fits_family": "jarvis-biped",
        "label": "Top hat",
        "styles": ["cartoon", "modern"],
        "hides": ["hair"],
        "pieces": lambda: [
            box(
                "HatBrim",
                "head",
                "primary",
                (-HEAD_W - 0.16, HEAD_W + 0.16),
                (-HEAD_D - 0.16, HEAD_D + 0.16),
                (HEAD_TOP - 0.06, HEAD_TOP - 0.01),
            ),
            box(
                "HatTube",
                "head",
                "primary",
                (-HEAD_W - 0.01, HEAD_W + 0.01),
                (-HEAD_D - 0.01, HEAD_D + 0.01),
                (HEAD_TOP - 0.06, HEAD_TOP + 0.34),
            ),
            box(
                "HatBand",
                "head",
                "accent",
                (-HEAD_W - 0.02, HEAD_W + 0.02),
                (-HEAD_D - 0.02, HEAD_D + 0.02),
                (HEAD_TOP + 0.01, HEAD_TOP + 0.08),
            ),
        ],
    },
    # ---- face ---------------------------------------------------------------
    "face_extra-glasses": {
        "slot": "face_extra",
        "fits_family": "jarvis-biped",
        "label": "Glasses",
        "styles": ["modern", "scifi", "cartoon", "fantasy"],
        "pieces": lambda: [
            box(
                "LensL",
                "head",
                "metal",
                (0.055, 0.215),
                (-HEAD_D - 0.045, -HEAD_D - 0.015),
                (HEAD_TOP - 0.52, HEAD_TOP - 0.38),
            ),
            box(
                "LensR",
                "head",
                "metal",
                (-0.215, -0.055),
                (-HEAD_D - 0.045, -HEAD_D - 0.015),
                (HEAD_TOP - 0.52, HEAD_TOP - 0.38),
            ),
            box(
                "Bridge",
                "head",
                "metal",
                (-0.055, 0.055),
                (-HEAD_D - 0.04, -HEAD_D - 0.02),
                (HEAD_TOP - 0.47, HEAD_TOP - 0.43),
            ),
            box(
                "TempleL",
                "head",
                "metal",
                (HEAD_W - 0.02, HEAD_W + 0.02),
                (-HEAD_D - 0.02, 0.0),
                (HEAD_TOP - 0.47, HEAD_TOP - 0.43),
            ),
            box(
                "TempleR",
                "head",
                "metal",
                (-HEAD_W - 0.02, -HEAD_W + 0.02),
                (-HEAD_D - 0.02, 0.0),
                (HEAD_TOP - 0.47, HEAD_TOP - 0.43),
            ),
        ],
    },
    "face_extra-shades": {
        "slot": "face_extra",
        "fits_family": "jarvis-biped",
        "label": "Shades",
        "styles": ["modern", "cartoon", "scifi", "fantasy"],
        "pieces": lambda: [
            box(
                "ShadeBar",
                "head",
                "eyes",
                (-HEAD_W - 0.01, HEAD_W + 0.01),
                (-HEAD_D - 0.05, -HEAD_D - 0.02),
                (HEAD_TOP - 0.52, HEAD_TOP - 0.37),
            ),
            box(
                "ShadeGlint",
                "head",
                "secondary",
                (0.10, 0.20),
                (-HEAD_D - 0.055, -HEAD_D - 0.045),
                (HEAD_TOP - 0.49, HEAD_TOP - 0.44),
            ),
        ],
    },
    "face_extra-respirator": {
        "slot": "face_extra",
        "fits_family": "jarvis-biped",
        "label": "Respirator",
        "styles": ["scifi", "modern"],
        "pieces": lambda: [
            box(
                "MaskBody",
                "head",
                "metal",
                (-HEAD_W * 0.7, HEAD_W * 0.7),
                (-HEAD_D - 0.07, -HEAD_D + 0.02),
                (HEAD_TOP - 0.72, HEAD_TOP - 0.50),
            ),
            box(
                "MaskFilter",
                "head",
                "accent",
                (-0.07, 0.07),
                (-HEAD_D - 0.12, -HEAD_D - 0.06),
                (HEAD_TOP - 0.68, HEAD_TOP - 0.56),
            ),
            box(
                "MaskStrap",
                "head",
                "leather",
                (-HEAD_W - 0.02, HEAD_W + 0.02),
                (-HEAD_D - 0.01, HEAD_D + 0.02),
                (HEAD_TOP - 0.66, HEAD_TOP - 0.58),
            ),
        ],
    },
    # ---- over the torso -----------------------------------------------------
    "torso_over-vest": {
        "slot": "torso_over",
        "covers": ["sides"],
        "label": "Vest",
        "styles": ["modern", "fantasy", "scifi", "cartoon"],
        "fits_family": "jarvis-biped",
        "wrapping": True,
        "pieces": lambda w, d: [
            box(
                "VestL", "chest", "leather", (w * 0.42, w), (-d, d), (WAIST_Z + 0.06, NECK_Z + 0.02)
            ),
            box(
                "VestR",
                "chest",
                "leather",
                (-w, -w * 0.42),
                (-d, d),
                (WAIST_Z + 0.06, NECK_Z + 0.02),
            ),
            box(
                "VestBack",
                "chest",
                "leather",
                (-w, w),
                (d * 0.82, d),
                (WAIST_Z + 0.06, NECK_Z + 0.02),
            ),
        ],
    },
    "torso_over-plate": {
        "slot": "torso_over",
        "covers": ["sides", "front_back"],
        "label": "Chest plate",
        "styles": ["scifi", "fantasy"],
        "fits_family": "jarvis-biped",
        "wrapping": True,
        "pieces": lambda w, d: [
            box(
                "Plate", "chest", "metal", (-w, w), (-d, -d * 0.72), (CHEST_Z - 0.02, NECK_Z + 0.02)
            ),
            box(
                "PlateBack",
                "chest",
                "metal",
                (-w, w),
                (d * 0.72, d),
                (CHEST_Z - 0.02, NECK_Z + 0.02),
            ),
            box(
                "PlateSideL",
                "chest",
                "metal",
                (w * 0.88, w),
                (-d, d),
                (CHEST_Z - 0.02, NECK_Z + 0.02),
            ),
            box(
                "PlateSideR",
                "chest",
                "metal",
                (-w, -w * 0.88),
                (-d, d),
                (CHEST_Z - 0.02, NECK_Z + 0.02),
            ),
            box(
                "PlateStud",
                "chest",
                "accent",
                (-0.06, 0.06),
                (-d - 0.03, -d + 0.01),
                (CHEST_Z + 0.12, CHEST_Z + 0.24),
            ),
        ],
    },
    "torso_over-apron": {
        "slot": "torso_over",
        "covers": ["sides"],
        "label": "Apron",
        "styles": ["modern", "fantasy", "cartoon", "scifi"],
        "fits_family": "jarvis-biped",
        "wrapping": True,
        "pieces": lambda w, d: [
            box(
                "ApronBody",
                "spine",
                "secondary",
                (-w, w),
                (-d, -d * 0.78),
                (HIP_Z - 0.18, CHEST_Z + 0.02),
            ),
            box(
                "ApronStrapL",
                "chest",
                "secondary",
                (w * 0.24, w * 0.62),
                (-d, d),
                (NECK_Z - 0.06, NECK_Z + 0.02),
            ),
            box(
                "ApronStrapR",
                "chest",
                "secondary",
                (-w * 0.62, -w * 0.24),
                (-d, d),
                (NECK_Z - 0.06, NECK_Z + 0.02),
            ),
        ],
    },
    # ---- belt ---------------------------------------------------------------
    "belt-toolbelt": {
        "slot": "belt",
        "covers": ["sides", "front_back"],
        "label": "Tool belt",
        "styles": ["modern", "scifi", "cartoon", "fantasy"],
        "fits_family": "jarvis-biped",
        "wrapping": True,
        "pieces": lambda w, d: [
            box("BeltStrap", "hips", "leather", (-w, w), (-d, d), (WAIST_Z - 0.07, WAIST_Z + 0.01)),
            box(
                "PouchL",
                "hips",
                "leather",
                (w * 0.48, w),
                (-d - 0.02, -d * 0.55),
                (WAIST_Z - 0.20, WAIST_Z - 0.05),
            ),
            box(
                "PouchR",
                "hips",
                "metal",
                (-w, -w * 0.48),
                (-d - 0.02, -d * 0.55),
                (WAIST_Z - 0.18, WAIST_Z - 0.05),
            ),
        ],
    },
    "belt-sash": {
        "slot": "belt",
        "covers": ["sides", "front_back"],
        "label": "Sash",
        "styles": ["fantasy", "cartoon", "modern"],
        "fits_family": "jarvis-biped",
        "wrapping": True,
        "pieces": lambda w, d: [
            box("SashBand", "hips", "accent", (-w, w), (-d, d), (WAIST_Z - 0.09, WAIST_Z + 0.02)),
            box(
                "SashTail",
                "hips",
                "accent",
                (w * 0.42, w * 0.82),
                (-d - 0.01, -d * 0.62),
                (HIP_Z - 0.10, WAIST_Z - 0.06),
            ),
        ],
    },
    # ---- back ---------------------------------------------------------------
    "back-satchel": {
        "slot": "back",
        "label": "Satchel",
        "asymmetric": True,
        "styles": ["modern", "fantasy", "cartoon"],
        "pieces": lambda: [
            box(
                "Bag",
                "chest",
                "leather",
                (0.18, 0.40),
                (-0.10, 0.16),
                (WAIST_Z - 0.04, CHEST_Z + 0.06),
            ),
            box(
                "BagFlap",
                "chest",
                "secondary_shade",
                (0.17, 0.41),
                (-0.11, 0.17),
                (CHEST_Z - 0.02, CHEST_Z + 0.07),
            ),
            box(
                "BagStrap",
                "chest",
                "leather",
                (-0.16, 0.30),
                (-0.24, 0.24),
                (CHEST_Z + 0.10, NECK_Z + 0.02),
            ),
        ],
    },
    "back-jetpack": {
        "slot": "back",
        "label": "Jetpack",
        "styles": ["scifi"],
        "pieces": lambda: [
            box(
                "TankL",
                "chest",
                "metal",
                (0.06, 0.22),
                (0.20, 0.42),
                (CHEST_Z - 0.06, NECK_Z),
            ),
            box(
                "TankR",
                "chest",
                "metal",
                (-0.22, -0.06),
                (0.20, 0.42),
                (CHEST_Z - 0.06, NECK_Z),
            ),
            box(
                "Thrust",
                "chest",
                "emissive",
                (-0.20, 0.20),
                (0.24, 0.38),
                (CHEST_Z - 0.14, CHEST_Z - 0.06),
            ),
            box(
                "PackStrapL",
                "chest",
                "leather",
                (0.10, 0.19),
                (-0.23, 0.21),
                (NECK_Z - 0.06, NECK_Z + 0.01),
            ),
            box(
                "PackStrapR",
                "chest",
                "leather",
                (-0.19, -0.10),
                (-0.23, 0.21),
                (NECK_Z - 0.06, NECK_Z + 0.01),
            ),
        ],
    },
    "back-quiver": {
        "slot": "back",
        "label": "Quiver",
        "styles": ["fantasy", "cartoon"],
        "pieces": lambda: [
            box(
                "QuiverTube",
                "chest",
                "leather",
                (0.14, 0.30),
                (0.18, 0.34),
                (WAIST_Z, NECK_Z - 0.02),
            ),
            box(
                "Arrows",
                "chest",
                "secondary_shade",
                (0.17, 0.27),
                (0.21, 0.31),
                (NECK_Z - 0.04, NECK_Z + 0.18),
            ),
            box(
                "QuiverStrap",
                "chest",
                "leather",
                (-0.24, 0.24),
                (-0.23, 0.22),
                (CHEST_Z + 0.06, CHEST_Z + 0.16),
            ),
        ],
    },
    "back-cloak": {
        "slot": "back",
        "label": "Cloak",
        "styles": ["fantasy", "cartoon", "scifi"],
        "two_sided": True,
        "pieces": lambda: [
            box(
                "CloakSheet",
                "chest",
                "primary_shade",
                (-0.40, 0.40),
                (0.235, 0.245),
                (HIP_Z - 0.22, NECK_Z + 0.02),
            ),
            box(
                "CloakCollar",
                "chest",
                "accent",
                (-0.28, 0.28),
                (-0.24, 0.26),
                (NECK_Z - 0.03, NECK_Z + 0.05),
            ),
        ],
    },
    # ---- hands --------------------------------------------------------------
    "hand_l-clipboard": {
        "slot": "hand_l",
        "label": "Clipboard",
        "styles": ["modern", "scifi", "cartoon", "fantasy"],
        "pieces": lambda: [
            box(
                "Board",
                "handslot_l",
                "leather",
                (HANDSLOT_L[0] - 0.04, HANDSLOT_L[0] + 0.03),
                (-0.44, -0.08),
                (HANDSLOT_L[2] - 0.24, HANDSLOT_L[2] + 0.22),
            ),
            box(
                "Paper",
                "handslot_l",
                "secondary",
                (HANDSLOT_L[0] + 0.03, HANDSLOT_L[0] + 0.045),
                (-0.41, -0.11),
                (HANDSLOT_L[2] - 0.21, HANDSLOT_L[2] + 0.16),
            ),
            box(
                "Clip",
                "handslot_l",
                "metal",
                (HANDSLOT_L[0] + 0.03, HANDSLOT_L[0] + 0.05),
                (-0.34, -0.18),
                (HANDSLOT_L[2] + 0.16, HANDSLOT_L[2] + 0.22),
            ),
        ],
    },
    "hand_l-lantern": {
        "slot": "hand_l",
        "label": "Lantern",
        "styles": ["fantasy", "modern", "cartoon"],
        "pieces": lambda: [
            box(
                "LanternGlass",
                "handslot_l",
                "emissive",
                (HANDSLOT_L[0] - 0.07, HANDSLOT_L[0] + 0.07),
                (-0.30, -0.16),
                (HANDSLOT_L[2] - 0.30, HANDSLOT_L[2] - 0.12),
            ),
            box(
                "LanternCap",
                "handslot_l",
                "metal",
                (HANDSLOT_L[0] - 0.08, HANDSLOT_L[0] + 0.08),
                (-0.31, -0.15),
                (HANDSLOT_L[2] - 0.12, HANDSLOT_L[2] - 0.05),
            ),
            box(
                "LanternHoop",
                "handslot_l",
                "metal",
                (HANDSLOT_L[0] - 0.02, HANDSLOT_L[0] + 0.02),
                (-0.25, -0.21),
                (HANDSLOT_L[2] - 0.05, HANDSLOT_L[2] + 0.06),
            ),
        ],
    },
    "hand_r-broom": {
        "slot": "hand_r",
        "label": "Broom",
        "styles": ["modern", "cartoon", "fantasy"],
        "pieces": lambda: [
            box(
                "Handle",
                "handslot_r",
                "leather",
                (HANDSLOT_R[0] - 0.025, HANDSLOT_R[0] + 0.025),
                (-0.30, 0.62),
                (HANDSLOT_R[2] - 0.025, HANDSLOT_R[2] + 0.025),
            ),
            box(
                "Bristles",
                "handslot_r",
                "accent",
                (HANDSLOT_R[0] - 0.07, HANDSLOT_R[0] + 0.07),
                (0.62, 0.86),
                (HANDSLOT_R[2] - 0.07, HANDSLOT_R[2] + 0.07),
            ),
        ],
    },
    "hand_r-torch": {
        "slot": "hand_r",
        "label": "Torch",
        "styles": ["fantasy", "cartoon", "scifi"],
        "pieces": lambda: [
            box(
                "Shaft",
                "handslot_r",
                "leather",
                (HANDSLOT_R[0] - 0.03, HANDSLOT_R[0] + 0.03),
                (-0.34, 0.10),
                (HANDSLOT_R[2] - 0.03, HANDSLOT_R[2] + 0.03),
            ),
            box(
                "Flame",
                "handslot_r",
                "emissive",
                (HANDSLOT_R[0] - 0.06, HANDSLOT_R[0] + 0.06),
                (-0.50, -0.34),
                (HANDSLOT_R[2] - 0.06, HANDSLOT_R[2] + 0.06),
            ),
        ],
    },
    "hand_r-scanner": {
        "slot": "hand_r",
        "label": "Scanner",
        "styles": ["scifi", "modern"],
        "pieces": lambda: [
            box(
                "Grip",
                "handslot_r",
                "primary_shade",
                (HANDSLOT_R[0] - 0.035, HANDSLOT_R[0] + 0.035),
                (-0.20, 0.02),
                (HANDSLOT_R[2] - 0.035, HANDSLOT_R[2] + 0.035),
            ),
            box(
                "Head",
                "handslot_r",
                "metal",
                (HANDSLOT_R[0] - 0.06, HANDSLOT_R[0] + 0.06),
                (-0.34, -0.20),
                (HANDSLOT_R[2] - 0.06, HANDSLOT_R[2] + 0.06),
            ),
            box(
                "Beam",
                "handslot_r",
                "emissive",
                (HANDSLOT_R[0] - 0.03, HANDSLOT_R[0] + 0.03),
                (-0.42, -0.34),
                (HANDSLOT_R[2] - 0.03, HANDSLOT_R[2] + 0.03),
            ),
        ],
    },
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
        "styles": ["modern", "scifi", "cartoon"],
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
        "fits_family": "jarvis-biped",
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


def build_part(
    part_id: str, arm: bpy.types.Object, sheet: dict, img, sheet_material, size: str | None = None
):
    """Build one accessory: boxes, UVs on the strip, weight 1 on its attach bone.

    A wrapping garment is cut to `size`; everything else hangs on a bone the
    rig places identically for every body and takes no size at all.
    """
    spec = PART_SPECS.get(part_id)
    if spec is None:
        raise SystemExit(f"unknown procedural part {part_id!r}")
    if spec.get("wrapping"):
        if size not in WRAP_SIZES:
            raise SystemExit(f"{part_id} is a wrapping garment and needs a size, got {size!r}")
        pieces = spec["pieces"](*WRAP_SIZES[size])
    else:
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
