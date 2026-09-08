"""Build the island's World Kit buildings in Blender — reproducible, headless.

The ONLY producer of the GLBs under
``jarvis/ui/web/frontend/src/assets/society/world/kit/`` (world-masterplan-v2.md §4).
Runs inside Blender's Python::

    blender -b --python scripts/world/build_world_kit.py -- --target plugin-docks \
        --out jarvis/ui/web/frontend/src/assets/society/world/kit [--preview out.png]

or, during development, through the Blender MCP (``exec(open(path).read())`` with
``KIT_ARGS`` set) — the MCP is the fast inner loop, never a build dependency.

Contract (§4.1): origin at the footprint's ground centre, the FRONT (door side)
faces glTF +Z, which is Blender −Y; metres; bevelled edges; one flat material
per colour; the root empty carries ``jarvis_building`` extras.

Never imports ``jarvis.*`` — Blender's bundled interpreter knows nothing of it.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# Palette — the world's own (worldPalette.ts), plus the plugin-family accents
# ---------------------------------------------------------------------------

PALETTE = {
    "wall": "#f7f3ea",
    "wall_shade": "#e6e0d2",
    "trim": "#c9c2b2",
    "plinth": "#b8a58a",
    "glass": "#8ed2f0",
    "roof": "#1d3557",
    "roof_line": "#3e5f95",
    "wood": "#b57f45",
    "wood_dark": "#8c5e2f",
    "metal": "#8b8f9c",
    "sign_board": "#22243a",
    "neon": "#ff5fa2",
    "neon_glow": "#ffd166",
    "plug": "#ffd166",
    "plug_dark": "#e0a800",
    # one accent per plugin family — the bay stripes
    "fam_brain": "#9b5de5",
    "fam_tool": "#2ec4b6",
    "fam_stt": "#ff6f61",
    "fam_tts": "#ffb703",
    "fam_channel": "#06d6a0",
    "fam_realtime": "#4cc9f0",
    # the Agent Foundry: brushed steel, hazard stripes and the assembly core
    "steel": "#9aa2b1",
    "steel_dark": "#5d6472",
    "belt": "#3a3f4f",
    "hazard": "#ffb703",
    "hazard_dark": "#2b2f42",
    "core": "#4cc9f0",
    "core_bright": "#b8f2ff",
    "copper": "#d98d4a",
}

FAMILY_BAYS = ["fam_brain", "fam_tool", "fam_stt", "fam_tts", "fam_channel", "fam_realtime"]


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(hex_color: str) -> tuple[float, float, float, float]:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), 1.0)


_materials: dict[str, bpy.types.Material] = {}


def material(key: str, emissive: float = 0.0) -> bpy.types.Material:
    """One flat Principled material per palette key (+ optional emission)."""
    name = f"kit_{key}" + ("_glow" if emissive else "")
    if name in _materials:
        return _materials[name]
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    rgba = hex_rgba(PALETTE[key])
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = 0.85
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.2
    if emissive:
        bsdf.inputs["Emission Color"].default_value = rgba
        bsdf.inputs["Emission Strength"].default_value = emissive
    _materials[name] = mat
    return mat


# ---------------------------------------------------------------------------
# Primitive helpers (all sizes in metres; Blender Z is up, front is −Y)
# ---------------------------------------------------------------------------


def _finish(
    obj: bpy.types.Object, mat: bpy.types.Material, bevel: float, parent: bpy.types.Object
) -> bpy.types.Object:
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    if bevel > 0:
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 1
        mod.limit_method = "ANGLE"
        mod.angle_limit = math.radians(40)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)
    obj.parent = parent
    return obj


def box(
    name: str,
    size: tuple[float, float, float],
    at: tuple[float, float, float],
    mat_key: str,
    parent: bpy.types.Object,
    bevel: float = 0.06,
    emissive: float = 0.0,
    rot: tuple[float, float, float] = (0, 0, 0),
) -> bpy.types.Object:
    """A bevelled box; `at` is its centre."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=at, rotation=rot)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0], size[1], size[2])
    bpy.ops.object.transform_apply(scale=True)
    return _finish(obj, material(mat_key, emissive), min(bevel, min(size) * 0.45), parent)


