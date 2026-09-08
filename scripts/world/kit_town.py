# ruff: noqa: E501
"""Six more World Kit halls for the town centre — the places the island was
missing for whole families of hands (world-masterplan-v2.md §5).

Until now the town showed four of the things an agent can do: plugins, skills,
MCP servers and a CLI seat. Everything else — sending mail, driving the
desktop, reading the web, delivering a result, thinking on a local model,
sitting in a room — collapsed into ``core`` and put the figure at the Workshop
outside the town. These halls give those families an address:

    signal-office     the post and telegraph office   (mail, chat, contacts, calls)
    control-room      the signal box                  (the hand on the desktop)
    gallery-hall      the exhibition hall             (finished work, artifacts)
    model-boilerhouse the boiler house                (a local model, inferring)
    town-hall         the town hall                   (rooms and the board)
    observatory       the lookout                     (the web and the browser)

Built on the helpers of ``build_world_kit.py``, in its own module so two
sessions can add buildings without editing the same file. Run inside Blender::

    blender -b --python scripts/world/kit_town.py -- --target signal-office --out <kit dir>

or through the Blender MCP with ``KIT_ARGS`` set. ``--keep-scene`` leaves
whatever else is in the scene alone and removes only the building it made.

Contract (§4.1), the same one every kit building keeps: origin at the
footprint's ground centre, the FRONT (door side) faces glTF +Z, which is
Blender −Y; metres; bevelled edges; one flat material per colour; the root
empty carries the ``jarvis_building`` extras.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import bpy  # type: ignore[import-not-found]

_BASE_PATH = (
    Path(__file__).resolve().parent / "build_world_kit.py" if "__file__" in globals() else None
)


def _load_base(path: Path):
    spec = importlib.util.spec_from_file_location("kit_base", str(path))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _base():
    path = _BASE_PATH or Path(globals().get("KIT_BASE_PATH", ""))
    return _load_base(path)


#: One accent per hall, plus the few surfaces the town's halls need and the
#: island palette does not carry: a screen, a canvas, brick, a hot furnace.
TOWN_PALETTE = {
    "fam_comms": "#06d6a0",
    "fam_desktop": "#ff5fa2",
    "fam_gallery": "#ffb703",
    "fam_models": "#9b5de5",
    "fam_civic": "#4361ee",
    "fam_web": "#4cc9f0",
    "screen": "#0f172a",
    "screen_glow": "#7ff0c8",
    "brick": "#a8553f",
    "brick_dark": "#7d3d2d",
    "furnace": "#ff7b33",
    "canvas": "#fdfbf4",
    "slate": "#2f3542",
    "stone": "#ded6c4",
    "stone_dark": "#bdb29b",
    "brass": "#d9a441",
    "ink": "#22243a",
    "paper": "#f4efe2",
}


def _sign(k, root, text, width, x, y_front, z, rim_key, size: float = 1.2):
    """A dark board with a coloured rim and glowing letters, hung on the front.

    Same anatomy as the other halls' signs, so a visitor reads every building
    the same way. ``x`` is the board's centre, ``y_front`` the facade plane.
    """
    k.box(
        "sign_board",
        (width, 0.5, size + 0.7),
        (x, y_front + 0.55, z),
        "sign_board",
        root,
        bevel=0.1,
    )
    k.box(
        "sign_rim",
        (width + 0.3, 0.3, size + 1.0),
        (x, y_front + 0.7, z),
        rim_key,
        root,
        bevel=0.08,
        emissive=0.8,
    )
    k.text_mesh(
        "sign_text",
        text,
        size,
        0.18,
        (x, y_front + 0.25, z - 0.05),
        "neon_glow",
        root,
        emissive=3.0,
    )


def _lamp(k, root, name, at, key="neon_glow"):
    """A post lamp beside a door: a slim pole with a glowing head."""
    k.cylinder(f"{name}_post", 0.12, 2.6, (at[0], at[1], at[2] + 1.3), "metal", root, verts=8)
    k.box(
        f"{name}_head",
        (0.45, 0.45, 0.5),
        (at[0], at[1], at[2] + 2.75),
        key,
        root,
        bevel=0.08,
        emissive=2.4,
    )


# ---------------------------------------------------------------------------
# 1. The Signal Office — mail, chat, contacts, calls
# ---------------------------------------------------------------------------


def build_signal_office(k) -> bpy.types.Object:
    """The post and telegraph office: a brick hall with a clock over the door, a
    row of letter slots along the facade, and a telegraph mast on the roof whose
    insulators glow while a message runs. 7 x 5 tiles."""
    W, D, H = 13.4, 9.4, 5.0
    root = k.new_root(
        "signal-office",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "signal-office",
                "footprint": [7, 5],
                "forward": "+Z",
                "door": [0.0, 4.7],
                "stand": [0, 7.2],
                "sign": [0.0, 8.6, 0],
                "height_m": 12.6,
            }
        },
    )
    y_front = -D / 2
    base = 0.35
    k.box("plinth", (W + 0.8, D + 0.8, base), (0, 0, base / 2), "plinth", root, bevel=0.08)
    k.box("hall", (W, D, H), (0, 0, base + H / 2), "brick", root, bevel=0.14)
    k.box(
        "base_band", (W + 0.12, D + 0.12, 0.7), (0, 0, base + 0.35), "brick_dark", root, bevel=0.05
    )
    roof_z = base + H
    k.box("cornice", (W + 1.0, D + 1.0, 0.45), (0, 0, roof_z + 0.22), "stone", root, bevel=0.1)
    k.box("roof_slab", (W - 0.6, D - 0.6, 0.35), (0, 0, roof_z + 0.6), "roof", root, bevel=0.08)

    # The letter slots: a band of small mint-lit openings along the facade.
    for i in range(9):
        k.box(
            f"slot_{i}",
            (0.85, 0.14, 0.3),
            (-W / 2 + 1.4 + i * 1.35, y_front - 0.06, base + 3.55),
            "fam_comms",
            root,
            bevel=0.03,
            emissive=1.1,
        )
    k.box(
        "slot_shelf",
        (W - 1.6, 0.4, 0.2),
        (0, y_front - 0.16, base + 3.25),
        "stone_dark",
        root,
        bevel=0.05,
    )

    # Door, its frame and the two windows flanking it.
    k.box("door_frame", (3.4, 0.34, 3.6), (0, y_front - 0.1, base + 1.8), "stone", root, bevel=0.05)
    k.box(
        "door_l",
        (1.45, 0.16, 3.1),
        (-0.78, y_front - 0.24, base + 1.55),
        "wood_dark",
        root,
        bevel=0.03,
    )
    k.box(
        "door_r",
        (1.45, 0.16, 3.1),
        (0.78, y_front - 0.24, base + 1.55),
        "wood_dark",
        root,
        bevel=0.03,
    )
    for sx in (-1, 1):
        k.box(
            f"window_{'l' if sx < 0 else 'r'}",
            (2.4, 0.14, 1.6),
            (sx * 4.4, y_front - 0.08, base + 2.2),
            "glass",
            root,
            bevel=0.02,
            emissive=0.35,
        )
        k.box(
            f"window_sill_{'l' if sx < 0 else 'r'}",
            (2.8, 0.3, 0.18),
            (sx * 4.4, y_front - 0.14, base + 1.3),
            "stone",
            root,
            bevel=0.04,
        )

    # The clock over the door: a brass ring, a pale face and two hands.
    k.cylinder(
        "clock_ring",
        1.15,
        0.28,
        (0, y_front - 0.18, base + 4.35),
        "brass",
        root,
        verts=24,
        rot=(math.pi / 2, 0, 0),
    )
    k.cylinder(
        "clock_face",
        0.98,
        0.16,
        (0, y_front - 0.3, base + 4.35),
        "paper",
        root,
        verts=24,
        rot=(math.pi / 2, 0, 0),
        emissive=0.5,
    )
    k.box(
        "clock_hand_h", (0.09, 0.08, 0.62), (0, y_front - 0.4, base + 4.6), "ink", root, bevel=0.01
    )
    k.box(
        "clock_hand_m",
        (0.7, 0.08, 0.09),
        (0.28, y_front - 0.4, base + 4.35),
        "ink",
        root,
        bevel=0.01,
    )

    # The telegraph mast: a lattice pole with three glowing insulators and the
    # wires that run off the roof toward the rest of the town.
    mx, my = W / 2 - 2.4, 1.2
    k.cylinder("mast", 0.22, 7.0, (mx, my, roof_z + 4.2), "metal", root, verts=10)
    for i, dz in enumerate((1.6, 3.0, 4.4)):
        k.box(f"cross_{i}", (3.4, 0.24, 0.22), (mx, my, roof_z + dz), "wood_dark", root, bevel=0.04)
        for sx in (-1, 1):
            k.box(
                f"insulator_{i}_{'l' if sx < 0 else 'r'}",
                (0.34, 0.34, 0.4),
                (mx + sx * 1.5, my, roof_z + dz + 0.3),
                "fam_comms",
                root,
                bevel=0.06,
                emissive=2.2,
            )
    for i, sag in enumerate((0.0, 0.35)):
        k.box(
            f"wire_{i}",
            (7.4, 0.07, 0.07),
            (mx - 4.2, my, roof_z + 4.7 - sag),
            "slate",
            root,
            bevel=0.0,
            rot=(0, 0.06 - i * 0.03, 0),
        )

    # The mailbox and the bench on the forecourt.
    k.box(
        "mailbox_body",
        (0.95, 0.95, 1.5),
        (-W / 2 + 1.5, y_front - 2.1, base + 0.75),
        "fam_comms",
        root,
        bevel=0.1,
    )
    k.box(
        "mailbox_slot",
        (0.6, 0.12, 0.14),
        (-W / 2 + 1.5, y_front - 2.6, base + 1.25),
        "ink",
        root,
        bevel=0.02,
    )
    k.box(
        "mailbox_cap",
        (1.15, 1.15, 0.22),
        (-W / 2 + 1.5, y_front - 2.1, base + 1.6),
        "stone_dark",
        root,
        bevel=0.06,
    )
    k.box(
        "bench",
        (2.6, 0.7, 0.22),
        (W / 2 - 2.6, y_front - 2.2, base + 0.55),
        "wood",
        root,
        bevel=0.05,
    )
    for sx in (-1, 1):
        k.box(
            f"bench_leg_{'l' if sx < 0 else 'r'}",
            (0.22, 0.6, 0.55),
            (W / 2 - 2.6 + sx * 1.0, y_front - 2.2, base + 0.28),
            "wood_dark",
            root,
            bevel=0.03,
        )
    _lamp(k, root, "lamp", (W / 2 - 0.9, y_front - 1.4, base), "fam_comms")

    _sign(k, root, "SIGNAL", 6.6, 0, y_front, roof_z + 1.55, "fam_comms")
    return root


# ---------------------------------------------------------------------------
# 2. The Control Room — the hand on the desktop
# ---------------------------------------------------------------------------


def build_control_room(k) -> bpy.types.Object:
    """The signal box: a raised glass cabin over a solid base, a desk of lit
    screens visible through the panorama window, a bank of levers, and a small
    dish on the roof. 7 x 5 tiles."""
    W, D = 13.4, 9.4
    root = k.new_root(
        "control-room",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "control-room",
                "footprint": [7, 5],
                "forward": "+Z",
                "door": [-3.6, 4.7],
                "stand": [0, 7.2],
                "sign": [0.0, 9.4, 0],
                "height_m": 11.2,
            }
        },
    )
    y_front = -D / 2
    base = 0.35
    lower_h = 2.5
    k.box("plinth", (W + 0.8, D + 0.8, base), (0, 0, base / 2), "plinth", root, bevel=0.08)
    k.box("lower", (W, D, lower_h), (0, 0, base + lower_h / 2), "slate", root, bevel=0.12)
    k.box(
        "lower_band", (W + 0.14, D + 0.14, 0.4), (0, 0, base + 0.2), "steel_dark", root, bevel=0.05
    )
    # The base is blind except for a vent band — the machinery is below.
    for i in range(6):
        k.box(
            f"vent_{i}",
            (1.3, 0.1, 0.7),
            (-W / 2 + 1.6 + i * 2.1, y_front - 0.04, base + 1.5),
            "steel_dark",
            root,
            bevel=0.03,
        )
    deck_z = base + lower_h
    k.box("deck", (W + 0.7, D + 0.7, 0.3), (0, 0, deck_z + 0.15), "steel", root, bevel=0.07)
    # A signal box's cabin: set back just far enough for a gallery to run round it.
    cab_w, cab_d, cab_h = W - 1.6, D - 1.6, 3.4
    cab_z = deck_z + 0.3
    k.box("cabin", (cab_w, cab_d, cab_h), (0, 0, cab_z + cab_h / 2), "wall", root, bevel=0.12)
    # The panorama: one tall glass band on the front, returning round both sides.
    k.box(
        "glass_front",
        (cab_w - 0.5, 0.18, 2.2),
        (0, -cab_d / 2 - 0.03, cab_z + 1.95),
        "glass",
        root,
        bevel=0.03,
        emissive=0.5,
    )
    for sx in (-1, 1):
        k.box(
            f"glass_side_{'l' if sx < 0 else 'r'}",
            (0.18, cab_d - 0.7, 2.2),
            (sx * (cab_w / 2 + 0.03), 0, cab_z + 1.95),
            "glass",
            root,
            bevel=0.03,
            emissive=0.5,
        )
    # Mullions, so the glass reads as a window band and not a hole in the wall.
    for i, mx in enumerate((-3.2, 0.0, 3.2)):
        k.box(
            f"mullion_{i}",
            (0.16, 0.24, 2.3),
            (mx, -cab_d / 2 - 0.05, cab_z + 1.95),
            "wall_shade",
            root,
            bevel=0.02,
        )
    # A thin roof: enough eave to cast a line, never enough to hide the facade.
    k.box(
        "cabin_roof",
        (cab_w + 0.7, cab_d + 0.7, 0.32),
        (0, 0, cab_z + cab_h + 0.16),
        "trim",
        root,
        bevel=0.08,
    )
    k.box(
        "roof_edge",
        (cab_w + 0.9, cab_d + 0.9, 0.12),
        (0, 0, cab_z + cab_h + 0.02),
        "fam_desktop",
        root,
        bevel=0.03,
        emissive=0.7,
    )

    # The desk of screens behind the glass, and the bank of levers beside it.
    k.box(
        "desk",
        (cab_w - 2.6, 1.0, 0.28),
        (0, -cab_d / 2 + 1.0, cab_z + 1.0),
        "wood_dark",
        root,
        bevel=0.05,
    )
    for i, sx in enumerate((-2.4, 0.0, 2.4)):
        k.box(
            f"screen_{i}",
            (1.9, 0.16, 1.1),
            (sx, -cab_d / 2 + 0.9, cab_z + 1.85),
            "screen",
            root,
            bevel=0.05,
        )
        k.box(
            f"screen_glow_{i}",
            (1.65, 0.06, 0.88),
            (sx, -cab_d / 2 + 0.8, cab_z + 1.85),
            "screen_glow",
            root,
            bevel=0.02,
            emissive=2.0,
        )
    for i in range(5):
        lx = -cab_w / 2 + 1.2 + i * 0.5
        k.box(
            f"lever_{i}",
            (0.12, 0.12, 0.8),
            (lx, 1.2, cab_z + 1.15),
            "steel",
            root,
            bevel=0.03,
            rot=(0.34 - i * 0.14, 0, 0),
        )
        k.box(
            f"lever_knob_{i}",
            (0.22, 0.22, 0.22),
            (lx, 1.35, cab_z + 1.55),
            "fam_desktop",
            root,
            bevel=0.06,
            emissive=1.4,
        )

    # The gallery rail round the deck, on the two sides the camera sees.
    for i in range(9):
        k.box(
            f"rail_post_f{i}",
            (0.11, 0.11, 0.8),
            (-W / 2 + 0.5 + i * 1.65, y_front - 0.3, deck_z + 0.7),
            "steel",
            root,
            bevel=0.02,
        )
    k.box(
        "rail_top_f",
        (W + 0.5, 0.13, 0.13),
        (0, y_front - 0.3, deck_z + 1.08),
        "steel",
        root,
        bevel=0.03,
    )
    for i in range(6):
        k.box(
            f"rail_post_s{i}",
            (0.11, 0.11, 0.8),
            (W / 2 + 0.3, y_front + 0.9 + i * 1.55, deck_z + 0.7),
            "steel",
            root,
            bevel=0.02,
        )
    k.box(
        "rail_top_s",
        (0.13, D + 0.5, 0.13),
        (W / 2 + 0.3, 0, deck_z + 1.08),
        "steel",
        root,
        bevel=0.03,
    )

    # The stair: it climbs ACROSS the facade, left to right, so the whole flight
    # stays on the lot instead of running four metres out into the street.
    steps, run, rise = 8, 0.72, deck_z / 8
    for i in range(steps):
        k.box(
            f"step_{i}",
            (run, 1.5, rise),
            (-W / 2 + 0.55 + i * run, y_front - 1.0, rise / 2 + i * rise),
            "steel_dark",
            root,
            bevel=0.03,
        )
    k.box(
        "door",
        (1.4, 0.18, 2.2),
        (-cab_w / 2 + 1.2, -cab_d / 2 - 0.06, cab_z + 1.1),
        "fam_desktop",
        root,
        bevel=0.04,
        emissive=0.5,
    )

    # The dish and the mast on the roof.
    mx, my = W / 2 - 2.4, 1.4
    k.cylinder("mast", 0.16, 2.6, (mx, my, cab_z + cab_h + 1.6), "metal", root, verts=10)
    k.cylinder(
        "dish",
        1.35,
        0.22,
        (mx, my, cab_z + cab_h + 2.9),
        "wall_shade",
        root,
        verts=20,
        rot=(0.95, 0, 0.4),
    )
    k.box(
        "dish_horn",
        (0.22, 0.22, 0.7),
        (mx, my - 0.75, cab_z + cab_h + 3.15),
        "fam_desktop",
        root,
        bevel=0.05,
        emissive=1.6,
    )

    # The pointer on its post outside — the building's mark, a cursor.
    px, py = W / 2 - 1.4, y_front - 2.4
    k.cylinder("mark_post", 0.14, 2.2, (px, py, base + 1.1), "metal", root, verts=8)
    k.box(
        "mark_body",
        (0.75, 0.16, 1.15),
        (px, py, base + 2.6),
        "fam_desktop",
        root,
        bevel=0.06,
        emissive=1.8,
        rot=(0, 0, 0.45),
    )
    k.box(
        "mark_tail",
        (0.3, 0.16, 0.75),
        (px + 0.32, py, base + 2.05),
        "fam_desktop",
        root,
        bevel=0.05,
        emissive=1.8,
        rot=(0, 0, 0.45),
    )

    _sign(k, root, "CONTROL", 6.8, 0, y_front, cab_z + cab_h + 1.3, "fam_desktop", size=1.1)
    return root


# ---------------------------------------------------------------------------
# 3. The Gallery — finished work
# ---------------------------------------------------------------------------


def build_gallery_hall(k) -> bpy.types.Object:
    """The exhibition hall: a pale block with a sawtooth skylight roof, a portico
    of four columns, framed pictures on the facade and a plinth with a piece on
    it out front. 7 x 5 tiles."""
    W, D, H = 13.4, 9.4, 5.2
    root = k.new_root(
        "gallery-hall",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "gallery-hall",
                "footprint": [7, 5],
                "forward": "+Z",
                "door": [0.0, 4.7],
                "stand": [0, 7.6],
                "sign": [0.0, 8.2, 0],
                "height_m": 9.4,
            }
        },
    )
    y_front = -D / 2
    base = 0.35
    k.box("plinth", (W + 0.8, D + 0.8, base), (0, 0, base / 2), "plinth", root, bevel=0.08)
    k.box("hall", (W, D, H), (0, 0, base + H / 2), "canvas", root, bevel=0.14)
    k.box(
        "base_band", (W + 0.12, D + 0.12, 0.55), (0, 0, base + 0.28), "wall_shade", root, bevel=0.05
    )
    roof_z = base + H
    k.box("cornice", (W + 0.9, D + 0.9, 0.4), (0, 0, roof_z + 0.2), "trim", root, bevel=0.1)

    # Sawtooth skylights: three glazed slopes facing north, the museum's own roof.
    for i, ox in enumerate((-4.2, 0.0, 4.2)):
        k.box(
            f"tooth_back_{i}", (3.4, 0.4, 1.9), (ox, 2.2, roof_z + 1.35), "trim", root, bevel=0.05
        )
        k.box(
            f"tooth_glass_{i}",
            (3.4, 4.6, 0.22),
            (ox, 0.1, roof_z + 1.15),
            "glass",
            root,
            bevel=0.04,
            emissive=0.5,
            rot=(0.5, 0, 0),
        )

    # The portico: four columns carrying a slim canopy over the entrance. It
    # stays narrow and low — a wide slab would hide the facade from a camera
    # that looks down at 50°, which is the only angle the island ever uses.
    for i, cx in enumerate((-3.3, -1.2, 1.2, 3.3)):
        k.cylinder(
            f"column_{i}", 0.32, 3.9, (cx, y_front - 0.95, base + 1.95), "stone", root, verts=16
        )
        k.cylinder(
            f"column_cap_{i}",
            0.44,
            0.24,
            (cx, y_front - 0.95, base + 4.0),
            "stone_dark",
            root,
            verts=16,
        )
    k.box("canopy", (8.4, 1.7, 0.32), (0, y_front - 0.95, base + 4.28), "stone", root, bevel=0.07)
    k.box(
        "canopy_rim",
        (8.7, 2.0, 0.13),
        (0, y_front - 0.95, base + 4.08),
        "fam_gallery",
        root,
        bevel=0.04,
        emissive=0.9,
    )

    # The doors and the framed pictures either side of them.
    k.box(
        "door_l",
        (1.5, 0.16, 3.2),
        (-0.8, y_front - 0.14, base + 1.6),
        "wood_dark",
        root,
        bevel=0.03,
    )
    k.box(
        "door_r", (1.5, 0.16, 3.2), (0.8, y_front - 0.14, base + 1.6), "wood_dark", root, bevel=0.03
    )
    k.box(
        "door_handle",
        (0.12, 0.12, 0.7),
        (0.25, y_front - 0.26, base + 1.7),
        "brass",
        root,
        bevel=0.03,
    )
    for i, (fx, key) in enumerate(((-5.0, "fam_gallery"), (5.0, "fam_web"))):
        k.box(
            f"frame_{i}",
            (2.2, 0.18, 1.9),
            (fx, y_front - 0.08, base + 2.4),
            "brass",
            root,
            bevel=0.05,
        )
        k.box(
            f"canvas_{i}",
            (1.8, 0.08, 1.5),
            (fx, y_front - 0.18, base + 2.4),
            key,
            root,
            bevel=0.02,
            emissive=0.9,
        )

    # The piece on its plinth on the forecourt, lit from the ground.
    k.box(
        "art_plinth",
        (1.6, 1.6, 1.5),
        (-W / 2 + 1.9, y_front - 3.4, base + 0.75),
        "stone",
        root,
        bevel=0.06,
    )
    k.box(
        "art_a",
        (0.9, 0.9, 1.2),
        (-W / 2 + 1.9, y_front - 3.4, base + 2.1),
        "fam_gallery",
        root,
        bevel=0.12,
        emissive=1.2,
        rot=(0, 0, 0.6),
    )
    k.box(
        "art_b",
        (0.55, 0.55, 0.8),
        (-W / 2 + 1.9, y_front - 3.4, base + 2.9),
        "fam_desktop",
        root,
        bevel=0.1,
        emissive=1.2,
        rot=(0, 0, 1.1),
    )
    k.cylinder(
        "art_light",
        0.4,
        0.14,
        (-W / 2 + 1.9, y_front - 2.4, base + 0.08),
        "neon_glow",
        root,
        verts=14,
        emissive=2.2,
    )

    # A crate by the door — a result just delivered.
    k.box(
        "crate", (1.2, 1.2, 1.2), (W / 2 - 2.0, y_front - 3.2, base + 0.6), "wood", root, bevel=0.06
    )
    k.box(
        "crate_band",
        (1.32, 1.32, 0.16),
        (W / 2 - 2.0, y_front - 3.2, base + 0.75),
        "fam_gallery",
        root,
        bevel=0.03,
        emissive=0.8,
    )
    _lamp(k, root, "lamp", (W / 2 - 0.8, y_front - 1.2, base), "fam_gallery")

    _sign(k, root, "GALLERY", 7.2, 0, y_front, roof_z + 1.4, "fam_gallery", size=1.1)
    return root


# ---------------------------------------------------------------------------
# 4. The Boiler House — a local model, inferring
# ---------------------------------------------------------------------------


def build_model_boilerhouse(k) -> bpy.types.Object:
    """The boiler house: a squat brick engine room with a tall chimney, a furnace
    door glowing violet, a pressure tank on its saddle and a bank of gauges. This
    is where an agent stands while its own local model thinks. 5 x 5 tiles."""
    W, D, H = 9.4, 9.4, 4.6
    root = k.new_root(
        "model-boilerhouse",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "model-boilerhouse",
                "footprint": [5, 5],
                "forward": "+Z",
                "door": [-1.4, 4.7],
                "stand": [0, 7.2],
                "sign": [0.0, 7.6, 0],
                "height_m": 15.0,
            }
        },
    )
    y_front = -D / 2
    base = 0.35
    k.box("plinth", (W + 0.8, D + 0.8, base), (0, 0, base / 2), "plinth", root, bevel=0.08)
    k.box("hall", (W, D, H), (0, 0, base + H / 2), "brick", root, bevel=0.14)
    k.box(
        "base_band", (W + 0.12, D + 0.12, 0.6), (0, 0, base + 0.3), "brick_dark", root, bevel=0.05
    )
    roof_z = base + H
    k.box("cornice", (W + 0.9, D + 0.9, 0.42), (0, 0, roof_z + 0.21), "stone", root, bevel=0.1)
    k.box("roof_slab", (W - 0.5, D - 0.5, 0.3), (0, 0, roof_z + 0.57), "roof", root, bevel=0.06)

    # The chimney: a tapered brick stack with a metal cap and a warm mouth.
    cx, cy = W / 2 - 1.9, 2.2
    k.cylinder("stack_low", 1.15, 5.4, (cx, cy, roof_z + 2.7), "brick_dark", root, verts=18)
    k.cylinder("stack_high", 0.95, 3.4, (cx, cy, roof_z + 7.1), "brick", root, verts=18)
    k.cylinder("stack_cap", 1.15, 0.4, (cx, cy, roof_z + 8.9), "metal", root, verts=18)
    k.cylinder(
        "stack_mouth", 0.8, 0.3, (cx, cy, roof_z + 9.05), "fam_models", root, verts=18, emissive=2.4
    )
    for i in range(3):
        k.cylinder(
            f"stack_band_{i}",
            1.2,
            0.22,
            (cx, cy, roof_z + 1.2 + i * 1.9),
            "stone_dark",
            root,
            verts=18,
        )

    # The furnace: an arched door in the facade, its slot glowing while it runs.
    k.box(
        "furnace_frame",
        (3.2, 0.34, 3.0),
        (-1.4, y_front - 0.1, base + 1.5),
        "steel_dark",
        root,
        bevel=0.06,
    )
    k.box(
        "furnace_door",
        (2.5, 0.2, 2.3),
        (-1.4, y_front - 0.26, base + 1.35),
        "steel",
        root,
        bevel=0.08,
    )
    k.box(
        "furnace_slot",
        (1.9, 0.1, 0.42),
        (-1.4, y_front - 0.38, base + 1.85),
        "fam_models",
        root,
        bevel=0.03,
        emissive=3.0,
    )
    k.cylinder(
        "furnace_wheel",
        0.55,
        0.18,
        (-1.4, y_front - 0.46, base + 0.85),
        "brass",
        root,
        verts=16,
        rot=(math.pi / 2, 0, 0),
    )

    # The pressure tank on its saddles, lying across the roof where it reads.
    tz = roof_z + 1.5
    k.cylinder("tank", 1.15, 6.2, (-1.6, 1.0, tz), "steel", root, verts=20, rot=(0, math.pi / 2, 0))
    for i, sx in enumerate((-4.0, 0.8)):
        k.box(
            f"saddle_{i}", (0.9, 2.4, 1.1), (sx, 1.0, roof_z + 0.95), "steel_dark", root, bevel=0.06
        )
    k.cylinder(
        "tank_cap",
        0.55,
        0.4,
        (-4.8, 1.0, tz),
        "fam_models",
        root,
        verts=16,
        rot=(0, math.pi / 2, 0),
        emissive=1.4,
    )
    k.cylinder("tank_riser", 0.24, 1.6, (1.4, 1.0, tz - 0.6), "copper", root, verts=10)

    # The gauges beside the door: three brass dials on a slate board.
    k.box(
        "gauge_board",
        (2.8, 0.16, 1.5),
        (2.6, y_front - 0.08, base + 3.1),
        "slate",
        root,
        bevel=0.05,
    )
    for i, gx in enumerate((1.7, 2.6, 3.5)):
        k.cylinder(
            f"gauge_{i}",
            0.35,
            0.2,
            (gx, y_front - 0.2, base + 3.1),
            "brass",
            root,
            verts=16,
            rot=(math.pi / 2, 0, 0),
        )
        k.cylinder(
            f"gauge_face_{i}",
            0.26,
            0.1,
            (gx, y_front - 0.3, base + 3.1),
            "paper",
            root,
            verts=16,
            rot=(math.pi / 2, 0, 0),
            emissive=0.6,
        )

    # Pipes running out of the wall and down to the ground.
    for i, px in enumerate((-3.6, -2.9)):
        k.cylinder(f"pipe_{i}", 0.2, 3.2, (px, y_front + 0.4, base + 3.6), "copper", root, verts=10)
        k.cylinder(
            f"pipe_elbow_{i}",
            0.24,
            1.6,
            (px, y_front - 0.3, base + 5.1),
            "copper",
            root,
            verts=10,
            rot=(math.pi / 2, 0, 0),
        )
    k.box(
        "coal_bin",
        (2.0, 1.4, 0.9),
        (-W / 2 + 1.6, y_front - 2.4, base + 0.45),
        "steel_dark",
        root,
        bevel=0.06,
    )
    k.box(
        "coal",
        (1.6, 1.0, 0.4),
        (-W / 2 + 1.6, y_front - 2.4, base + 1.05),
        "fam_models",
        root,
        bevel=0.15,
        emissive=0.9,
    )

    _sign(k, root, "MODELS", 5.8, -1.4, y_front, roof_z + 1.35, "fam_models", size=1.0)
    return root


# ---------------------------------------------------------------------------
# 5. The Town Hall — rooms and the board
# ---------------------------------------------------------------------------


def build_town_hall(k) -> bpy.types.Object:
    """The town hall: a symmetric stone front with a flight of steps, four
    columns under a pediment, a clock tower with a bell, and the notice board
    where the board's open items hang. 7 x 5 tiles."""
    W, D, H = 13.4, 9.4, 5.6
    root = k.new_root(
        "town-hall",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "town-hall",
                "footprint": [7, 5],
                "forward": "+Z",
                "door": [0.0, 4.7],
                "stand": [0, 8.0],
                "sign": [0.0, 9.2, 0],
                "height_m": 15.6,
            }
        },
    )
    y_front = -D / 2
    base = 0.4
    k.box("plinth", (W + 1.0, D + 1.0, base), (0, 0, base / 2), "plinth", root, bevel=0.08)
    k.box("hall", (W, D, H), (0, 0, base + H / 2), "stone", root, bevel=0.14)
    k.box(
        "base_band", (W + 0.14, D + 0.14, 0.7), (0, 0, base + 0.35), "stone_dark", root, bevel=0.05
    )
    roof_z = base + H
    k.box("cornice", (W + 1.1, D + 1.1, 0.5), (0, 0, roof_z + 0.25), "stone_dark", root, bevel=0.1)
    k.box("roof", (W - 0.4, D - 0.4, 0.5), (0, 0, roof_z + 0.75), "roof", root, bevel=0.08)

    # The steps and the portico.
    for i in range(4):
        k.box(
            f"step_{i}",
            (8.4 - i * 0.4, 0.6, 0.26),
            (0, y_front - 2.6 + i * 0.6, base - 0.05 + i * 0.26),
            "stone_dark",
            root,
            bevel=0.03,
        )
    for i, cx in enumerate((-3.9, -1.3, 1.3, 3.9)):
        k.cylinder(
            f"column_{i}", 0.46, 4.6, (cx, y_front - 1.2, base + 2.3), "stone", root, verts=16
        )
        k.cylinder(
            f"column_cap_{i}",
            0.6,
            0.3,
            (cx, y_front - 1.2, base + 4.6),
            "stone_dark",
            root,
            verts=16,
        )
        k.cylinder(
            f"column_foot_{i}",
            0.6,
            0.3,
            (cx, y_front - 1.2, base + 0.15),
            "stone_dark",
            root,
            verts=16,
        )
    k.box(
        "architrave", (9.8, 2.2, 0.55), (0, y_front - 1.2, base + 5.05), "stone", root, bevel=0.06
    )
    # The pediment: a dark tympanum field with two roof slabs sloping off it, so
    # the triangle reads against the stone instead of dissolving into it.
    arch_top = base + 5.325
    k.box(
        "tympanum",
        (9.0, 1.4, 1.5),
        (0, y_front - 1.0, arch_top + 0.75),
        "stone_dark",
        root,
        bevel=0.05,
    )
    slope = math.atan2(1.7, 4.9)
    for sx in (-1, 1):
        k.box(
            f"pediment_{'l' if sx < 0 else 'r'}",
            (5.19, 1.8, 0.4),
            (sx * 2.45, y_front - 1.3, arch_top + 0.85),
            "roof",
            root,
            bevel=0.04,
            rot=(0, sx * slope, 0),
        )
    k.cylinder(
        "pediment_medal",
        0.62,
        0.22,
        (0, y_front - 1.75, arch_top + 0.7),
        "fam_civic",
        root,
        verts=20,
        rot=(math.pi / 2, 0, 0),
        emissive=1.0,
    )

    # The doors, tall and dark, between the middle pair of columns.
    k.box(
        "door_frame",
        (3.6, 0.32, 4.0),
        (0, y_front + 0.06, base + 2.0),
        "stone_dark",
        root,
        bevel=0.05,
    )
    k.box(
        "door_l",
        (1.55, 0.16, 3.5),
        (-0.82, y_front - 0.08, base + 1.75),
        "wood_dark",
        root,
        bevel=0.03,
    )
    k.box(
        "door_r",
        (1.55, 0.16, 3.5),
        (0.82, y_front - 0.08, base + 1.75),
        "wood_dark",
        root,
        bevel=0.03,
    )

    # The clock tower with its bell, set back on the roof.
    tx, ty = 0.0, 1.6
    k.box("tower", (3.6, 3.6, 5.0), (tx, ty, roof_z + 1.0 + 2.5), "stone", root, bevel=0.1)
    k.box("tower_band", (3.8, 3.8, 0.35), (tx, ty, roof_z + 1.35), "stone_dark", root, bevel=0.05)
    k.cylinder(
        "tower_clock",
        1.05,
        0.25,
        (tx, ty - 1.85, roof_z + 4.4),
        "brass",
        root,
        verts=24,
        rot=(math.pi / 2, 0, 0),
    )
    k.cylinder(
        "tower_face",
        0.88,
        0.14,
        (tx, ty - 1.97, roof_z + 4.4),
        "paper",
        root,
        verts=24,
        rot=(math.pi / 2, 0, 0),
        emissive=0.55,
    )
    k.box(
        "tower_hand_h", (0.08, 0.07, 0.55), (tx, ty - 2.06, roof_z + 4.62), "ink", root, bevel=0.01
    )
    k.box(
        "tower_hand_m",
        (0.62, 0.07, 0.08),
        (tx - 0.25, ty - 2.06, roof_z + 4.4),
        "ink",
        root,
        bevel=0.01,
    )
    k.box("belfry", (3.0, 3.0, 1.9), (tx, ty, roof_z + 6.9), "stone_dark", root, bevel=0.08)
    for sx in (-1, 1):
        k.box(
            f"belfry_arch_{'l' if sx < 0 else 'r'}",
            (0.9, 0.14, 1.3),
            (tx + sx * 0.8, ty - 1.52, roof_z + 6.9),
            "ink",
            root,
            bevel=0.04,
        )
    k.cylinder("bell", 0.62, 0.9, (tx, ty, roof_z + 6.8), "brass", root, verts=16, emissive=0.4)
    k.box("tower_roof_a", (3.6, 3.6, 0.4), (tx, ty, roof_z + 8.0), "roof", root, bevel=0.08)
    k.box("tower_roof_b", (2.4, 2.4, 0.9), (tx, ty, roof_z + 8.6), "roof", root, bevel=0.1)
    k.cylinder("finial", 0.16, 1.2, (tx, ty, roof_z + 9.6), "brass", root, verts=10, emissive=0.6)

    # The notice board on the forecourt: the board's open items, in wood and paper.
    bx, by = -W / 2 + 2.2, y_front - 3.6
    for sx in (-1, 1):
        k.box(
            f"notice_post_{'l' if sx < 0 else 'r'}",
            (0.2, 0.2, 2.4),
            (bx + sx * 1.5, by, base + 1.2),
            "wood_dark",
            root,
            bevel=0.03,
        )
    k.box("notice_board", (3.4, 0.18, 2.0), (bx, by, base + 2.0), "wood", root, bevel=0.05)
    for i, (nx, nz) in enumerate(((-0.9, 2.35), (0.2, 2.5), (1.1, 2.15), (-0.4, 1.65))):
        k.box(
            f"notice_{i}",
            (0.75, 0.06, 0.55),
            (bx + nx, by - 0.14, base + nz),
            "paper",
            root,
            bevel=0.01,
            emissive=0.5,
        )
    # The flagpole on the other side.
    k.cylinder(
        "flag_pole", 0.13, 6.0, (W / 2 - 1.8, y_front - 2.6, base + 3.0), "metal", root, verts=8
    )
    k.box(
        "flag",
        (1.9, 0.08, 1.1),
        (W / 2 - 0.85, y_front - 2.6, base + 5.4),
        "fam_civic",
        root,
        bevel=0.03,
        emissive=0.8,
    )

    # A civic front carries its name in the tympanum, not on a neon board.
    k.text_mesh(
        "name",
        "TOWN HALL",
        0.68,
        0.14,
        (0, y_front - 2.42, arch_top + 0.62),
        "ink",
        root,
        emissive=0.0,
    )
    return root


