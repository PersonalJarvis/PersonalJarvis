"""Author editable future-city reference collections; never replace production assets.

Run with Blender --background --factory-startup --disable-autoexec --python this_file.
The manifest exporter remains the only path from these sources to study GLBs.
"""
from pathlib import Path
import math

import bpy
from mathutils import Vector

STUDY = Path(__file__).resolve().parents[2] / "art/studies/city-realism-study"
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)


def material(name, color, roughness=0.6, metal=0, glow=0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metal
    if glow:
        shader.inputs["Emission Color"].default_value = (*color, 1)
        shader.inputs["Emission Strength"].default_value = glow
    return mat


CONCRETE = material("concrete", (0.48, 0.46, 0.42), 0.85)
STEEL = material("graphite_structure", (0.025, 0.035, 0.044), 0.35, 0.7)
METAL = material("brushed_aluminium", (0.4, 0.46, 0.5), 0.38, 0.75)
GLASS = material("blue_glazing", (0.09, 0.19, 0.23), 0.17, 0.35)
LIGHT = material("warm_light", (1, 0.72, 0.38), 0.4, 0, 2)
GREEN = material("foliage", (0.11, 0.2, 0.075), 0.9)
BARK = material("bark", (0.12, 0.07, 0.035), 0.95)
CLOTH = material("technical_uniform", (0.055, 0.19, 0.23), 0.85)
SKIN = material("skin", (0.46, 0.25, 0.15), 0.85)
current = None


def collection(name):
    global current
    current = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(current)


def finish(obj, name, mat):
    obj.name = name
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    current.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def box(name, at, size, mat, bevel=0.08):
    bpy.ops.mesh.primitive_cube_add(size=1, location=at)
    obj = bpy.context.object
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new("edge_detail", "BEVEL")
        mod.width = min(bevel, min(size) / 4)
        mod.segments = 2
        bpy.ops.object.modifier_apply(modifier=mod.name)
        obj.modifiers.new("weighted_normals", "WEIGHTED_NORMAL")
    return finish(obj, name, mat)


def beam(name, a, b, width, mat):
    delta = Vector(b) - Vector(a)
    obj = box(name, (Vector(a) + Vector(b)) / 2, (width, width, delta.length), mat)
    obj.rotation_euler = delta.to_track_quat("Z", "Y").to_euler()
    return obj


collection("Station")
box("platform", (0, -8, -0.3), (38, 10, 0.6), CONCRETE)
box("canopy", (0, -8, 7.6), (44, 15, 0.65), STEEL)
box("roof_cap", (0, -8, 8), (44.5, 15.5, 0.18), METAL)
for x in (-15, 15):
    beam("diagonal_column", (x - 4, -10, 0), (x + 2, -8, 7.3), 0.65, STEEL)
    beam("branch_column", (x - 4, -10, 0), (x - 7, -8, 7.3), 0.45, STEEL)
for y in (-14.8, -1.2):
    box("canopy_light", (0, y, 7.25), (43, 0.1, 0.12), LIGHT)
for x in range(-18, 19, 6):
    box("rear_glass", (x, -12.5, 2), (5.8, 0.12, 4), GLASS)
box("boarding_edge", (0, -3.2, 0.03), (36, 0.25, 0.06), LIGHT)
for x in (-10, 10):
    box("bench", (x, -10, 0.55), (4, 0.7, 0.18), METAL)
    box("bench_back", (x, -10.3, 1), (4, 0.12, 0.8), METAL)

collection("Building")
box("base", (0, 0, 0.3), (40, 36, 0.6), CONCRETE)
box("core", (0, 4, 12), (24, 19, 24), STEEL)
for level in (0, 8, 16):
    box("floor_slab", (0, 0, level + 0.8), (42, 36, 0.6), CONCRETE)
    for x in range(-18, 19, 6):
        box("facade_glass", (x, -15, level + 4.3), (5.8, 0.2, 6.5), GLASS)
        box("mullion", (x - 3, -15.2, level + 4.3), (0.15, 0.4, 7), METAL)
    box("floor_light", (0, -18, level + 0.4), (41, 0.1, 0.12), LIGHT)
for x in (-18, 18):
    beam("facade_brace", (x, -16, 0), (-x, -16, 24), 0.7, STEEL)
box("roof", (0, 0, 25), (48, 42, 1.2), STEEL)
box("roof_edge", (0, -21, 24.8), (47, 0.16, 0.18), LIGHT)
box("entrance_frame", (0, -17, 2.6), (27, 3, 5.2), METAL)
box("entrance_glass", (0, -18.6, 2.4), (25, 0.2, 4.8), GLASS)
for x in (-15, 15):
    box("roof_planter", (x, 0, 26), (5, 25, 0.8), CONCRETE)
    box("roof_planting", (x, 0, 26.6), (4.5, 24.5, 0.5), GREEN)

collection("Train")
# Open doors on the platform side allow visible physical boarding.
box("train_floor", (0, 0, 0.15), (19, 5, 0.3), METAL)
box("train_roof", (0, 0, 3.8), (20, 5.4, 0.3), STEEL)
box("rear_windows", (0, 2.4, 2.1), (18, 0.1, 2.7), GLASS)
for x in (-8, 8):
    box("car_end", (x, 0, 1.1), (2, 4.8, 2), METAL)
    box("cab_window", (x, -2.42, 2.5), (2.8, 0.1, 1.7), GLASS)
    box("front_light", (x, -2.52, 0.7), (2.4, 0.08, 0.15), LIGHT)
box("train_light", (0, -2.7, 3.7), (19, 0.08, 0.1), LIGHT)
for x in (-6, 6):
    box("door_post", (x, -2.4, 2), (0.15, 0.2, 3.5), METAL)

collection("Tree")
beam("trunk", (0, 0, 0), (0.3, 0, 5), 0.28, BARK)
for x, y, z, radius in ((0, 0, 6, 2.5), (-1.2, 0, 5, 1.8), (1.3, 0.4, 5.5, 2), (0, 1, 7, 1.7)):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=radius, location=(x, y, z))
    finish(bpy.context.object, "leaf_cluster", GREEN)

