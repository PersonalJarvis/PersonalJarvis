"""Modular pixel-diorama architecture, shared by all twelve building targets.

Geometry, entrances and clearances originate in world-manifest.json. Builds
retain asset IDs and animated node names; imported user assets are never read.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "jarvis/ui/web/frontend/src/assets/society/world/world-manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
ASSETS = {v["asset"]: v for v in MANIFEST["buildings"].values()}
ACCENTS = {
    "plugin-docks": "fam_tool",
    "skill-forge": "fam_tts",
    "relay-tower": "core",
    "terminal-cantina": "fam_channel",
    "agent-foundry": "hazard",
    "signal-office": "fam_stt",
    "control-room": "fam_brain",
    "gallery-hall": "fam_realtime",
    "model-boilerhouse": "copper",
    "town-hall": "plug",
    "observatory": "fam_realtime",
    "memory-house": "fam_brain",
}
LABELS = {
    "plugin-docks": "PLUGINS",
    "skill-forge": "SKILLS",
    "relay-tower": "RELAY",
    "terminal-cantina": "TERMINAL",
    "agent-foundry": "FOUNDRY",
    "signal-office": "SIGNAL",
    "control-room": "CONTROL",
    "gallery-hall": "GALLERY",
    "model-boilerhouse": "MODELS",
    "town-hall": "TOWN HALL",
    "observatory": "LOOKOUT",
    "memory-house": "MEMORY",
}


def build(asset_id: str, k):
    spec = ASSETS[asset_id]
    width, depth = spec["sizeM"]
    accent = ACCENTS[asset_id]
    root = k.new_root(asset_id, {})
    root["jarvis_building"] = json.dumps(
        {
            "contract": 2,
            "id": asset_id,
            "style": "pixel-diorama",
            "forward": "+Z",
            "footprint": [width / 2, depth / 2],
            "collision": spec["collision"],
            "door": spec["door"],
            "stand": spec["stand"],
            "sign": spec["sign"],
            "height_m": spec["heightM"],
            "ramps": spec["ramps"],
        }
    )
    # Leave the peripheral strip clear: the declared wall includes every
    # planter, step and light at body height, including the side and back views.
    w, d = width - 1.2, depth - 1.2
    front = -depth / 2 + 0.60
    h = 4.6 if asset_id not in ("town-hall", "agent-foundry") else 5.6

    def b(name, size, pos, mat="wall", bevel=0.06):
        return k.box(name, size, pos, mat, root, bevel=bevel)

    b("foundation", (width, depth, 0.18), (0, 0, 0.09), "plinth", 0)
    b("hall", (w, d, h), (0, 0, 0.18 + h / 2))
    b("wall_foot", (w + 0.1, d + 0.1, 0.4), (0, 0, 0.38), "wall_shade", 0)
    # Strong horizontal structure, readable in the far view.
    b("roof", (w + 0.35, d + 0.35, 0.42), (0, 0, h + 0.36), "roof", 0)
    b("cornice", (w + 0.42, d + 0.42, 0.16), (0, 0, h + 0.12), accent, 0)
    for sign in [-1, 1]:
        b(f"corner_{sign}", (0.36, d, h), (sign * (w / 2 - 0.15), 0, 0.18 + h / 2), "trim", 0)
        for side in [-1, 1]:
            # Side windows and back windows make all four fixed views designed.
            b(
                f"side_window_{sign}_{side}",
                (0.08, 1.5, 1.75),
                (sign * (w / 2 + 0.02), side * d * 0.24, 2.8),
                "glass",
                0,
            )
            b(
                f"side_sill_{sign}_{side}",
                (0.20, 1.7, 0.18),
                (sign * (w / 2 + 0.02), side * d * 0.24, 1.9),
                "roof",
                0,
            )
        b(f"back_window_{sign}", (2.1, 0.08, 1.8), (sign * w * 0.26, d / 2 + 0.02, 2.8), "glass", 0)
    # The real entrance lines up with the manifest, rather than a second table.
    door_x = spec["door"][0]
    b("entrance_frame", (2.55, 0.24, 3.25), (door_x, front - 0.03, 1.82), "roof", 0)
    b("entrance", (2.12, 0.12, 2.92), (door_x, front - 0.18, 1.67), "glass", 0)
    b("door_divider", (0.09, 0.15, 2.92), (door_x, front - 0.26, 1.67), "roof", 0)
    b("door_handle", (0.52, 0.10, 0.10), (door_x + 0.47, front - 0.35, 1.5), "plug", 0)
    b("threshold", (2.75, 0.50, 0.16), (door_x, front - 0.27, 0.17), "trim", 0)
    # Large family sign, anchored into the facade instead of floating above it.
    sign_z = h - 0.30
    sign_w = min(w - 1.1, max(4.1, len(LABELS[asset_id]) * 0.58))
    b("sign_board", (sign_w, 0.22, 0.95), (0, front - 0.16, sign_z), "sign_board", 0)
    text = k.text_mesh(
        "sign_letters",
        LABELS[asset_id],
        0.61,
        0.008,
        (0, front - 0.29, sign_z - 0.24),
        "wall",
        root,
    )
    # Exact dimensions prevent a long label from leaving its board.
    if text.dimensions.x > sign_w - 0.45:
        text.scale.x *= (sign_w - 0.45) / text.dimensions.x
    for sign in [-1, 1]:
        x = sign * (w / 2 - 0.7)
        b(f"planter_{sign}", (0.85, 0.8, 0.65), (x, front + 0.15, 0.5), "roof", 0)
        b(f"plant_{sign}", (0.63, 0.63, 0.48), (x, front + 0.15, 1.0), "fam_channel", 0.10)
        b(f"lamp_{sign}", (0.26, 0.28, 0.55), (sign * 1.65 + door_x, front - 0.12, 2.9), "plug", 0)

    top = h + 0.58
    if asset_id == "plugin-docks":
        # Three large modular wings; the central public entrance remains clear.
        for i, color in enumerate(["fam_brain", "fam_tool", "fam_channel"]):
            x = (i - 1) * 4.1
            b(f"module_{i}", (3.65, 3.8, 1.6), (x, 0.35, top + 0.8), "roof", 0)
            b(f"module_cap_{i}", (3.75, 3.9, 0.20), (x, 0.35, top + 1.64), color, 0)
            for slot in [-1, 1]:
                b(
                    f"socket_{i}_{slot}",
                    (0.56, 0.16, 0.62),
                    (x + slot * 0.65, -1.61, top + 0.81),
                    "glass",
                    0,
                )
        b("plug_symbol", (1.75, 0.65, 1.30), (0, 0.2, top + 2.65), "plug", 0)
        for x in [-0.49, 0.49]:
            b(f"plug_pin_{x}", (0.25, 0.36, 0.75), (x, 0.2, top + 3.64), "metal", 0)
    elif asset_id == "skill-forge":
        b("forge_roof", (w * 0.68, d * 0.70, 1.7), (-0.8, 0.4, top + 0.85), "roof", 0.1)
        b("chimney", (1.45, 1.45, 3.8), (w * 0.32, d * 0.2, top + 1.9), "copper", 0)
        b("anvil_foot", (1.2, 1.0, 0.45), (-1.0, -0.3, top + 1.92), "metal", 0)
        b("anvil", (3.0, 1.1, 0.55), (-1.0, -0.3, top + 2.42), "steel_dark", 0.08)
    elif asset_id == "relay-tower":
        b("mast", (0.7, 0.7, 7.0), (0, 0.8, top + 3.5), "steel_dark", 0)
        for i in range(3):
            k.torus(
                f"signal_ring_{i}",
                1.9 - i * 0.40,
                0.10,
                (0, 0.8, top + 3.0 + i * 1.4),
                "core",
                root,
                rot=(math.pi / 2, 0, 0),
                segments=12,
            )
    elif asset_id == "terminal-cantina":
        b("terminal_screen", (w * 0.67, 0.65, 2.5), (0, 0.5, top + 1.45), "sign_board", 0)
        k.text_mesh("prompt", ">_", 1.55, 0.015, (0, 0.14, top + 0.75), "fam_channel", root)
        for sign in [-1, 1]:
            b(f"awning_{sign}", (2.5, 1.0, 0.23), (sign * w * 0.28, front + 0.05, 3.5), accent, 0)
    elif asset_id == "agent-foundry":
        b("machine_left", (3.2, 3.6, 3.2), (-w * 0.30, 0.8, top + 1.6), "steel_dark", 0)
        b("machine_right", (3.2, 3.6, 3.2), (w * 0.30, 0.8, top + 1.6), "steel_dark", 0)
        k.cylinder("core_orb", 1.65, 2.3, (0, 0.2, top + 1.6), "core", root, verts=8, bevel=0)
        b("core_beacon", (0.65, 0.65, 1.7), (0, 0.2, top + 3.6), "plug", 0)
        for sign in [-1, 1]:
            b(f"arm_{sign}", (3.0, 0.5, 0.5), (sign * 3.0, -0.3, top + 2.9), "copper", 0)
    elif asset_id == "signal-office":
        b("envelope", (4.6, 0.7, 2.65), (0, 0.2, top + 1.6), accent, 0)
        for sign in [-1, 1]:
            obj = b(
                f"envelope_fold_{sign}",
                (2.4, 0.11, 0.14),
                (sign * 0.95, -0.21, top + 1.9),
                "wall",
                0,
            )
            obj.rotation_euler.y = sign * -0.50
    elif asset_id == "control-room":
        b("control_tower", (w * 0.57, d * 0.62, 3.4), (1.2, 0.35, top + 1.7), "roof", 0)
        b(
            "control_glass",
            (w * 0.57 + 0.1, d * 0.62 + 0.1, 1.1),
            (1.2, 0.35, top + 2.6),
            "glass",
            0,
        )
        b("tower_lid", (w * 0.65, d * 0.70, 0.25), (1.2, 0.35, top + 3.55), accent, 0)
    elif asset_id == "gallery-hall":
        for i, color in enumerate(["fam_stt", "plug", "fam_tool"]):
            b(
                f"gallery_frame_{i}",
                (2.5, 0.4, 2.6 + i * 0.4),
                ((i - 1) * 3.2, 0.2, top + 1.5 + i * 0.2),
                "roof",
                0,
            )
            b(
                f"gallery_work_{i}",
                (2.1, 0.12, 2.2 + i * 0.4),
                ((i - 1) * 3.2, -0.07, top + 1.5 + i * 0.2),
                color,
                0,
            )
    elif asset_id == "model-boilerhouse":
        for sign in [-1, 1]:
            k.cylinder(
                f"model_tank_{sign}",
                1.35,
                4.4,
                (sign * 2, 0.3, top + 2.2),
                "copper",
                root,
                verts=8,
                bevel=0,
            )
            for j in range(3):
                k.cylinder(
                    f"tank_band_{sign}_{j}",
                    1.41,
                    0.18,
                    (sign * 2, 0.3, top + 0.7 + j * 1.3),
                    "roof",
                    root,
                    verts=8,
                    bevel=0,
                )
    elif asset_id == "town-hall":
        b("civic_tower", (3.6, 3.4, 4.4), (0, 0.2, top + 2.2), "wall", 0)
        b("clock_face", (2.1, 0.12, 2.1), (0, -1.58, top + 2.7), "roof", 0)
        b("clock_hand", (0.14, 0.10, 0.8), (0, -1.68, top + 2.95), "plug", 0)
        b("clock_hand_short", (0.70, 0.10, 0.14), (0.3, -1.68, top + 2.55), "plug", 0)
        b("civic_crown", (4.2, 4.0, 0.4), (0, 0.2, top + 4.6), "roof", 0)
    elif asset_id == "observatory":
        k.cylinder("lookout_drum", 2.4, 2.5, (0, 0.2, top + 1.25), "roof", root, verts=12, bevel=0)
        k.cylinder(
            "telescope",
            0.65,
            4.0,
            (0, -0.1, top + 3.0),
            "steel_dark",
            root,
            verts=8,
            rot=(math.pi / 3, 0, -0.35),
            bevel=0,
        )
        k.cylinder(
            "telescope_lens",
            0.70,
            0.18,
            (-0.6, -1.73, top + 4.0),
            "glass",
            root,
            verts=8,
            rot=(math.pi / 3, 0, -0.35),
            bevel=0,
        )
    elif asset_id == "memory-house":
        for i in range(3):
            b(
                f"archive_stack_{i}",
                (w * 0.58, d * 0.62, 1.0),
                (0, 0.25, top + 0.6 + i * 1.15),
                "roof",
                0,
            )
            b(
                f"band_{i}",
                (w * 0.58 + 0.05, 0.1, 0.18),
                (0, -d * 0.31 + 0.18, top + 0.6 + i * 1.15),
                accent,
                0,
            )
        k.cylinder("core_orb", 0.70, 1.4, (0, 0.25, top + 4.0), "core", root, verts=8, bevel=0)
        k.torus("halo", 1.55, 0.10, (0, 0.25, top + 4.1), accent, root, segments=12)
    finalize(root, k, spec)
    return root


def finalize(root, k, spec):
    """Bake palette and four-value cavity shading; merge static geometry.

    No per-colour draw calls. Two materials plus the few named moving parts
    remain, and vertex colours survive the runtime's toon restyling.
    """
    static = []
    moving = {"core_orb", "core_beacon", "halo"}
    neutral = k.material("wall").copy()
    neutral.name = "diorama-palette"
    bsdf = neutral.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (1, 1, 1, 1)
    attr = neutral.node_tree.nodes.new("ShaderNodeVertexColor")
    attr.layer_name = "DioramaColor"
    neutral.node_tree.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    bpy.context.view_layer.update()
    for obj in list(root.children_recursive):
        if obj.type != "MESH":
            continue
        # A single segment bevel gives intentional pixel planes at low cost.
        colors = obj.data.color_attributes.new(
            name="DioramaColor", type="FLOAT_COLOR", domain="CORNER"
        )
        original = obj.data.materials[0].node_tree.nodes.get("Principled BSDF")
        rgba = original.inputs["Base Color"].default_value
        for poly in obj.data.polygons:
            n = obj.matrix_world.to_3x3() @ poly.normal
            shade = 1 if n.z > 0.6 else 0.94 if n.y < -0.3 else 0.80 if n.x > 0.3 else 0.72
            for li in poly.loop_indices:
                colors.data[li].color = (rgba[0] * shade, rgba[1] * shade, rgba[2] * shade, 1)
        obj.data.materials.clear()
        obj.data.materials.append(neutral)
        if obj.name not in moving:
            static.append(obj)
    if static:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in static:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = static[0]
        bpy.ops.object.join()
        body = bpy.context.object
        body.name = "Architecture"
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # Source sizes are authoritative; correct the label/height from actual geometry.
    top = max(
        (obj.matrix_world @ Vector(v)).z
        for obj in root.children_recursive
        if obj.type == "MESH"
        for v in obj.bound_box
    )
    metadata = json.loads(root["jarvis_building"])
    metadata["height_m"] = round(top, 4)
    root["jarvis_building"] = json.dumps(metadata)


def build_all(out: Path, k):
    for asset_id in ASSETS:
        k.clear_scene()
        root = build(asset_id, k)
        k.export_glb(root, out)
        print(f"Built pixel-diorama {asset_id}", flush=True)
