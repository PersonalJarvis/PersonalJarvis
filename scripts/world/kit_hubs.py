# ruff: noqa: E501
"""The Skill Forge, the Relay Tower and the Terminal Cantina — three more World Kit
hubs (world-masterplan-v2.md §5), built on the helpers of ``build_world_kit.py``.

Kept in its own module so two sessions can add buildings without editing the same
file; ``build_world_kit.py`` stays the home of the helpers, the contract and the
Plugin Docks / Agent Foundry. Run inside Blender::

    blender -b --python scripts/world/kit_hubs.py -- --target skill-forge --out <kit dir>

or through the Blender MCP with ``KIT_ARGS`` set. ``--keep-scene`` leaves whatever
else is in the scene alone and removes only the building it made after export.
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


HUB_PALETTE = {
    "fam_skill": "#ff9f1c",
    "fam_mcp": "#4cc9f0",
    "fam_cli": "#2ec4b6",
    "fam_channel": "#06d6a0",
    "screen": "#0f172a",
    "screen_glow": "#7ff0c8",
}


def _sign(k, root, text, width, x, y_front, z, rim_key):
    """A dark board with a coloured rim and glowing letters, hung on the front."""
    k.box("sign_board", (width, 0.5, 2.0), (x, y_front + 0.55, z), "sign_board", root, bevel=0.1)
    k.box(
        "sign_rim",
        (width + 0.3, 0.3, 2.3),
        (x, y_front + 0.7, z),
        rim_key,
        root,
        bevel=0.08,
        emissive=0.8,
    )
    k.text_mesh(
        "sign_text", text, 1.3, 0.18, (x, y_front + 0.25, z - 0.05), "neon_glow", root, emissive=3.0
    )


def build_skill_forge(k) -> bpy.types.Object:
    """The Skill Forge: a hall with a tall glass 'book' tower, a chimney whose top
    glows amber, three skylights and a stack of books by the door. 7 x 5 tiles."""
    W, D, H = 13.0, 10.0, 4.4
    root = k.new_root(
        "skill-forge",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "skill-forge",
                "footprint": [7, 5],
                "forward": "+Z",
                "door": [1.6, 5.0],
                "stand": [0, 7.5],
                "sign": [1.6, 9.0, 0],
                "height_m": 12.4,
            }
        },
    )
    y_front = -D / 2
    k.box("plinth", (W + 0.8, D + 0.8, 0.35), (0, 0, 0.175), "plinth", root, bevel=0.08)
    k.box("hall", (W, D, H), (0, 0, 0.35 + H / 2), "wall", root, bevel=0.14)
    k.box("base_band", (W + 0.1, D + 0.1, 0.5), (0, 0, 0.6), "wall_shade", root, bevel=0.05)
    roof_z = 0.35 + H
    k.box("roof_slab", (W + 1.0, D + 1.0, 0.4), (0, 0, roof_z + 0.2), "trim", root, bevel=0.1)
    for i, x in enumerate((-1.0, 1.8, 4.6)):
        k.box(
            f"skylight_{i}",
            (2.2, 3.4, 0.5),
            (x, 1.2, roof_z + 0.6),
            "glass",
            root,
            bevel=0.06,
            emissive=0.35,
        )
    tx = -W / 2 + 2.9
    k.box("tower", (5.0, 5.0, 11.2), (tx, 0.8, 0.35 + 5.6), "wall", root, bevel=0.16)
    for i in range(4):
        k.box(
            f"tower_band_{i}",
            (5.1, 5.1, 0.5),
            (tx, 0.8, 2.8 + i * 2.4),
            "glass",
            root,
            bevel=0.04,
            emissive=0.35,
        )
    k.box(
        "tower_cap", (5.6, 5.6, 0.5), (tx, 0.8, 0.35 + 11.2 + 0.25), "fam_skill", root, bevel=0.08
    )
    for i, key in enumerate(("fam_skill", "neon", "fam_mcp", "fam_channel", "neon_glow")):
        k.box(
            f"spine_{i}",
            (0.7, 0.2, 9.0),
            (tx - 2.0 + i * 1.0, y_front + 0.8 - 0.1, 0.35 + 5.4),
            key,
            root,
            bevel=0.03,
        )
    k.cylinder("chimney", 0.9, 4.0, (W / 2 - 1.6, 1.5, roof_z + 2.0), "metal", root, verts=14)
    k.cylinder(
        "chimney_glow",
        0.95,
        0.5,
        (W / 2 - 1.6, 1.5, roof_z + 4.1),
        "fam_skill",
        root,
        verts=14,
        emissive=2.5,
    )
    k.box("door_frame", (3.6, 0.3, 3.4), (1.6, y_front - 0.1, 0.35 + 1.7), "trim", root, bevel=0.05)
    k.box("door_l", (1.55, 0.16, 3.0), (0.8, y_front - 0.22, 0.35 + 1.5), "wood", root, bevel=0.03)
    k.box("door_r", (1.55, 0.16, 3.0), (2.4, y_front - 0.22, 0.35 + 1.5), "wood", root, bevel=0.03)
    k.box(
        "window_r",
        (2.2, 0.14, 1.2),
        (5.0, y_front - 0.08, 0.35 + 2.6),
        "glass",
        root,
        bevel=0.02,
        emissive=0.35,
    )
    for i, key in enumerate(("fam_skill", "neon", "fam_mcp")):
        k.box(
            f"book_{i}",
            (1.4 - i * 0.2, 1.0, 0.45),
            (4.6, y_front - 1.6, 0.35 + 0.22 + i * 0.45),
            key,
            root,
            bevel=0.05,
        )
    _sign(k, root, "SKILLS", 7.0, 1.6, y_front, roof_z + 1.5, "fam_skill")
    k.box(
        "planter",
        (1.4, 1.0, 0.7),
        (-W / 2 + 1.2, y_front - 2.0, 0.35),
        "wood_dark",
        root,
        bevel=0.05,
    )
    k.cylinder(
        "bush",
        0.6,
        0.9,
        (-W / 2 + 1.2, y_front - 2.0, 1.05),
        "fam_channel",
        root,
        verts=10,
        bevel=0.2,
    )
    return root


def build_relay_tower(k) -> bpy.types.Object:
    """The Relay Tower (MCP servers): a small base hall and a slim tower with a
    dish and an antenna. 5 x 5 tiles."""
    W, D, H = 9.0, 9.0, 3.4
    root = k.new_root(
        "relay-tower",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "relay-tower",
                "footprint": [5, 5],
                "forward": "+Z",
                "door": [0, 4.5],
                "stand": [0, 7.0],
                "sign": [0, 16.5, 0],
                "height_m": 17.0,
            }
        },
    )
    y_front = -D / 2
    k.box("plinth", (W + 0.8, D + 0.8, 0.35), (0, 0, 0.175), "plinth", root, bevel=0.08)
    k.box("base", (W, D, H), (0, 0, 0.35 + H / 2), "wall", root, bevel=0.14)
    k.box(
        "base_glass",
        (W + 0.1, D + 0.1, 0.9),
        (0, 0, 0.35 + 2.2),
        "glass",
        root,
        bevel=0.03,
        emissive=0.35,
    )
    k.box("base_roof", (W + 0.8, D + 0.8, 0.4), (0, 0, 0.35 + H + 0.2), "trim", root, bevel=0.1)
    k.box("door", (1.6, 0.16, 2.6), (0, y_front - 0.08, 0.35 + 1.3), "door", root, bevel=0.03)
    tz0 = 0.35 + H + 0.4
    k.cylinder("tower", 1.7, 10.0, (0, 0.6, tz0 + 5.0), "wall", root, verts=18, bevel=0.06)
    for i in range(3):
        k.cylinder(
            f"tower_ring_{i}",
            1.8,
            0.35,
            (0, 0.6, tz0 + 2.4 + i * 3.0),
            "fam_mcp",
            root,
            verts=18,
            bevel=0.03,
            emissive=0.5,
        )
    k.cylinder("tower_cap", 2.1, 0.5, (0, 0.6, tz0 + 10.25), "trim", root, verts=18, bevel=0.06)
    dz = tz0 + 10.5
    k.box("dish_mast", (0.35, 0.35, 1.6), (0, 0.6, dz + 0.8), "metal", root, bevel=0.05)
    k.cylinder(
        "dish",
        2.6,
        0.4,
        (0, -0.6, dz + 2.2),
        "wall",
        root,
        verts=24,
        rot=(math.radians(-55), 0, 0),
        bevel=0.08,
    )
    k.cylinder(
        "dish_hub",
        0.35,
        1.6,
        (0, -1.2, dz + 2.7),
        "metal",
        root,
        verts=10,
        rot=(math.radians(-55), 0, 0),
        bevel=0.03,
    )
    k.box("antenna", (0.2, 0.2, 3.2), (0, 0.6, dz + 3.4), "metal", root, bevel=0.03)
    k.box("beacon", (0.5, 0.5, 0.5), (0, 0.6, dz + 5.2), "neon", root, bevel=0.06, emissive=3.0)
    _sign(k, root, "MCP", 4.6, 0, y_front, 0.35 + H + 1.4, "fam_mcp")
    k.cylinder(
        "drum_a",
        0.7,
        0.7,
        (-W / 2 + 1.4, y_front - 1.8, 0.7),
        "wood",
        root,
        verts=12,
        rot=(0, math.pi / 2, 0),
    )
    k.cylinder(
        "drum_b",
        0.55,
        0.6,
        (W / 2 - 1.4, y_front - 1.7, 0.6),
        "wood_dark",
        root,
        verts=12,
        rot=(0, math.pi / 2, 0),
    )
    return root


def build_terminal_cantina(k) -> bpy.types.Object:
    """The Terminal Cantina (CLI seats): a low cafe with an open front, a long
    counter of glowing terminals under a striped awning. 7 x 5 tiles."""
    W, D, H = 13.5, 9.0, 3.6
    root = k.new_root(
        "terminal-cantina",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "terminal-cantina",
                "footprint": [7, 5],
                "forward": "+Z",
                "door": [0, 4.5],
                "stand": [0, 7.5],
                "sign": [-3.75, 7.6, 1.1],
                "height_m": 8.0,
            }
        },
    )
    y_front = -D / 2
    k.box("plinth", (W + 0.8, D + 0.8, 0.35), (0, 0, 0.175), "plinth", root, bevel=0.08)
    k.box("house", (W, D * 0.62, H), (0, D * 0.19, 0.35 + H / 2), "wall_shade", root, bevel=0.14)
    k.box(
        "house_glass",
        (W + 0.1, 0.16, 1.1),
        (0, y_front + D * 0.38 - 0.08, 0.35 + 2.3),
        "glass",
        root,
        bevel=0.02,
        emissive=0.35,
    )
    roof_z = 0.35 + H
    k.box(
        "roof_slab",
        (W + 0.6, D * 0.62 + 0.6, 0.4),
        (0, D * 0.19, roof_z + 0.2),
        "trim",
        root,
        bevel=0.1,
    )
    k.box(
        "roof_garden",
        (W - 1.0, D * 0.62 - 1.0, 0.3),
        (0, D * 0.19, roof_z + 0.55),
        "fam_channel",
        root,
        bevel=0.05,
    )
    for i, x in enumerate((-4.0, -1.0, 2.5, 5.0)):
        k.cylinder(
            f"roof_bush_{i}",
            0.7,
            1.0,
            (x, D * 0.19 + (i % 2) * 1.2 - 0.6, roof_z + 1.2),
            "fam_channel",
            root,
            verts=10,
            bevel=0.25,
        )
    k.cylinder("pipe", 0.35, 2.0, (W / 2 - 1.2, D * 0.4, roof_z + 1.0), "metal", root, verts=10)
    cy = y_front + 1.9
    k.box("counter", (W - 1.6, 0.9, 1.1), (0, cy, 0.35 + 0.55), "wood", root, bevel=0.05)
    k.box("counter_top", (W - 1.4, 1.1, 0.12), (0, cy, 0.35 + 1.16), "wood_dark", root, bevel=0.03)
    for i in range(6):
        x = -W / 2 + 1.7 + i * (W - 3.4) / 5
        k.box(
            f"screen_{i}",
            (1.3, 0.12, 0.9),
            (x, cy + 0.2, 0.35 + 1.75),
            "screen",
            root,
            bevel=0.03,
            rot=(math.radians(-12), 0, 0),
        )
        k.box(
            f"screen_glow_{i}",
            (1.1, 0.06, 0.7),
            (x, cy + 0.12, 0.35 + 1.75),
            "screen_glow",
            root,
            bevel=0.0,
            rot=(math.radians(-12), 0, 0),
            emissive=2.5,
        )
        k.cylinder(
            f"stool_{i}",
            0.35,
            0.7,
            (x, cy - 1.4, 0.35 + 0.35),
            "fam_cli",
            root,
            verts=10,
            bevel=0.06,
        )
    for x in (-W / 2 + 0.5, W / 2 - 0.5):
        k.box(
            f"post_{int(x)}",
            (0.3, 0.3, 3.4),
            (x, y_front - 0.2, 0.35 + 1.7),
            "metal",
            root,
            bevel=0.04,
        )
    for i in range(10):
        x = -W / 2 + 0.4 + i * (W - 0.8) / 10 + (W - 0.8) / 20
        key = "fam_cli" if i % 2 == 0 else "wall"
        k.box(
            f"awning_{i}",
            ((W - 0.8) / 10, D * 0.22, 0.12),
            (x, y_front + D * 0.27, 0.35 + 3.5),
            key,
            root,
            bevel=0.02,
            rot=(math.radians(-10), 0, 0),
        )
    _sign(k, root, "CLI", 4.4, -W / 2 + 3.0, y_front + D * 0.38, roof_z + 1.4, "fam_cli")
    k.box(
        "crate", (1.0, 1.0, 0.9), (W / 2 - 1.2, y_front - 1.4, 0.8), "wood_dark", root, bevel=0.05
    )
    return root


HUB_BUILDERS = {
    "skill-forge": build_skill_forge,
    "relay-tower": build_relay_tower,
    "terminal-cantina": build_terminal_cantina,
}


def main(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="skill-forge", choices=sorted(HUB_BUILDERS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--keep-scene", action="store_true")
    args = parser.parse_args(argv)
    k = _base()
    for key, value in {
        **HUB_PALETTE,
        "door": "#e0893b",
        "sign_board": "#22243a",
        "neon": "#ff5fa2",
        "neon_glow": "#ffd166",
    }.items():
        k.PALETTE.setdefault(key, value)
    if not args.keep_scene:
        k.clear_scene()
    else:
        # A previous run of this target may have left its tree behind (an aborted build).
        stale = bpy.data.objects.get(args.target)
        if stale is not None:
            k.select_tree(stale)
            bpy.ops.object.delete(use_global=False)
    from pixel_kit import build

    root = build(args.target, k)
    path = k.export_glb(root, Path(args.out))
    print(f"exported {path} ({path.stat().st_size} bytes)")
    if args.keep_scene:
        # Leave the scene as we found it: remove only what this run made.
        k.select_tree(root)
        bpy.ops.object.delete(use_global=False)


if __name__ == "__main__":
    own = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    main(own)
elif "KIT_ARGS" in globals():
    main(globals()["KIT_ARGS"])
