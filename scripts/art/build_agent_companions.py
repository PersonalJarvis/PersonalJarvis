"""Author the approved flat bot silhouettes as small bevelled Blender figures.

Input: source/outlines.json sampled from the actual AgentSymbol SVG geometry.
Run inside Blender with --background --factory-startup --python this_file.
The .blend remains editable; GLB uses ordinary meshes and PBR materials only.
"""

import json
from pathlib import Path

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/agent-symbol-companions"


def material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = 0.78
    return mat


def main():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    outlines = json.loads((STUDY / "source/outlines.json").read_text(encoding="utf-8"))
    body_mat = material("Companion.Body", (0.62, 0.40, 0.93))
    eye_mat = material("Companion.Eyes", (0.01, 0.009, 0.015))
    shine_mat = material("Companion.Highlight", (1, 1, 1))
    for name, points in outlines.items():
        parent = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(parent)
        low = min(y for _, y in points)
        high = max(y for _, y in points)
        scale = 1 / (high - low)
        contour = [((x - 20) * scale, (high - y) * scale) for x, y in points]
        count = len(contour)
        vertices = [(x, depth, z) for depth in (-0.25, 0.25) for x, z in contour]
        faces = [tuple(range(count)), tuple(range(count, count * 2))]
        faces += [(i, (i + 1) % count, (i + 1) % count + count, i + count) for i in range(count)]
        mesh = bpy.data.meshes.new(f"{name}.Body")
        mesh.from_pydata(vertices, [], faces)
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(f"{name}.Body", mesh)
        bpy.context.collection.objects.link(obj)
        obj.parent = parent
        obj.data.materials.append(body_mat)
        bevel = obj.modifiers.new("Soft edge", "BEVEL")
        bevel.width = 0.065
        bevel.segments = 4
        obj.modifiers.new("Surface normals", "WEIGHTED_NORMAL")
        eye_y = 23 if name in ("cloud", "triangle") else 25 if name == "drop" else 17.2
        for style in ("Dots", "Lines"):
            for side, x in enumerate((15.4, 24.6)):
                bpy.ops.mesh.primitive_uv_sphere_add(
                    segments=16,
                    ring_count=8,
                    location=((x - 20) * scale, -0.259, (high - eye_y) * scale),
                )
                eye = bpy.context.object
                eye.name = f"{name}.{style}.{side}"
                eye.parent = parent
                eye.scale = (
                    (2.2 if style == "Dots" else 1.3) * scale,
                    0.023,
                    (2.3 if style == "Dots" else 3.2) * scale,
                )
                eye.data.materials.append(eye_mat)
                if style == "Dots":
                    bpy.ops.mesh.primitive_uv_sphere_add(
                        segments=12,
                        ring_count=6,
                        radius=0.65 * scale,
                        location=((x - 20 - 0.6) * scale, -0.283, (high - eye_y + 0.7) * scale),
                    )
                    shine = bpy.context.object
                    shine.name = f"{name}.Highlight.{side}"
                    shine.parent = parent
                    shine.data.materials.append(shine_mat)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(
        filepath=str(STUDY / "source/companions.blend"), check_existing=False
    )
    bpy.ops.export_scene.gltf(
        filepath=str(STUDY / "exports/companions.glb"), export_format="GLB", export_animations=False
    )
    print("COMPANION_GEOMETRY_EXPORTED", (STUDY / "exports/companions.glb").stat().st_size)


if __name__ == "__main__":
    main()
