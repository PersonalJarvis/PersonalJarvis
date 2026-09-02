# ruff: noqa: E501
"""The Memory House — the society's shared memory as a building
(``docs/agent-society/memory-house.md`` §3.5), built on the helpers of
``build_world_kit.py`` and kept in its own module so sessions never edit the
same file. Run inside Blender::

    blender -b --python scripts/world/kit_memory_house.py -- --out <kit dir> [--preview out.png]

or through the Blender MCP with ``KIT_ARGS`` (and ``KIT_BASE_PATH``) set;
``--keep-scene`` leaves everything else in the scene alone and removes only
the building it made after export.

The look: a dark slate plinth with a luminous reflecting ring, a translucent
glass monolith with a glowing memory core and floating memory shards inside,
a cantilevered roof slab with a light edge, a tilted halo ring with light
nodes, two data pylons flanking the entrance, and a floating MEMORY sign.
Nodes the island animates by name: ``core_orb``, ``halo``, ``shard_<n>``,
``pool_ring``, ``pylon_light_<l|r>``. Contract (§4.1): origin at the ground
centre, the front (door) faces Blender −Y = glTF +Z, metres.
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


MEMORY_PALETTE = {
    "slate": "#343948",
    "obsidian": "#15171e",
    "mem_glass": "#9fd8ff",
    "mem_frame": "#dfe9f3",
    "mem_core": "#7df9ff",
    "mem_core_hot": "#eafdff",
    "mem_violet": "#b388ff",
    "mem_pool": "#4cc9f0",
    "mem_gold": "#ffd166",
}

# Footprint 8 x 6 tiles (16 x 12 m); the monolith is 9 x 7 m and 12 m tall.
W, D = 16.0, 12.0
MONO_W, MONO_D, MONO_H = 9.0, 7.0, 12.0
PLINTH_Z = 0.45


def _glass(k, key: str, alpha: float, emissive: float = 0.0) -> bpy.types.Material:
    """A translucent flat material; the island keeps the alpha (the glTF alphaMode)."""
    mat = k.material(key, emissive)
    mat = mat.copy()
    mat.name = f"kit_{key}_a{int(alpha * 100)}"
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    rgba = k.hex_rgba(k.PALETTE[key])
    bsdf.inputs["Base Color"].default_value = (rgba[0], rgba[1], rgba[2], alpha)
    if "Alpha" in bsdf.inputs:
        bsdf.inputs["Alpha"].default_value = alpha
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    if hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    mat.show_transparent_back = False
    return mat


def _torus(k, name, major, minor, at, mat_key, parent, rot=(0, 0, 0), emissive=0.0, segments=32):
    """A ring in the XY plane before `rot` (own helper: the base module's is optional)."""
    bpy.ops.mesh.primitive_torus_add(
        location=at, rotation=rot, major_radius=major, minor_radius=minor,
        major_segments=segments, minor_segments=8,
    )
    obj = bpy.context.active_object
    obj.name = name
    _set_material(obj, k.material(mat_key, emissive))
    obj.parent = parent
    return obj


def _icosphere(k, name, radius, at, mat, parent, subdivisions=2):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions, radius=radius, location=at)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.parent = parent
    bpy.ops.object.shade_smooth()
    return obj