# ---------------------------------------------------------------------------
# 6. The Observatory — the web and the browser
# ---------------------------------------------------------------------------


def build_observatory(k) -> bpy.types.Object:
    """The lookout: a round tower under a slotted dome with the telescope
    pointing out of it, a railed terrace with a standing glass, and a weather
    vane. Where an agent stands while it reads the world outside. 5 x 5 tiles."""
    D = 9.4
    root = k.new_root(
        "observatory",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "observatory",
                "footprint": [5, 5],
                "forward": "+Z",
                "door": [0.0, 3.4],
                "stand": [0, 6.6],
                "sign": [0.0, 6.0, 0],
                "height_m": 14.2,
            }
        },
    )
    y_front = -D / 2
    base = 0.4
    # A round building gets a round plinth: a square slab under it reads as a
    # stray platform from the island's fixed 50° camera.
    k.cylinder("plinth", 4.9, base, (0, 0, base / 2), "plinth", root, verts=24)
    # The drum: a low round base carrying the tower.
    k.cylinder("drum", 4.2, 1.5, (0, 0, base + 0.75), "stone", root, verts=24)
    k.cylinder("drum_band", 4.35, 0.3, (0, 0, base + 1.35), "stone_dark", root, verts=24)
    tower_h = 7.2
    k.cylinder("tower", 3.3, tower_h, (0, 0, base + 1.5 + tower_h / 2), "wall", root, verts=24)
    for i in range(3):
        k.cylinder(
            f"tower_band_{i}",
            3.36,
            0.35,
            (0, 0, base + 3.0 + i * 2.1),
            "fam_web",
            root,
            verts=24,
            emissive=0.7,
        )
    # Windows: four tall slits round the shaft, one facing the viewer.
    for i, ang in enumerate((0.0, 0.9, -0.9, math.pi)):
        k.box(
            f"slit_{i}",
            (0.9, 0.16, 2.4),
            (math.sin(ang) * 3.28, -math.cos(ang) * 3.28, base + 4.2),
            "glass",
            root,
            bevel=0.03,
            emissive=0.45,
            rot=(0, 0, -ang),
        )
    top_z = base + 1.5 + tower_h
    # The terrace: a ring deck with posts and a rail.
    k.cylinder("terrace", 4.1, 0.3, (0, 0, top_z + 0.15), "trim", root, verts=24)
    for i in range(12):
        a = i / 12 * math.pi * 2
        k.box(
            f"rail_post_{i}",
            (0.12, 0.12, 0.85),
            (math.sin(a) * 3.85, math.cos(a) * 3.85, top_z + 0.72),
            "metal",
            root,
            bevel=0.02,
        )
    k.cylinder("rail_top", 3.96, 0.13, (0, 0, top_z + 1.12), "metal", root, verts=24)
    # The dome, its shutter and the telescope leaning out of it.
    k.cylinder("dome_base", 2.85, 0.5, (0, 0, top_z + 0.55), "wall_shade", root, verts=24)
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=24, ring_count=12, radius=2.7, location=(0, 0, top_z + 0.8)
    )
    dome = bpy.context.active_object
    dome.name = "dome"
    dome.scale = (1.0, 1.0, 0.7)
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.bisect(
        plane_co=(0, 0, top_z + 0.8), plane_no=(0, 0, 1), clear_inner=True, use_fill=True
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    dome.data.materials.clear()
    dome.data.materials.append(k.material("metal"))
    dome.parent = root
    # The slit the telescope looks out of, sunk into the dome.
    k.box(
        "dome_slot",
        (1.1, 2.6, 1.2),
        (0, -1.5, top_z + 1.4),
        "screen",
        root,
        bevel=0.04,
        rot=(0.45, 0, 0),
    )
    k.cylinder(
        "scope", 0.55, 4.8, (0, -1.7, top_z + 2.4), "steel_dark", root, verts=16, rot=(1.0, 0, 0)
    )
    k.cylinder(
        "scope_lens",
        0.6,
        0.3,
        (0, -3.7, top_z + 3.6),
        "fam_web",
        root,
        verts=16,
        rot=(1.0, 0, 0),
        emissive=2.2,
    )
    k.cylinder("scope_mount", 0.38, 1.6, (0, -0.9, top_z + 1.2), "steel", root, verts=12)
    # The vane on top of the dome.
    k.cylinder("vane_pole", 0.1, 1.8, (0, 0, top_z + 2.9), "brass", root, verts=8)
    k.box("vane_arrow", (1.4, 0.08, 0.45), (0.45, 0, top_z + 3.7), "brass", root, bevel=0.03)
    k.box("vane_tail", (0.55, 0.08, 0.65), (-0.45, 0, top_z + 3.7), "brass", root, bevel=0.03)

    # The door at the foot, its little porch, and a standing glass on the plaza.
    k.box("door_porch", (3.4, 2.4, 3.0), (0, y_front + 0.9, base + 1.5), "wall", root, bevel=0.08)
    k.box("porch_roof", (4.0, 2.9, 0.32), (0, y_front + 0.9, base + 3.15), "roof", root, bevel=0.06)
    k.box("door", (1.4, 0.18, 2.4), (0, y_front - 0.26, base + 1.2), "wood_dark", root, bevel=0.04)
    _lamp(k, root, "lamp_l", (-2.5, y_front - 0.1, base), "fam_web")
    _lamp(k, root, "lamp_r", (2.5, y_front - 0.1, base), "fam_web")
    gx, gy = -3.6, y_front - 0.4
    k.cylinder("glass_stand", 0.26, 2.0, (gx, gy, base + 1.0), "metal", root, verts=10)
    k.cylinder(
        "glass_tube",
        0.32,
        1.5,
        (gx, gy, base + 2.5),
        "steel_dark",
        root,
        verts=12,
        rot=(1.1, 0, 0.3),
    )
    k.cylinder(
        "glass_lens",
        0.36,
        0.2,
        (gx + 0.18, gy - 0.6, base + 3.1),
        "fam_web",
        root,
        verts=12,
        rot=(1.1, 0, 0.3),
        emissive=1.8,
    )

    # The sign hangs on the shaft above the porch roof, where the tower's own
    # curve gives it a back — a board floating in front of a cylinder reads as
    # a mistake from every angle but one.
    _sign(k, root, "LOOKOUT", 3.6, 0, y_front - 0.85, base + 4.75, "fam_web", size=0.72)
    return root


TOWN_BUILDERS = {
    "signal-office": build_signal_office,
    "control-room": build_control_room,
    "gallery-hall": build_gallery_hall,
    "model-boilerhouse": build_model_boilerhouse,
    "town-hall": build_town_hall,
    "observatory": build_observatory,
}


def main(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", default="signal-office", choices=sorted(TOWN_BUILDERS) + ["all"]
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--keep-scene", action="store_true")
    parser.add_argument(
        "--preview-dir", default=None, help="render a dimetric preview per target into this folder"
    )
    parser.add_argument("--preview-scale", type=float, default=26.0)
    args = parser.parse_args(argv)
    k = _base()
    for key, value in TOWN_PALETTE.items():
        k.PALETTE.setdefault(key, value)
    targets = sorted(TOWN_BUILDERS) if args.target == "all" else [args.target]
    for target in targets:
        if not args.keep_scene:
            k.clear_scene()
        else:
            # A previous run of this target may have left its tree behind.
            stale = bpy.data.objects.get(target)
            if stale is not None:
                k.select_tree(stale)
                bpy.ops.object.delete(use_global=False)
        from pixel_kit import build

        root = build(target, k)
        path = k.export_glb(root, Path(args.out))
        print(f"exported {path} ({path.stat().st_size} bytes)")
        if args.preview_dir:
            shot = Path(args.preview_dir) / f"{target}.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            k.render_preview(root, shot, args.preview_scale)
            print(f"preview {shot}")
        if args.keep_scene:
            k.select_tree(root)
            bpy.ops.object.delete(use_global=False)


if __name__ == "__main__":
    own = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    main(own)
elif "KIT_ARGS" in globals():
    main(globals()["KIT_ARGS"])
