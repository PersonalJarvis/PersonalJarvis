"""Author the editable Gigi reference; exports remain inside the isolated study.

Run in Blender with --background --factory-startup --disable-autoexec --python.
The companion uses metres, Blender Z-up and -Y forward (glTF +Z forward).
No body emblem is authored, including the inconsistent emblems in the concept.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/gigi-hover-companion"
TAU = 2 * math.pi


def material(name, color, metallic=0.0, roughness=0.4, emission=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Emission Color"].default_value = (*color, 1)
    shader.inputs["Emission Strength"].default_value = emission
    return mat


def attach(obj, name, mat, parent):
    obj.name = name
    if mat:
        obj.data.materials.append(mat)
    obj.parent = parent
    for face in getattr(obj.data, "polygons", []):
        face.use_smooth = True
    return obj


def ellipsoid(name, position, size, mat, parent, segments=32):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=16, location=position)
    obj = bpy.context.object
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return attach(obj, name, mat, parent)


def tube(name, points, radius, mat, parent, closed=False):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 3
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, position in zip(spline.points, points, strict=True):
        point.co = (*position, 1)
    spline.use_cyclic_u = closed
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    attach(obj, name, mat, parent)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.convert(target="MESH")
    obj.select_set(False)
    return obj


def rounded_box(name, position, size, radius, mat, parent):
    bpy.ops.mesh.primitive_cube_add(size=1, location=position)
    obj = bpy.context.object
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = obj.modifiers.new("Machined edge radius", "BEVEL")
    bevel.width = radius
    bevel.segments = 3
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    return attach(obj, name, mat, parent)


def hem_height(angle):
    # Three teeth across both face and rear, joined around the side surfaces.
    across = (math.cos(angle) + 1) * 1.5
    triangle = abs(2 * (across % 1) - 1)
    return 0.014 + 0.034 * triangle


def make_shell(parent, mat):
    count = 96
    rings = []
    for height in (None, 0.065, 0.12, 0.20, 0.275):
        rings.append(
            [
                (
                    0.143 * math.cos(i * TAU / count),
                    0.095 * math.sin(i * TAU / count),
                    hem_height(i * TAU / count) if height is None else height,
                )
                for i in range(count)
            ]
        )
    for step in range(1, 13):
        angle = step * math.pi / 26
        rings.append(
            [
                (
                    0.143 * math.cos(angle) * math.cos(i * TAU / count),
                    0.095 * math.cos(angle) * math.sin(i * TAU / count),
                    0.275 + 0.125 * math.sin(angle),
                )
                for i in range(count)
            ]
        )
    vertices = [point for ring in rings for point in ring]
    faces = []
    for ring in range(len(rings) - 1):
        for index in range(count):
            a = ring * count + index
            b = ring * count + (index + 1) % count
            faces.append((a, b, b + count, a + count))
    vertices.extend([(0, 0, 0.02), (0, 0, 0.4)])
    for index in range(count):
        nxt = (index + 1) % count
        faces.append((len(vertices) - 2, nxt, index))
        offset = (len(rings) - 1) * count
        faces.append((len(vertices) - 1, offset + index, offset + nxt))
    mesh = bpy.data.meshes.new("Gigi continuous closed shell")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("Gigi.Housing", mesh)
    bpy.context.collection.objects.link(obj)
    return attach(obj, obj.name, mat, parent)


def empty(name, parent=None):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    return obj


def main():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    collection = bpy.data.collections.new("GigiExport")
    scene.collection.children.link(collection)
    layer = bpy.context.view_layer.layer_collection.children[collection.name]
    bpy.context.view_layer.active_layer_collection = layer
    root = empty("Gigi.Root")
    root["body_height_m"] = 0.4
    root["role"] = "optional assistant companion, never worker identity"
    root["forward"] = "Blender -Y; glTF +Z"
    root["body_emblem"] = "none"
    shell = material("Gigi.Graphite", (0.045, 0.050, 0.057), 0.3, 0.46)
    face = material("Gigi.FaceGlass", (0.004, 0.006, 0.009), 0.15, 0.23)
    metal = material("Gigi.AnodizedRim", (0.11, 0.10, 0.075), 0.85, 0.29)
    yellow = material("Gigi.SignalYellow", (1.0, 0.59, 0.025), 0.25, 0.26, 1.2)
    pupil = material("Gigi.Pupil", (0.001, 0.002, 0.003), 0.0, 0.23)
    highlight = material("Gigi.EyeHighlight", (0.95, 0.95, 0.88), 0.0, 0.2, 0.35)
    make_shell(root, shell)
    # The face is a fitted curved lens rather than a flat billboard.
    ellipsoid("Gigi.Face", (0, -0.037, 0.275), (0.137, 0.064, 0.116), face, root)
    ring = [
        (0.144 * math.cos(i * TAU / 144), 0.096 * math.sin(i * TAU / 144), 0.207)
        for i in range(144)
    ]
    tube("Gigi.EquatorSeam", ring, 0.0015, metal, root, True)
    tube(
        "Gigi.EquatorLight",
        [(x, y - 0.0008, z + 0.002) for x, y, z in ring[72:]],
        0.0008,
        yellow,
        root,
    )
    hem = [
        (
            0.144 * math.cos(i * TAU / 192),
            0.096 * math.sin(i * TAU / 192),
            hem_height(i * TAU / 192) + 0.004,
        )
        for i in range(192)
    ]
    tube("Gigi.HemLight", hem, 0.0025, yellow, root, True)
    arch = [
        (0.137 * math.cos(i * math.pi / 64), -0.035, 0.275 + 0.121 * math.sin(i * math.pi / 64))
        for i in range(65)
    ]
    tube("Gigi.CrownLight", arch, 0.0022, yellow, root)
    for side, x in (("L", -0.055), ("R", 0.055)):
        eye = empty(f"Gigi.Eye.{side}", root)
        eye.location = (x, -0.100, 0.268)
        ellipsoid(f"Gigi.Iris.{side}", (0, 0, 0), (0.027, 0.007, 0.037), yellow, eye)
        ellipsoid(f"Gigi.Pupil.{side}", (0.002, -0.006, -0.004), (0.0115, 0.003, 0.023), pupil, eye)
        ellipsoid(
            f"Gigi.Glint.{side}", (0.008, -0.009, 0.016), (0.004, 0.002, 0.005), highlight, eye, 16
        )
        eye.scale = (1, 1, 1)
        eye.keyframe_insert(data_path="scale", frame=1)
        eye.keyframe_insert(data_path="scale", frame=90)
        eye.scale.z = 0.12
        eye.keyframe_insert(data_path="scale", frame=94)
        eye.scale.z = 1
        eye.keyframe_insert(data_path="scale", frame=98)
        eye.keyframe_insert(data_path="scale", frame=180)
        eye.animation_data.action.name = f"Gigi.Blink.{side}"
    mouth = [
        (0.012 * math.cos(i * TAU / 48), -0.0975, 0.178 + 0.019 * math.sin(i * TAU / 48))
        for i in range(48)
    ]
    tube("Gigi.Mouth", mouth, 0.0025, yellow, root, True)
    for sign, side in ((-1, "L"), (1, "R")):
        ellipsoid(
            f"Gigi.SensorRim.{side}", (sign * 0.143, 0, 0.254), (0.023, 0.049, 0.050), metal, root
        )
        ellipsoid(
            f"Gigi.Sensor.{side}", (sign * 0.161, 0, 0.254), (0.01, 0.039, 0.040), shell, root
        )
        rounded_box(
            f"Gigi.SensorLight.{side}",
            (sign * 0.170, -0.005, 0.254),
            (0.003, 0.007, 0.045),
            0.002,
            yellow,
            root,
        )
        arm = empty(f"Gigi.Arm.{side}", root)
        arm.location = (sign * 0.136, 0, 0.170)
        ellipsoid(
            f"Gigi.ArmShell.{side}",
            (sign * 0.020, -0.002, -0.034),
            (0.021, 0.028, 0.056),
            shell,
            arm,
        )
        ellipsoid(
            f"Gigi.ArmLight.{side}",
            (sign * 0.028, -0.026, -0.040),
            (0.014, 0.004, 0.036),
            yellow,
            arm,
        )
        arm.rotation_euler.y = sign * -0.15
    rounded_box("Gigi.RearPanel", (0, 0.094, 0.142), (0.18, 0.014, 0.14), 0.011, shell, root)
    for z in (0.165, 0.176):
        rounded_box(
            f"Gigi.RearStatus.{z}", (0, 0.104, z), (0.027, 0.003, 0.002), 0.001, yellow, root
        )
    for x in (-0.072, 0.072):
        for z in (0.092, 0.196):
            ellipsoid(
                f"Gigi.RearFastener.{x}.{z}", (x, 0.103, z), (0.002, 0.001, 0.002), metal, root, 12
            )
    ellipsoid("Gigi.HoverHousing", (0, 0, 0.026), (0.062, 0.057, 0.012), metal, root)
    tube(
        "Gigi.HoverEmitter",
        [
            (0.045 * math.cos(i * TAU / 64), 0.043 * math.sin(i * TAU / 64), 0.014)
            for i in range(64)
        ],
        0.003,
        yellow,
        root,
        True,
    )
    scene.frame_start, scene.frame_end, scene.render.fps = 1, 180, 30
    scene.frame_set(1)
    scene["production_recipe"] = "scripts/art/build_gigi_companion.py"
    scene["reference_status"] = "pending actual-runtime visual review"
    # Review lighting/camera is outside the export collection.
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection
    world = bpy.data.worlds.new("Gigi neutral studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.18, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.5
    scene.world = world
    for name, location, energy, size in (
        ("Key", (-0.6, -0.6, 0.9), 45, 0.7),
        ("Fill", (0.7, -0.3, 0.5), 25, 0.6),
        ("Rim", (0, 0.6, 0.7), 40, 0.5),
    ):
        bpy.ops.object.light_add(type="AREA", location=location)
        light = bpy.context.object
        light.name, light.data.energy, light.data.shape, light.data.size = (
            name,
            energy,
            "DISK",
            size,
        )
        light.rotation_euler = (
            (Vector((0, 0, 0.2)) - light.location).to_track_quat("-Z", "Y").to_euler()
        )
    bpy.ops.object.camera_add(location=(0.65, -1.1, 0.58))
    camera = bpy.context.object
    camera.rotation_euler = (
        (Vector((0, 0, 0.20)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.type, camera.data.ortho_scale = "ORTHO", 0.58
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 24
    scene.render.resolution_x = scene.render.resolution_y = 768
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "AgX"
    source = STUDY / "source/gigi.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    report = {
        "blender": bpy.app.version_string,
        "body_height_m": 0.4,
        "forward": "glTF +Z",
        "pivot": "body bottom center",
        "objects": len(collection.all_objects),
        "textures": "none; glTF PBR constants",
        "approval": "pending",
        "body_emblem": "none",
    }
    (STUDY / "source/build-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