def cylinder(
    name: str,
    radius: float,
    depth: float,
    at: tuple[float, float, float],
    mat_key: str,
    parent: bpy.types.Object,
    verts: int = 16,
    rot: tuple[float, float, float] = (0, 0, 0),
    bevel: float = 0.04,
    emissive: float = 0.0,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=verts, radius=radius, depth=depth, location=at, rotation=rot
    )
    obj = bpy.context.active_object
    obj.name = name
    return _finish(obj, material(mat_key, emissive), bevel, parent)


def half_barrel(
    name: str,
    radius: float,
    length: float,
    at: tuple[float, float, float],
    mat_key: str,
    parent: bpy.types.Object,
) -> bpy.types.Object:
    """A half cylinder lying along X — the solar barrel roof of the village."""
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=24, radius=radius, depth=length, location=at, rotation=(0, math.pi / 2, 0)
    )
    obj = bpy.context.active_object
    obj.name = name
    # Cut away the lower half with a bisect in edit mode.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.bisect(plane_co=at, plane_no=(0, 0, 1), clear_inner=True, use_fill=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    return _finish(obj, material(mat_key), 0.05, parent)


def torus(
    name: str,
    major: float,
    minor: float,
    at: tuple[float, float, float],
    mat_key: str,
    parent: bpy.types.Object,
    rot: tuple[float, float, float] = (0, 0, 0),
    emissive: float = 0.0,
    segments: int = 24,
) -> bpy.types.Object:
    """A ring lying in the XY plane before `rot` — the foundry's core hoops."""
    bpy.ops.mesh.primitive_torus_add(
        location=at,
        rotation=rot,
        major_radius=major,
        minor_radius=minor,
        major_segments=segments,
        minor_segments=8,
    )
    obj = bpy.context.active_object
    obj.name = name
    return _finish(obj, material(mat_key, emissive), 0.0, parent)