def _set_material(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def build_memory_house(k) -> bpy.types.Object:
    root = k.new_root(
        "memory-house",
        {
            "jarvis_building": {
                "contract": 1,
                "id": "memory-house",
                "footprint": [8, 6],
                "forward": "+Z",
                "door": [0, 3.6],
                "stand": [0, 8.5],
                "sign": [0, 15.5, 0],
                "lights": ["core_orb", "halo", "pool_ring", "pylon_light_l", "pylon_light_r"],
                "height_m": 15.0,
            }
        },
    )
    y_front = -D / 2

    # --- plinth: a dark slate slab with a lit reflecting ring cut into it
    k.box("plinth", (W + 0.6, D + 0.6, PLINTH_Z), (0, 0, PLINTH_Z / 2), "slate", root, bevel=0.12)
    k.box("plinth_band", (W + 0.9, D + 0.9, 0.12), (0, 0, 0.06), "obsidian", root, bevel=0.0)
    pool = _torus(k, "pool_ring", 6.1, 0.14, (0, 0.4, PLINTH_Z + 0.02), "mem_pool", root, emissive=2.2, segments=36)
    pool.scale = (1.0, 0.8, 1.0)
    bpy.context.view_layer.objects.active = pool
    bpy.ops.object.transform_apply(scale=True)
    # entrance steps down to the plaza (front)
    for i in range(3):
        k.box(
            f"step_{i}",
            (6.0 - i * 0.6, 0.9, PLINTH_Z - i * 0.15),
            (0, y_front - 0.25 - i * 0.8, (PLINTH_Z - i * 0.15) / 2),
            "slate",
            root,
            bevel=0.0,
        )

    # --- the monolith: a translucent glass block on four light frame columns
    z0 = PLINTH_Z
    glass = _glass(k, "mem_glass", 0.32, emissive=0.25)
    mono = k.box("monolith", (MONO_W, MONO_D, MONO_H), (0, 0.4, z0 + MONO_H / 2), "mem_glass", root, bevel=0.22)
    _set_material(mono, glass)
    for sx in (-1, 1):
        for sy in (-1, 1):
            k.box(
                f"column_{'l' if sx < 0 else 'r'}{'f' if sy < 0 else 'b'}",
                (0.42, 0.42, MONO_H + 0.6),
                (sx * (MONO_W / 2 + 0.05), 0.4 + sy * (MONO_D / 2 + 0.05), z0 + (MONO_H + 0.6) / 2),
                "mem_frame",
                root,
                bevel=0.06,
            )
    # horizontal light bands wrapping the glass at each memory layer
    for i, zz in enumerate((2.6, 5.4, 8.2, 11.0)):
        k.box(
            f"band_{i}",
            (MONO_W + 0.28, MONO_D + 0.28, 0.12),
            (0, 0.4, z0 + zz),
            "mem_core",
            root,
            bevel=0.0,
            emissive=1.8,
        )

    # --- inside: the memory core and the floating shards
    core_mat = k.material("mem_core_hot", 4.0)
    _icosphere(k, "core_orb", 2.0, (0, 0.4, z0 + 6.4), core_mat, root, subdivisions=2)
    inner_mat = k.material("mem_core", 2.4)
    _icosphere(k, "core_halo", 2.6, (0, 0.4, z0 + 6.4), _glass(k, "mem_core", 0.28, 1.2), root, subdivisions=1)
    k.cylinder("core_spine", 0.22, MONO_H - 0.8, (0, 0.4, z0 + MONO_H / 2), "mem_frame", root, verts=10, bevel=0.0, emissive=0.6)
    shard_specs = [
        (-2.6, -1.4, 2.4, 0.35, 1.3, 0.28),
        (2.4, 1.2, 3.6, -0.5, 1.5, -0.22),
        (-1.9, 1.9, 5.0, 0.8, 1.1, 0.4),
        (2.7, -1.6, 7.4, -0.3, 1.6, 0.15),
        (-2.4, 0.6, 8.9, 0.55, 1.2, -0.35),
        (1.7, 2.1, 10.2, -0.7, 1.4, 0.3),
        (0.9, -2.3, 4.3, 0.2, 1.0, -0.5),
    ]
    for i, (x, y, z, rz, size, rx) in enumerate(shard_specs):
        k.box(
            f"shard_{i}",
            (size * 1.4, 0.1, size * 0.9),
            (x, 0.4 + y, z0 + z),
            "mem_violet",
            root,
            bevel=0.0,
            emissive=1.9,
            rot=(rx, 0, rz),
        )
    # light strips on the floor inside the glass (data lanes)
    for i, x in enumerate((-3.2, -1.6, 0, 1.6, 3.2)):
        k.box(f"lane_{i}", (0.12, MONO_D - 1.0, 0.04), (x, 0.4, z0 + 0.06), "mem_core", root, bevel=0.0, emissive=1.6)
    del inner_mat

    # --- the door: a dark portal in the front glass with a light frame
    k.box("door_frame", (3.4, 0.5, 4.4), (0, y_front + 2.5 + 0.4 - 0.0, z0 + 2.2), "mem_frame", root, bevel=0.06)
    k.box("door_frame_glow", (3.0, 0.2, 4.0), (0, y_front + 2.5 + 0.4 - 0.2, z0 + 2.0), "mem_core", root, bevel=0.0, emissive=2.0)
    k.box("door", (2.4, 0.3, 3.5), (0, y_front + 2.5 + 0.4 - 0.3, z0 + 1.75), "obsidian", root, bevel=0.03)

    # --- the roof: a cantilevered dark slab floating above the glass, lit edge
    roof_z = z0 + MONO_H + 0.8
    k.box("roof_slab", (MONO_W + 2.6, MONO_D + 2.0, 0.36), (0, 0.4 - 0.5, roof_z), "slate", root, bevel=0.1)
    k.box("roof_edge", (MONO_W + 2.8, MONO_D + 2.2, 0.08), (0, 0.4 - 0.5, roof_z - 0.22), "mem_core", root, bevel=0.0, emissive=1.7)
    k.box("roof_fin", (0.8, MONO_D + 0.4, 1.0), (MONO_W / 2 + 0.9, 0.4, roof_z + 0.65), "obsidian", root, bevel=0.06)
    for sx in (-1, 1):
        k.cylinder(f"roof_post_{'l' if sx < 0 else 'r'}", 0.16, 0.9, (sx * 3.2, 0.4, z0 + MONO_H + 0.45), "mem_frame", root, verts=8, bevel=0.0)

    # --- the halo: a tilted ring of light nodes orbiting above the roof
    halo_z = z0 + 9.4
    halo = _torus(k, "halo", 6.2, 0.11, (0, 0.4, halo_z), "mem_frame", root, rot=(math.radians(14), 0, 0), emissive=0.9, segments=28)
    node_mat = k.material("mem_gold", 3.2)
    for i in range(8):
        a = i / 8 * math.pi * 2
        x = math.cos(a) * 6.2
        y = math.sin(a) * 6.2
        # tilt the node ring with the halo (rotation about X by 14 deg)
        yy = y * math.cos(math.radians(14))
        zz = y * math.sin(math.radians(14))
        n = _icosphere(k, f"halo_node_{i}", 0.3, (x, 0.4 + yy, halo_z + zz), node_mat, root, subdivisions=1)
        n.parent = halo
        n.matrix_parent_inverse = halo.matrix_world.inverted()

    # --- two data pylons flanking the steps: slim prisms with a light seam
    for sx, tag in ((-1, "l"), (1, "r")):
        px = sx * 5.6
        py = y_front - 0.6
        k.box(f"pylon_{tag}", (0.9, 0.9, 6.2), (px, py, z0 + 3.1), "obsidian", root, bevel=0.06)
        k.box(f"pylon_light_{tag}", (0.18, 0.95, 5.4), (px, py, z0 + 3.1), "mem_core", root, bevel=0.0, emissive=2.6)
        k.box(f"pylon_cap_{tag}", (1.1, 1.1, 0.25), (px, py, z0 + 6.35), "mem_frame", root, bevel=0.04)

    # --- the sign: floating letters above the door with a thin light rail
    k.box("sign_rail", (7.6, 0.14, 0.14), (0, y_front + 0.9, z0 + 5.4), "mem_frame", root, bevel=0.0, emissive=0.5)
    k.text_mesh("sign_text", "MEMORY", 1.25, 0.22, (0, y_front + 1.05, z0 + 6.25), "mem_core_hot", root, emissive=3.2)

    # --- garden edges: two low planters with light-blue shrubs
    for sx in (-1, 1):
        k.box(f"planter_{'l' if sx < 0 else 'r'}", (2.4, 1.2, 0.6), (sx * (W / 2 - 1.6), 0.4 + D / 2 - 1.0, z0 + 0.3), "slate", root, bevel=0.05)
        k.cylinder(f"shrub_{'l' if sx < 0 else 'r'}", 0.7, 0.9, (sx * (W / 2 - 1.6), 0.4 + D / 2 - 1.0, z0 + 1.0), "mem_pool", root, verts=10, bevel=0.25)
    return root


def main(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--preview", default=None)
    parser.add_argument("--keep-scene", action="store_true")
    args = parser.parse_args(argv)
    k = _base()
    for key, value in MEMORY_PALETTE.items():
        k.PALETTE.setdefault(key, value)
    if not args.keep_scene:
        k.clear_scene()
    else:
        stale = bpy.data.objects.get("memory-house")
        if stale is not None:
            k.select_tree(stale)
            bpy.ops.object.delete(use_global=False)
    root = build_memory_house(k)
    tris = sum(
        len(o.data.loop_triangles) if o.type == "MESH" and (o.data.calc_loop_triangles() or True) else 0
        for o in root.children_recursive
    )
    print(f"memory-house: {len(root.children_recursive)} objects, {tris} triangles")
    path = k.export_glb(root, Path(args.out))
    print(f"exported {path} ({path.stat().st_size} bytes)")
    if args.preview:
        # Only this building in the picture: other trees in a kept scene hide for the render.
        mine = {root, *root.children_recursive}
        hidden = [o for o in bpy.data.objects if o not in mine and not o.hide_render]
        for o in hidden:
            o.hide_render = True
        try:
            k.render_preview(root, Path(args.preview))
        finally:
            for o in hidden:
                o.hide_render = False
        print(f"preview {args.preview}")
        for name in ("kit_preview_cam", "kit_sun"):
            obj = bpy.data.objects.get(name)
            if obj is not None and args.keep_scene:
                bpy.data.objects.remove(obj, do_unlink=True)
    if args.keep_scene:
        k.select_tree(root)
        bpy.ops.object.delete(use_global=False)


if __name__ == "__main__":
    own = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    main(own)
elif "KIT_ARGS" in globals():
    main(globals()["KIT_ARGS"])