collection("Citizen")
box("torso", (0, 0, 1.13), (0.42, 0.24, 0.5), CLOTH, 0.08)
box("head", (0, -0.01, 1.59), (0.23, 0.22, 0.29), SKIN, 0.07)
box("hair", (0, 0.015, 1.73), (0.24, 0.23, 0.09), STEEL, 0.04)
for x, side in ((-0.12, "l"), (0.12, "r")):
    upper = box("leg_" + side, (x, 0, 0.7), (0.16, 0.18, 0.47), CLOTH, 0.05)
    lower = box("shin_" + side, (x, 0, 0.26), (0.14, 0.16, 0.47), CLOTH, 0.04)
    shoe = box("shoe_" + side, (x, -0.04, 0.08), (0.18, 0.3, 0.16), STEEL, 0.03)
    box("arm_" + side, (x * 2.3, 0, 1.06), (0.13, 0.16, 0.57), CLOTH, 0.04)
    for frame in range(25):
        phase = (frame / 24 + (0.5 if side == "r" else 0)) % 1
        # The stance foot moves backward at 1.28 m/s relative to the body.
        foot_y = -0.32 + 1.28 * phase if phase < 0.5 else 0.32 - 1.28 * (phase - 0.5)
        lift = 0 if phase < 0.5 else math.sin((phase - 0.5) * math.pi * 2) * 0.16
        hip = Vector((x, 0, 0.92))
        ankle = Vector((x, foot_y, 0.12 + lift))
        axis = ankle - hip
        bend = math.sqrt(max(0, 0.47**2 - (axis.length / 2)**2))
        perpendicular = Vector((0, axis.z, -axis.y)).normalized()
        knee = (hip + ankle) / 2 + perpendicular * bend
        for obj, a, b in ((upper, hip, knee), (lower, knee, ankle)):
            obj.location = (a + b) / 2
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = (b - a).to_track_quat("Z", "Y")
            obj.keyframe_insert("location", frame=frame)
            obj.keyframe_insert("rotation_quaternion", frame=frame)
        shoe.location = (x, foot_y - 0.04, 0.08 + lift)
        shoe.keyframe_insert("location", frame=frame)
    for obj in (upper, lower, shoe):
        action = obj.animation_data.action
        track = obj.animation_data.nla_tracks.new()
        track.name = "walk"
        track.strips.new("walk", 0, action)
        obj.animation_data.action = None
box("badge", (0.09, -0.125, 1.26), (0.045, 0.015, 0.07), LIGHT, 0)

bpy.context.scene.unit_settings.system = "METRIC"
bpy.context.scene.unit_settings.scale_length = 1
bpy.context.scene.render.fps = 24
bpy.context.scene.frame_start = 0
bpy.context.scene.frame_end = 24
bpy.context.scene.frame_set(0)
STUDY.joinpath("source").mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(STUDY / "source/city-reference.blend"))
print("Saved editable reference collections; export using the study manifest.")