def text_mesh(
    name: str,
    body: str,
    size: float,
    extrude: float,
    at: tuple[float, float, float],
    mat_key: str,
    parent: bpy.types.Object,
    emissive: float = 0.0,
) -> bpy.types.Object:
    """Extruded letters standing upright, facing the front (−Y)."""
    bpy.ops.object.text_add(location=at, rotation=(math.pi / 2, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    obj.data.body = body
    obj.data.size = size
    obj.data.resolution_u = 2
    obj.data.extrude = extrude
    obj.data.align_x = "CENTER"
    obj.data.align_y = "CENTER"
    bpy.ops.object.convert(target="MESH")
    obj = bpy.context.active_object
    return _finish(obj, material(mat_key, emissive), 0.0, parent)


# ---------------------------------------------------------------------------
# Buildings
# ---------------------------------------------------------------------------


def new_root(name: str, extras: dict) -> bpy.types.Object:
    root = bpy.data.objects.new(name, None)
    root.empty_display_size = 0.5
    bpy.context.scene.collection.objects.link(root)
    for k, v in extras.items():
        root[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
    return root


def build_plugin_docks() -> bpy.types.Object:
    """The Plugin Store — 'Plugin Docks': a hall with six loading bays, one per
    plugin family, a giant plug on the roof and a retro neon sign. Footprint
    8 × 5 tiles (16 × 10 m); the front with the bays faces −Y (glTF +Z)."""
    W, D = 16.0, 10.0  # footprint
    H = 4.6  # hall height
    root = new_root(
        "plugin-docks",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "plugin-docks",
                "footprint": [8, 5],
                "forward": "+Z",
                "door": [0, 5.0],
                "stand": [0, 7.0],
                "sign": [0, 9.6, 0],
                "height_m": 10.8,
            }
        },
    )
    # Plinth and hall body
    box("plinth", (W + 0.8, D + 0.8, 0.35), (0, 0, 0.175), "plinth", root, bevel=0.08)
    box("hall", (W, D, H), (0, 0, 0.35 + H / 2), "wall", root, bevel=0.14)
    # Shade band along the base of the walls
    box("base_band", (W + 0.1, D + 0.1, 0.5), (0, 0, 0.6), "wall_shade", root, bevel=0.05)
    # Roof: flat slab with an overhang, then the solar barrel along X
    roof_z = 0.35 + H
    box("roof_slab", (W + 1.0, D + 1.0, 0.4), (0, 0, roof_z + 0.2), "trim", root, bevel=0.1)
    half_barrel("solar_barrel", D * 0.36, W * 0.62, (-W * 0.12, 0.6, roof_z + 0.4), "roof", root)
    box(
        "barrel_ridge",
        (W * 0.62, 0.35, 0.12),
        (-W * 0.12, 0.6, roof_z + 0.4 + D * 0.36),
        "roof_line",
        root,
        bevel=0.03,
    )
    for i in range(4):
        box(
            f"barrel_seam_{i}",
            (0.12, D * 0.7, 0.14),
            (-W * 0.12 - W * 0.24 + i * W * 0.16, 0.6, roof_z + 0.4 + D * 0.36 * 0.72),
            "roof_line",
            root,
            bevel=0.02,
        )

    # The plug on the roof — the plugin symbol everyone knows
    px, py, pz = W * 0.28, 0.8, roof_z + 0.4
    cylinder("plug_body", 1.45, 1.9, (px, py, pz + 0.95), "plug", root, verts=24, bevel=0.12)
    box("plug_cable", (0.5, 0.5, 1.2), (px, py, pz + 0.6 - 0.9), "plug_dark", root, bevel=0.12)
    for dx in (-0.55, 0.55):
        box(
            f"prong_{'l' if dx < 0 else 'r'}",
            (0.34, 0.34, 2.2),
            (px + dx, py, pz + 1.9 + 1.0),
            "metal",
            root,
            bevel=0.08,
        )
    box("plug_face", (1.9, 1.9, 0.3), (px, py, pz + 1.95), "plug_dark", root, bevel=0.08)

    # Front: six loading bays with roll-up doors, one accent per plugin family
    bay_w = 2.1
    gap = (W - 6 * bay_w) / 7
    y_front = -D / 2
    for i, fam in enumerate(FAMILY_BAYS):
        x = -W / 2 + gap + bay_w / 2 + i * (bay_w + gap)
        box(
            f"bay_frame_{i}",
            (bay_w + 0.3, 0.35, 3.1),
            (x, y_front - 0.1, 0.35 + 1.55),
            "trim",
            root,
            bevel=0.05,
        )
        box(
            f"bay_door_{i}",
            (bay_w, 0.18, 2.6),
            (x, y_front - 0.2, 0.35 + 1.3),
            "wall_shade",
            root,
            bevel=0.03,
        )
        # roll-up slats
        for s in range(5):
            box(
                f"bay_slat_{i}_{s}",
                (bay_w - 0.2, 0.06, 0.08),
                (x, y_front - 0.31, 0.35 + 0.5 + s * 0.5),
                "trim",
                root,
                bevel=0.0,
            )
        # family stripe over the door
        box(
            f"bay_stripe_{i}",
            (bay_w + 0.3, 0.22, 0.55),
            (x, y_front - 0.22, 0.35 + 3.1 + 0.95),
            fam,
            root,
            bevel=0.04,
            emissive=0.6,
        )
        # a small lamp above each bay
        box(
            f"bay_lamp_{i}",
            (0.5, 0.35, 0.2),
            (x, y_front - 0.45, 0.35 + 4.55),
            "neon_glow",
            root,
            bevel=0.03,
            emissive=2.0,
        )

    # Retro striped awning over the bays
    for i in range(12):
        x = -W / 2 + 0.2 + i * (W - 0.4) / 12 + (W - 0.4) / 24
        key = "neon" if i % 2 == 0 else "wall"
        box(
            f"awning_{i}",
            ((W - 0.4) / 12, 1.6, 0.12),
            (x, y_front - 0.9, 0.35 + 3.35),
            key,
            root,
            bevel=0.02,
            rot=(math.radians(-18), 0, 0),
        )

    # The sign: a dark board on the roof edge with glowing letters
    box(
        "sign_board",
        (W * 0.66, 0.5, 2.2),
        (-W * 0.08, y_front + 0.6, roof_z + 1.55),
        "sign_board",
        root,
        bevel=0.1,
    )
    box(
        "sign_rim",
        (W * 0.66 + 0.3, 0.3, 2.5),
        (-W * 0.08, y_front + 0.75, roof_z + 1.55),
        "neon",
        root,
        bevel=0.08,
        emissive=0.8,
    )
    text_mesh(
        "sign_text",
        "PLUGINS",
        1.45,
        0.18,
        (-W * 0.08, y_front + 0.3, roof_z + 1.5),
        "neon_glow",
        root,
        emissive=3.0,
    )

    # Side windows: a glass band on both ends
    for sx in (-1, 1):
        box(
            f"glass_band_{'l' if sx < 0 else 'r'}",
            (0.15, D * 0.7, 1.1),
            (sx * (W / 2 + 0.02), 0.4, 0.35 + 2.9),
            "glass",
            root,
            bevel=0.02,
            emissive=0.35,
        )
    # Back: two vents and a service door
    for i, x in enumerate((-3.5, 3.5)):
        cylinder(
            f"vent_{i}", 0.45, 1.6, (x, D / 2 - 1.2, roof_z + 0.4 + 0.8), "metal", root, verts=12
        )
    box(
        "back_door", (1.6, 0.16, 2.4), (W * 0.3, D / 2 + 0.05, 0.35 + 1.2), "wood", root, bevel=0.03
    )

    # Forecourt furniture: two planters and a crate stack
    for x in (-W / 2 + 1.2, W / 2 - 1.2):
        box(
            f"planter_{int(x)}",
            (1.4, 1.0, 0.7),
            (x, y_front - 2.2, 0.35),
            "wood_dark",
            root,
            bevel=0.05,
        )
        cylinder(
            f"bush_{int(x)}",
            0.6,
            0.9,
            (x, y_front - 2.2, 1.05),
            "fam_channel",
            root,
            verts=10,
            bevel=0.2,
        )
    box("crate_a", (1.0, 1.0, 1.0), (W / 2 - 3.2, y_front - 2.4, 0.85), "wood", root, bevel=0.05)
    box(
        "crate_b",
        (0.8, 0.8, 0.8),
        (W / 2 - 3.2, y_front - 2.4, 1.75),
        "wood_dark",
        root,
        bevel=0.05,
    )
    return root


def _robot_arm(root: bpy.types.Object, side: int, x: float, y: float, z: float) -> None:
    """One assembly manipulator beside the conveyor.

    A column with a boom reaching in over the belt and a gripper hanging off
    its end — the shape reads as "factory arm" at island zoom, where a
    multi-jointed arm would just be a tangle. `side` is -1 (left of the ramp)
    or +1 (right); the pair frames the door a new agent walks out of.
    """
    s = "l" if side < 0 else "r"
    cylinder(f"arm_foot_{s}", 0.95, 0.5, (x, y, z + 0.25), "steel_dark", root, verts=12)
    cylinder(f"arm_turn_{s}", 0.78, 0.5, (x, y, z + 0.72), "hazard", root, verts=12)
    box(f"arm_column_{s}", (0.95, 0.95, 3.4), (x, y, z + 2.6), "steel", root, bevel=0.08)
    # hazard banding at the foot of the column
    for k in range(3):
        box(
            f"arm_band_{s}{k}",
            (1.02, 1.02, 0.16),
            (x, y, z + 1.35 + k * 0.42),
            "hazard" if k % 2 == 0 else "hazard_dark",
            root,
            bevel=0.02,
        )
    cylinder(
        f"arm_shoulder_{s}",
        0.55,
        1.3,
        (x, y, z + 4.35),
        "steel_dark",
        root,
        verts=12,
        rot=(0, math.pi / 2, 0),
    )
    # the boom, reaching in over the belt
    reach = 3.1
    bx = x - side * reach / 2
    box(f"arm_boom_{s}", (reach, 0.6, 0.6), (bx, y, z + 4.35), "steel", root, bevel=0.07)
    box(
        f"arm_boom_rail_{s}",
        (reach - 0.3, 0.66, 0.12),
        (bx, y, z + 4.68),
        "copper",
        root,
        bevel=0.03,
    )
    # the head at the boom's tip
    hx = x - side * reach
    box(f"arm_head_{s}", (0.9, 0.9, 0.7), (hx, y, z + 4.1), "steel_dark", root, bevel=0.08)
    for j, dx in enumerate((-0.32, 0.32)):
        box(
            f"arm_claw_{s}{j}",
            (0.16, 0.55, 0.75),
            (hx + dx, y, z + 3.4),
            "steel",
            root,
            bevel=0.04,
        )
    box(
        f"arm_lamp_{s}",
        (0.42, 0.42, 0.22),
        (hx, y, z + 3.35),
        "core_bright",
        root,
        bevel=0.06,
        emissive=2.4,
    )


def build_agent_foundry() -> bpy.types.Object:
    """The Agent Foundry — where a new agent is assembled and walks out of the
    door (world-masterplan-v2.md §5, world-behaviour-manual.md §2 `foundry`).

    A wide steel-and-plaster hall: corner columns, a sawtooth skylight roof, a
    tall arched portal with a glowing containment field in the middle of the
    front, a conveyor ramp running out of it onto the square, two assembly arms
    over the ramp and the assembly core in a crowned tower on the roof.
    Footprint 10 × 7 tiles (20 × 13 m); the portal faces −Y (glTF +Z).
    """
    W, D = 20.0, 13.0  # footprint
    PL = 0.45  # plinth
    H = 6.4  # hall height
    roof_z = PL + H
    y_front = -D / 2
    root = new_root(
        "agent-foundry",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "agent-foundry",
                "footprint": [10, 7],
                "forward": "+Z",
                # glTF metres: +Z is the front. The door is the portal mouth,
                # `stand` the foot of the conveyor ramp where a figure appears.
                "door": [0, 6.5],
                "stand": [0, 13.0],
                "ramp_end": [0, 13.4],
                "sign": [0, 15.5, 0],
                "height_m": 14.6,
            }
        },
    )

    # ---- plinth, hall body, corner columns --------------------------------
    box("plinth", (W + 1.0, D + 1.0, PL), (0, 0, PL / 2), "plinth", root, bevel=0.1)
    box("hall", (W, D, H), (0, 0, PL + H / 2), "wall", root, bevel=0.16)
    box("base_band", (W + 0.12, D + 0.12, 0.7), (0, 0, PL + 0.35), "wall_shade", root, bevel=0.06)
    for sx in (-1, 1):
        for sy in (-1, 1):
            box(
                f"column_{'w' if sx < 0 else 'e'}{'n' if sy > 0 else 's'}",
                (1.15, 1.15, H + 0.5),
                (sx * (W / 2 - 0.2), sy * (D / 2 - 0.2), PL + (H + 0.5) / 2),
                "steel",
                root,
                bevel=0.1,
            )

    # ---- roof: overhanging slab, copper fascia, sawtooth skylights ---------
    box("roof_slab", (W + 1.4, D + 1.4, 0.45), (0, 0, roof_z + 0.22), "trim", root, bevel=0.12)
    for i, (fw, fd, fx, fy) in enumerate(
        (
            (W + 1.5, 0.4, 0.0, -(D + 1.4) / 2),
            (W + 1.5, 0.4, 0.0, (D + 1.4) / 2),
            (0.4, D + 1.5, -(W + 1.4) / 2, 0.0),
            (0.4, D + 1.5, (W + 1.4) / 2, 0.0),
        )
    ):
        box(f"roof_fascia_{i}", (fw, fd, 0.3), (fx, fy, roof_z + 0.5), "copper", root, bevel=0.05)
    for i, ox in enumerate((-6.0, -1.4, 3.2)):
        box(
            f"skylight_glass_{i}",
            (3.4, D * 0.62, 0.18),
            (ox, 0.6, roof_z + 1.45),
            "glass",
            root,
            bevel=0.03,
            emissive=0.9,
            rot=(0, math.radians(38), 0),
        )
        box(
            f"skylight_back_{i}",
            (2.4, D * 0.62, 0.18),
            (ox + 2.4, 0.6, roof_z + 1.5),
            "roof",
            root,
            bevel=0.03,
            rot=(0, math.radians(-58), 0),
        )
    # cooling fins along the back edge
    for i in range(6):
        box(
            f"fin_{i}",
            (0.22, 2.6, 1.1),
            (-6.5 + i * 2.6, D / 2 - 1.6, roof_z + 1.0),
            "steel_dark",
            root,
            bevel=0.04,
        )

    # ---- the portal: the door a new agent walks out of ---------------------
    PW, PH = 6.8, 5.4  # portal opening
    # recessed dark bay so the glow reads as depth, not as a sticker
    box(
        "portal_bay",
        (PW, 1.2, PH),
        (0, y_front + 0.5, PL + PH / 2),
        "hazard_dark",
        root,
        bevel=0.06,
    )
    box(
        "portal_field",
        (PW - 0.7, 0.28, PH - 0.7),
        (0, y_front + 0.02, PL + PH / 2 - 0.2),
        "core",
        root,
        bevel=0.05,
        emissive=2.6,
    )
    # heavy frame: two posts, a lintel and the arch above it
    for sx in (-1, 1):
        box(
            f"portal_post_{'l' if sx < 0 else 'r'}",
            (0.9, 1.5, PH + 0.5),
            (sx * (PW / 2 + 0.35), y_front - 0.25, PL + (PH + 0.5) / 2),
            "steel",
            root,
            bevel=0.08,
        )
        # hazard stripes on the lower posts
        for k in range(4):
            box(
                f"portal_stripe_{'l' if sx < 0 else 'r'}{k}",
                (0.98, 0.2, 0.28),
                (sx * (PW / 2 + 0.35), y_front - 1.02, PL + 0.45 + k * 0.62),
                "hazard" if k % 2 == 0 else "hazard_dark",
                root,
                bevel=0.02,
            )
    box(
        "portal_lintel",
        (PW + 2.4, 1.5, 0.9),
        (0, y_front - 0.25, PL + PH + 0.45),
        "steel_dark",
        root,
        bevel=0.08,
    )
    half_barrel("portal_arch", 0.7, PW + 2.4, (0, y_front - 0.05, PL + PH + 0.95), "copper", root)
    # threshold plate and the two guide rails leaving the door
    box(
        "portal_sill",
        (PW + 0.6, 1.9, 0.2),
        (0, y_front - 0.5, PL + 0.05),
        "steel_dark",
        root,
        bevel=0.04,
    )
    # side panels: recessed plaster with a lit window band over each
    for sx in (-1, 1):
        px = sx * (PW / 2 + 3.9)
        box(
            f"front_panel_{'l' if sx < 0 else 'r'}",
            (5.2, 0.3, H - 0.9),
            (px, y_front - 0.12, PL + (H - 0.9) / 2),
            "wall_shade",
            root,
            bevel=0.06,
        )
        box(
            f"front_ledge_{'l' if sx < 0 else 'r'}",
            (5.4, 0.5, 0.22),
            (px, y_front - 0.25, PL + 3.4),
            "trim",
            root,
            bevel=0.04,
        )
        box(
            f"front_window_{'l' if sx < 0 else 'r'}",
            (4.4, 0.22, 1.3),
            (px, y_front - 0.3, PL + 4.3),
            "glass",
            root,
            bevel=0.04,
            emissive=0.5,
        )
    # a row of small lamps under the arch
    for i in range(5):
        box(
            f"portal_lamp_{i}",
            (0.42, 0.3, 0.22),
            (-3.2 + i * 1.6, y_front - 1.05, PL + PH + 0.05),
            "core",
            root,
            bevel=0.04,
            emissive=2.2,
        )

    # ---- the conveyor ramp out of the portal -------------------------------
    RL = 7.0  # ramp length
    ry = y_front - RL / 2 - 0.4
    box("ramp_bed", (6.2, RL, 0.34), (0, ry, PL - 0.1), "belt", root, bevel=0.05)
    for sx in (-1, 1):
        box(
            f"ramp_rail_{'l' if sx < 0 else 'r'}",
            (0.42, RL, 0.62),
            (sx * 3.1, ry, PL + 0.12),
            "steel_dark",
            root,
            bevel=0.06,
        )
        box(
            f"ramp_stripe_{'l' if sx < 0 else 'r'}",
            (0.46, RL, 0.12),
            (sx * 3.1, ry, PL + 0.44),
            "hazard",
            root,
            bevel=0.02,
        )
    for i in range(9):
        cylinder(
            f"ramp_roller_{i}",
            0.26,
            5.9,
            (0, y_front - 0.9 - i * 0.74, PL + 0.06),
            "steel",
            root,
            verts=10,
            rot=(0, math.pi / 2, 0),
            bevel=0.02,
        )
    # a lit strip down the middle of the belt — the path the figure takes
    box(
        "ramp_guide",
        (0.5, RL - 0.5, 0.08),
        (0, ry, PL + 0.14),
        "core",
        root,
        bevel=0.02,
        emissive=1.6,
    )

    # ---- assembly arms over the ramp ---------------------------------------
    _robot_arm(root, -1, -4.6, y_front - 2.6, PL)
    _robot_arm(root, 1, 4.6, y_front - 2.6, PL)

    # ---- the assembly core in its crowned tower ----------------------------
    cylinder("core_base", 3.9, 0.9, (0, 1.2, roof_z + 0.85), "steel", root, verts=20, bevel=0.1)
    cylinder("core_shaft", 3.2, 3.6, (0, 1.2, roof_z + 3.1), "wall", root, verts=20, bevel=0.1)
    cylinder("core_glass", 3.3, 0.9, (0, 1.2, roof_z + 2.3), "glass", root, verts=20, emissive=0.7)
    cylinder("core_collar", 3.7, 0.4, (0, 1.2, roof_z + 4.9), "copper", root, verts=20, bevel=0.06)
    # four struts hold the crown open so the core is visible from every side
    for i in range(4):
        a = math.pi / 4 + i * math.pi / 2
        box(
            f"core_strut_{i}",
            (0.4, 0.4, 2.4),
            (math.cos(a) * 2.6, 1.2 + math.sin(a) * 2.6, roof_z + 6.1),
            "steel_dark",
            root,
            bevel=0.05,
        )
    cylinder(
        "core_orb", 1.6, 2.9, (0, 1.2, roof_z + 6.5), "core_bright", root, verts=16, emissive=3.0
    )
    torus("core_ring_a", 2.7, 0.16, (0, 1.2, roof_z + 6.5), "copper", root)
    torus(
        "core_ring_b",
        2.5,
        0.14,
        (0, 1.2, roof_z + 6.5),
        "core",
        root,
        rot=(math.radians(62), 0, 0),
        emissive=1.4,
    )
    torus(
        "core_ring_c",
        2.5,
        0.14,
        (0, 1.2, roof_z + 6.5),
        "core",
        root,
        rot=(0, math.radians(62), 0),
        emissive=1.4,
    )
    cylinder("core_cap", 1.9, 0.5, (0, 1.2, roof_z + 8.3), "steel", root, verts=20, bevel=0.08)
    cylinder("core_mast", 0.24, 2.0, (0, 1.2, roof_z + 9.5), "metal", root, verts=8)
    box(
        "core_beacon",
        (0.7, 0.7, 0.7),
        (0, 1.2, roof_z + 10.6),
        "hazard",
        root,
        bevel=0.1,
        emissive=2.8,
    )

    # ---- the sign on the front roof edge -----------------------------------
    box(
        "sign_board",
        (W * 0.52, 0.55, 2.3),
        (0, y_front + 0.5, roof_z + 2.6),
        "sign_board",
        root,
        bevel=0.1,
    )
    box(
        "sign_rim",
        (W * 0.52 + 0.34, 0.32, 2.62),
        (0, y_front + 0.66, roof_z + 2.6),
        "core",
        root,
        bevel=0.08,
        emissive=0.9,
    )
    text_mesh(
        "sign_text",
        "FOUNDRY",
        1.28,
        0.18,
        (0, y_front + 0.2, roof_z + 2.55),
        "core_bright",
        root,
        emissive=3.0,
    )

    # ---- side and back: glass bands, vents, service door --------------------
    for sx in (-1, 1):
        box(
            f"glass_band_{'l' if sx < 0 else 'r'}",
            (0.18, D * 0.62, 1.4),
            (sx * (W / 2 + 0.03), 0.8, PL + 4.1),
            "glass",
            root,
            bevel=0.02,
            emissive=0.4,
        )
    for i, x in enumerate((-4.6, 4.6)):
        cylinder(f"vent_{i}", 0.55, 1.8, (x, D / 2 - 1.5, roof_z + 1.1), "metal", root, verts=12)
    box(
        "back_door",
        (1.8, 0.18, 2.6),
        (W * 0.28, D / 2 + 0.06, PL + 1.3),
        "steel_dark",
        root,
        bevel=0.04,
    )

    # ---- forecourt: bollards, part racks, crates ---------------------------
    for sx in (-1, 1):
        bx = sx * 4.3
        cylinder(
            f"bollard_{'l' if sx < 0 else 'r'}",
            0.3,
            1.5,
            (bx, y_front - 7.4, PL + 0.6),
            "steel_dark",
            root,
            verts=10,
        )
        box(
            f"bollard_lamp_{'l' if sx < 0 else 'r'}",
            (0.5, 0.5, 0.34),
            (bx, y_front - 7.4, PL + 1.45),
            "hazard",
            root,
            bevel=0.06,
            emissive=2.0,
        )
    box(
        "rack_frame",
        (3.4, 1.2, 2.6),
        (-W / 2 + 2.0, y_front - 2.4, PL + 1.3),
        "steel_dark",
        root,
        bevel=0.06,
    )
    for i in range(2):
        box(
            f"rack_part_{i}",
            (2.6, 0.9, 0.5),
            (-W / 2 + 2.0, y_front - 2.4, PL + 0.75 + i * 1.1),
            "copper" if i == 0 else "core",
            root,
            bevel=0.06,
            emissive=1.2 if i == 1 else 0.0,
        )
    box(
        "crate_a", (1.2, 1.2, 1.2), (W / 2 - 2.2, y_front - 2.6, PL + 0.6), "wood", root, bevel=0.06
    )
    box(
        "crate_b",
        (0.95, 0.95, 0.95),
        (W / 2 - 2.2, y_front - 2.6, PL + 1.68),
        "wood_dark",
        root,
        bevel=0.06,
    )
    box(
        "crate_c",
        (1.0, 1.0, 1.0),
        (W / 2 - 3.6, y_front - 2.2, PL + 0.5),
        "steel",
        root,
        bevel=0.06,
    )
    return root


