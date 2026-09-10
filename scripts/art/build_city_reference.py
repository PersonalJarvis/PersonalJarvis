"""Author editable future-city reference collections; never replace production assets.

Run with Blender --background --factory-startup --disable-autoexec --python this_file.
The manifest exporter remains the only path from these sources to study GLBs.
"""

import math
import sys
from pathlib import Path

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


collection("Citizen")
box("torso", (0, 0, 1.13), (0.42, 0.24, 0.5), CLOTH, 0.08)
box("head", (0, -0.01, 1.59), (0.23, 0.22, 0.29), SKIN, 0.07)
box("hair", (0, 0.015, 1.73), (0.24, 0.23, 0.09), STEEL, 0.04)
for x, side in ((-0.12, "l"), (0.12, "r")):
    upper = box("leg_" + side, (x, 0, 0.7), (0.16, 0.18, 0.47), CLOTH, 0.05)
    lower = box("shin_" + side, (x, 0, 0.26), (0.14, 0.16, 0.47), CLOTH, 0.04)
    shoe = box("shoe_" + side, (x, -0.04, 0.08), (0.18, 0.3, 0.16), STEEL, 0.03)
    shoulder = bpy.data.objects.new("Arm_" + side.upper(), None)
    shoulder.location = (x * 2.3, 0, 1.32)
    current.objects.link(shoulder)
    upper_arm = box("upper_arm_" + side, (0, 0, -0.15), (0.13, 0.16, 0.3), CLOTH, 0.04)
    upper_arm.parent = shoulder
    elbow = bpy.data.objects.new("Forearm_" + side.upper(), None)
    elbow.location = (0, 0, -0.3)
    elbow.parent = shoulder
    current.objects.link(elbow)
    forearm = box("forearm_" + side, (0, 0, -0.13), (0.115, 0.14, 0.26), CLOTH, 0.035)
    forearm.parent = elbow
    hand = box("hand_" + side, (0, 0, -0.29), (0.1, 0.12, 0.1), SKIN, 0.025)
    hand.parent = elbow
    for frame in range(25):
        phase = (frame / 24 + (0.5 if side == "r" else 0)) % 1
        # The stance foot moves backward at 1.28 m/s relative to the body.
        foot_y = -0.32 + 1.28 * phase if phase < 0.5 else 0.32 - 1.28 * (phase - 0.5)
        lift = 0 if phase < 0.5 else math.sin((phase - 0.5) * math.pi * 2) * 0.16
        hip = Vector((x, 0, 0.92))
        ankle = Vector((x, foot_y, 0.12 + lift))
        axis = ankle - hip
        bend = math.sqrt(max(0, 0.47**2 - (axis.length / 2) ** 2))
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from city_architecture import author_city_architecture  # noqa: E402

author_city_architecture()
bpy.context.scene.unit_settings.system = "METRIC"
bpy.context.scene.unit_settings.scale_length = 1
bpy.context.scene.render.fps = 24
bpy.context.scene.frame_start = 0
bpy.context.scene.frame_end = 24
bpy.context.scene.frame_set(0)
STUDY.joinpath("source").mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(STUDY / "source/city-reference.blend"))
print("Saved editable reference collections; export using the study manifest.")
