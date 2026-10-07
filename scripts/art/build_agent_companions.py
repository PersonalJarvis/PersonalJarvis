"""Author the flat bot silhouettes as small volumetric Blender figures.

Input: source/outlines.json sampled from the actual AgentSymbol SVG geometry,
and the slot anchors of the accessory catalog. Run inside Blender with
--background --factory-startup --python this_file.

Every body is a real solid (see companion_bodies.py), not an extruded outline.
The eyes sit on the curved front surface, turned along its normal. The script
also writes source/body_depths.json: the surface depth under each accessory
slot anchor, which companion_accessories.py copies into the catalog so worn
items rest on the body instead of a fixed front plane.
The .blend remains editable; GLB uses ordinary meshes and PBR materials only.
"""

import json
import shutil
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from companion_bodies import EYE_X, EYE_Y, FACETED, Surface, build_body, frame  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/agent-symbol-companions"
CATALOG = ROOT / "jarvis/ui/web/frontend/src/components/society/companion/accessories.json"
RUNTIME_COPY = ROOT / "jarvis/ui/web/frontend/src/assets/society/companions/companions.glb"
# Eye radii in symbol units (x, y) and their thickness in metres.
EYE_SIZE = {"Dots": (2.2, 2.3), "Lines": (1.3, 3.2)}
EYE_THICKNESS_M = 0.024


def material(name, color, roughness):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = roughness
    return mat


def mesh_object(name, bm, parent, mat):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    obj.data.materials.append(mat)
    return obj


def surface_ellipsoid(location, normal, radii, segments=16):
    """A flat ellipsoid lying on the surface: local Y follows the outward normal."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=segments // 2, radius=1)
    bmesh.ops.scale(bm, vec=radii, verts=bm.verts)
    turn = Vector((0, -1, 0)).rotation_difference(normal).to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=Matrix.Translation(location) @ turn, verts=bm.verts)
    for face in bm.faces:
        face.smooth = True
    return bm


def slot_depths(surface, outline, anchors):
    """Metres from the centre plane toward the viewer for each slot anchor."""
    bottom, scale = frame(outline)

    def at(slot, front=True, centre=False):
        x, y, _ = anchors[slot]
        return surface.depth_near(0 if centre else (x - 20) * scale, (bottom - y) * scale, front)

    depths = {slot: round(at(slot), 4) for slot in ("face", "mouth", "neck", "outfit")}
    depths["back"] = round(at("back", front=False), 4)
    depths["head"] = 0.0
    depths["held"] = round(0.4 * at("held", centre=True), 4)
    return depths


def main():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    outlines = json.loads((STUDY / "source/outlines.json").read_text(encoding="utf-8"))
    shapes = json.loads(CATALOG.read_text(encoding="utf-8"))["shapes"]
    body_mat = material("Companion.Body", (0.62, 0.40, 0.93), 0.6)
    eye_mat = material("Companion.Eyes", (0.01, 0.009, 0.015), 0.35)
    shine_mat = material("Companion.Highlight", (1, 1, 1), 0.2)
    depths = {}
    for name, points in outlines.items():
        parent = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(parent)
        bottom, scale = frame(points)
        body = build_body(name, points)
        surface = Surface(body)
        depths[name] = slot_depths(surface, points, shapes[name]["anchors"])
        obj = mesh_object(f"{name}.Body", body, parent, body_mat)
        if name in FACETED:
            # Flat facets stay flat; the rounded bevels between them stay soft.
            weighted = obj.modifiers.new("Surface normals", "WEIGHTED_NORMAL")
            weighted.keep_sharp = True
        eye_y = EYE_Y.get(name, 17.2)
        for style, (rx, rz) in EYE_SIZE.items():
            for side, x in enumerate(EYE_X):
                found = surface.hit((x - 20) * scale, (bottom - eye_y) * scale)
                if found is None:
                    raise RuntimeError(f"{name}: eye {side} misses the body")
                location, normal = found
                # Half sunk into the body, so the eye reads as painted on, not stuck on.
                centre = location - normal * EYE_THICKNESS_M * 0.35
                radii = (rx * scale, EYE_THICKNESS_M, rz * scale)
                eye = surface_ellipsoid(centre, normal, radii)
                mesh_object(f"{name}.{style}.{side}", eye, parent, eye_mat)
                if style == "Dots":
                    glint = surface.hit((x - 20.6) * scale, (bottom - eye_y + 0.7) * scale)
                    spot, spot_normal = glint or found
                    mesh_object(
                        f"{name}.Highlight.{side}",
                        surface_ellipsoid(
                            spot + spot_normal * EYE_THICKNESS_M * 0.75,
                            spot_normal,
                            (0.65 * scale, 0.3 * EYE_THICKNESS_M, 0.65 * scale),
                            segments=12,
                        ),
                        parent,
                        shine_mat,
                    )
    (STUDY / "source/body_depths.json").write_text(
        json.dumps(depths, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(
        filepath=str(STUDY / "source/companions.blend"), check_existing=False
    )
    export = STUDY / "exports/companions.glb"
    bpy.ops.export_scene.gltf(
        filepath=str(export),
        export_format="GLB",
        export_animations=False,
        export_apply=True,
        # Flat colours only: no texture coordinates to ship.
        export_texcoords=False,
    )
    shutil.copyfile(export, RUNTIME_COPY)
    print("COMPANION_GEOMETRY_EXPORTED", export.stat().st_size)


if __name__ == "__main__":
    main()
