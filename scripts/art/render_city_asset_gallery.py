"""Render every inventoried GLB individually in Blender, with source hashes.

Run headlessly with --factory-startup --disable-autoexec --python this_file.
Optional script arguments: --group study|legacy|all, --limit N, --force.
The original assets are read only; animation-only assets get a labeled skeleton
visualization because their GLB deliberately contains no renderable geometry.
"""

import argparse
import csv
import hashlib
import json
import struct
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/city-realism-study"
GALLERY = STUDY / "render-gallery"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*color, 1)
    return mat


def light(name, location, power, size, color):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = power
    data.shape = "DISK"
    data.size = size
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (-Vector(location)).to_track_quat("-Z", "Y").to_euler()


def render_asset(source, target, detail=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    bpy.context.scene.frame_set(0)
    if detail and source.stem == "train":
        for name, offset in (("Door_Platform_Left", -1.25), ("Door_Platform_Right", 1.25)):
            door = bpy.data.objects.get(name)
            if door is not None:
                door.location.x += offset
    originals = [obj for obj in bpy.context.scene.objects if obj.visible_get()]
    kind = "asset geometry"
    if not any(obj.type == "MESH" for obj in originals):
        kind = "animation library; skeleton visualization"
        mat = material("Skeleton review aid", (0.08, 0.53, 0.67))
        for armature in [obj for obj in originals if obj.type == "ARMATURE"]:
            for bone in armature.data.bones:
                a = armature.matrix_world @ bone.head_local
                b = armature.matrix_world @ bone.tail_local
                delta = b - a
                if delta.length < 0.001:
                    continue
                bpy.ops.mesh.primitive_cylinder_add(
                    vertices=8,
                    radius=max(delta.length * 0.08, 0.008),
                    depth=delta.length,
                    location=(a + b) / 2,
                )
                obj = bpy.context.object
                obj.name = "Review skeleton bone"
                obj.rotation_euler = delta.to_track_quat("Z", "Y").to_euler()
                obj.data.materials.append(mat)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.visible_get()]
    bounds = []
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            bounds.extend(evaluated.matrix_world @ vertex.co for vertex in mesh.vertices)
        finally:
            evaluated.to_mesh_clear()
    if not bounds:
        raise ValueError(f"No reviewable mesh or skeleton in {source.name}")
    low = Vector(tuple(min(point[axis] for point in bounds) for axis in range(3)))
    high = Vector(tuple(max(point[axis] for point in bounds) for axis in range(3)))
    center = (low + high) / 2
    dimensions = high - low
    extent = max(dimensions)
    ground = material("Gallery ground", (0.16, 0.18, 0.2))
    bpy.ops.mesh.primitive_plane_add(
        size=extent * 200, location=(center.x, center.y, low.z - extent * 0.005)
    )
    bpy.context.object.data.materials.append(ground)
    camera_data = bpy.data.cameras.new("Asset review camera")
    camera = bpy.data.objects.new("Asset review camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    # City fronts are runtime -Z = Blender +Y. Legacy figures use the opposite.
    front = 1 if source.parent == STUDY / "exports" else -1
    direction = Vector((1.15, front * 1.65, 1.05)).normalized()
    if source.stem == "building":
        direction = Vector((0.65, 2.4, 0.62)).normalized()
    if source.stem == "train":
        direction = Vector((1.3, -2.4, 1.2)).normalized()
    if source.stem == "station":
        direction = Vector((1.1, 1.8, 0.45)).normalized()
    camera.location = center + direction * extent * 3.2
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    view_rotation = camera.rotation_euler.to_matrix().transposed()
    projected = [view_rotation @ (p - center) for p in bounds]
    camera_data.ortho_scale = (
        max(
            max(p.x for p in projected) - min(p.x for p in projected),
            max(p.y for p in projected) - min(p.y for p in projected),
        )
        * 1.3
    )
    if detail and source.stem == "building":
        target_point = Vector((0, 0, 1.8))
        camera.location = (9, 42, 6.2)
        camera.rotation_euler = (target_point - camera.location).to_track_quat("-Z", "Y").to_euler()
        camera_data.ortho_scale = 32
    if detail and source.stem == "train":
        target_point = Vector((0, -1, 1.6))
        camera.location = (8, -28, 11)
        camera.rotation_euler = (target_point - camera.location).to_track_quat("-Z", "Y").to_euler()
        camera_data.ortho_scale = 12
    camera_data.clip_start = max(0.001, extent / 10000)
    camera_data.clip_end = extent * 1000
    scene = bpy.context.scene
    scene.camera = camera
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 768
    scene.render.resolution_y = 768
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("Review studio")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.24, 0.28, 0.34, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.45
    scene.view_settings.view_transform = "AgX"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = str(target)
    # Area-light power scales with object area so tiny accessories remain visible.
    for name, position, energy, size, color in (
        ("Key", (1, -1, 1.8), 180, 1.4, (1, 0.9, 0.77)),
        ("Fill", (-1.4, 1, 1), 110, 1.3, (0.65, 0.8, 1)),
        ("Rim", (0.8, 1.5, 1.6), 230, 1, (1, 0.94, 0.8)),
    ):
        pos = center + Vector(position) * extent
        light(name, pos, energy * extent**2, size * extent, color)
    bpy.ops.render.render(write_still=True)
    raw = source.read_bytes()
    length = struct.unpack_from("<I", raw, 12)[0]
    gltf = json.loads(raw[20 : 20 + length])
    triangles = sum(
        gltf["accessors"][primitive["indices"]]["count"] // 3
        for mesh in gltf.get("meshes", [])
        for primitive in mesh["primitives"]
        if "indices" in primitive
    )
    return {
        "visualization": kind,
        "meshes": len(gltf.get("meshes", [])),
        "triangles": triangles,
        "materials": len(gltf.get("materials", [])),
        "dimensions_blender_xyz": list(dimensions),
        "bounds_runtime": {
            "min": [low.x, low.z, -high.y],
            "max": [high.x, high.z, -low.y],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=["study", "legacy", "all"], default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
    assets = []
    if args.group in ("study", "all"):
        for asset in json.loads((STUDY / "study.json").read_text(encoding="utf-8"))["assets"]:
            assets.append(("study", asset["id"], STUDY / asset["export"]))
    if args.group in ("legacy", "all"):
        for row in csv.DictReader(
            (STUDY / "asset-inventory.csv").read_text(encoding="utf-8").splitlines()
        ):
            source = ROOT / row["path"]
            assets.append(("legacy", source.stem, source))
    GALLERY.mkdir(exist_ok=True)
    manifest = GALLERY / "coverage.json"
    reports = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    for group, asset_id, source in assets[: args.limit]:
        key = f"{group}/{asset_id}"
        target = GALLERY / group / f"{asset_id}.png"
        target.parent.mkdir(exist_ok=True)
        sha = digest(source)
        if not args.force and target.exists() and reports.get(key, {}).get("source_sha256") == sha:
            continue
        try:
            report = render_asset(source, target)
            reports[key] = {
                "source": source.relative_to(ROOT).as_posix(),
                "source_sha256": sha,
                "render": target.relative_to(STUDY).as_posix(),
                "render_sha256": digest(target),
                "renderer": bpy.app.version_string + " EEVEE",
                "status": "rendered; human visual approval pending",
                **report,
            }
            if group == "study" and asset_id in ("train", "building"):
                detail_path = target.with_name(target.stem + "-detail.png")
                render_asset(source, detail_path, detail=True)
                reports[key]["detail_render"] = detail_path.relative_to(STUDY).as_posix()
                reports[key]["detail_render_sha256"] = digest(detail_path)
        except Exception as error:
            reports[key] = {
                "source": source.relative_to(ROOT).as_posix(),
                "source_sha256": sha,
                "status": "failed",
                "error": str(error),
            }
            print(f"Render failed for {key}: {error}", flush=True)
        manifest.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
        print(f"Gallery recorded {key}", flush=True)
    failures = [key for key, value in reports.items() if value["status"] == "failed"]
    if failures:
        raise SystemExit(f"Render failures: {failures}")


if __name__ == "__main__":
    main()