BUILDERS = {"plugin-docks": build_plugin_docks, "agent-foundry": build_agent_foundry}


# ---------------------------------------------------------------------------
# Export and preview
# ---------------------------------------------------------------------------


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.curves):
        for item in list(block):
            if item.users == 0:
                block.remove(item)
    _materials.clear()


def select_tree(root: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    root.select_set(True)
    for child in root.children_recursive:
        child.select_set(True)
    bpy.context.view_layer.objects.active = root


def export_glb(root: bpy.types.Object, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{root.name}.glb"
    select_tree(root)
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
        use_active_scene=True,
        export_apply=True,
        export_yup=True,
        export_animations=False,
        export_extras=True,
        export_texcoords=True,
        export_vertex_color="MATERIAL",
        export_normals=True,
        export_materials="EXPORT",
    )
    return path


def render_preview(
    root: bpy.types.Object, path: Path, ortho: float = 30.0, yaw_deg: float = 45.0
) -> None:
    """A dimetric preview from the island's camera angle (50° pitch, 45° yaw).

    `ortho` is the ground width the frame shows; give a wide building more.
    `yaw_deg` swings the camera around so a facade can be checked head on.
    """
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("kit_preview_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho
    cam = bpy.data.objects.new("kit_preview_cam", cam_data)
    scene.collection.objects.link(cam)
    dist = 60
    pitch, yaw = math.radians(50), math.radians(yaw_deg)
    cam.location = (
        dist * math.cos(pitch) * math.sin(yaw),
        -dist * math.cos(pitch) * math.cos(yaw),
        dist * math.sin(pitch) + 3,
    )
    cam.rotation_euler = (math.pi / 2 - pitch, 0, yaw)
    scene.camera = cam
    sun_data = bpy.data.lights.new("kit_sun", "SUN")
    sun_data.energy = 3.5
    sun_data.angle = math.radians(6)
    sun = bpy.data.objects.new("kit_sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(45), math.radians(-25), math.radians(20))
    world = scene.world or bpy.data.worlds.new("kit_world")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = hex_rgba("#a5dbff")
        bg.inputs[1].default_value = 1.0
    scene.render.engine = (
        "BLENDER_EEVEE"
        if "BLENDER_EEVEE"
        in {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
        else "BLENDER_EEVEE_NEXT"
    )
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 900
    scene.render.film_transparent = False
    scene.render.filepath = str(path)
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)


def main(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="plugin-docks", choices=sorted(BUILDERS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--preview", default=None)
    parser.add_argument("--preview-scale", type=float, default=30.0)
    parser.add_argument("--preview-yaw", type=float, default=45.0)
    parser.add_argument(
        "--keep-scene", action="store_true", help="do not clear the scene first (MCP inner loop)"
    )
    args = parser.parse_args(argv)
    if not args.keep_scene:
        clear_scene()
    from pixel_kit import build

    root = build(args.target, sys.modules[__name__])
    path = export_glb(root, Path(args.out))
    print(f"exported {path} ({path.stat().st_size} bytes)")
    if args.preview:
        render_preview(root, Path(args.preview), args.preview_scale, args.preview_yaw)
        print(f"preview {args.preview}")


if __name__ == "__main__":
    # `blender -b --python this.py -- <args>`: everything after `--` is ours.
    own = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    main(own)
elif "KIT_ARGS" in globals():
    main(globals()["KIT_ARGS"])  # the MCP inner loop: exec() with KIT_ARGS set
