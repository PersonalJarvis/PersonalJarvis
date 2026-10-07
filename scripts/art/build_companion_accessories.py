"""Build the companion accessory meshes from the shared accessory catalog.

Input: jarvis/ui/web/frontend/src/components/society/companion/accessories.json
(written by companion_accessories.py) and source/outlines.json. The bodies are
rebuilt with companion_bodies.py, exactly as in companions.glb.
Run inside Blender with --background --factory-startup --python this_file.

Output nodes in accessories.glb:
  acc_<id>               free parts in symbol units around the slot anchor; the
                         runtime places and scales them per shape.
  acc_<id>__<shape>      clothing patches cut from the body surface itself, already
                         in the shape's 1 m unit-master space (same frame as
                         companions.glb).
  acc_<id>__<shape>__fit the free parts of items worn on the body (outfit, neck,
                         face), placed on that shape and bent onto its surface;
                         the runtime uses it instead of placing acc_<id>.
Materials are named acc:<fill>[:glow]; fills body/bodyDark/bodyLight are tinted
from the agent colour at runtime.
"""

import json
import math
import shutil
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from companion_bodies import FACETED, Surface, build_body  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/agent-symbol-companions"
CATALOG = ROOT / "jarvis/ui/web/frontend/src/components/society/companion/accessories.json"
RUNTIME_COPY = ROOT / "jarvis/ui/web/frontend/src/assets/society/companions/accessories.glb"

SLOT_LIFT_M = {"outfit": 0.0, "neck": 0.006, "head": 0.006}
# Items worn on the body surface get per-shape fitted free parts.
FITTED_SLOTS = {"outfit", "neck", "face"}


# --- small geometry helpers ------------------------------------------------------


def catmull_rom(points, closed, steps=5):
    n = len(points)
    if n < 3:
        return list(points)
    out = []
    count = n if closed else n - 1

    def at(i):
        return points[i % n] if closed else points[max(0, min(n - 1, i))]

    for i in range(count):
        p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            out.append(
                tuple(
                    0.5
                    * (
                        2 * b
                        + (-a + c) * t
                        + (2 * a - 5 * b + 4 * c - d) * t2
                        + (-a + 3 * b - 3 * c + d) * t3
                    )
                    for a, b, c, d in zip(p0, p1, p2, p3, strict=True)
                )
            )
    if not closed:
        out.append(tuple(points[-1]))
    return out


def signed_area(poly):
    return (
        sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1], strict=True)) / 2
    )


def svg_to_blender(x, y, z):
    """Symbol units (y down, z toward viewer) to Blender (z up, -y toward viewer)."""
    return Vector((x, -z, -y))


def spin_matrix(part):
    """Rotation about the view axis around the part centre, plus its translation."""
    cx, cy, cz = part["c"]
    rot = Matrix.Rotation(math.radians(part.get("rot", 0)), 4, "Y")
    return Matrix.Translation(svg_to_blender(cx, cy, cz)) @ rot


# --- primitive builders (each returns a fresh bmesh in local space) ----------------


def extrude_outline(outline, y_front, y_back, smooth_sides=True):
    """A flat slab: outline points are (x, z) in Blender space."""
    bm = bmesh.new()
    front = [bm.verts.new((x, y_front, z)) for x, z in outline]
    back = [bm.verts.new((x, y_back, z)) for x, z in outline]
    if signed_area(outline) < 0:
        front.reverse(), back.reverse()
    n = len(front)
    bm.faces.new(front)
    bm.faces.new(list(reversed(back)))
    for i in range(n):
        side = bm.faces.new((front[(i + 1) % n], front[i], back[i], back[(i + 1) % n]))
        side.smooth = smooth_sides
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def build_sphere(part, dome=False):
    bm = bmesh.new()
    size = max(part["r"])
    # Small beads and jewels do not need a smooth silhouette at map distance.
    u = 20 if size >= 3 else 14 if size >= 1.5 else 10
    bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=u // 2, radius=1)
    if dome:
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(
            bm, geom=geom, plane_co=(0, 0, 0), plane_no=(0, 0, -1), clear_outer=True
        )
        bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
    rx, ry, rz = part["r"]
    bmesh.ops.scale(bm, vec=(rx, rz, ry), verts=bm.verts)
    for face in bm.faces:
        face.smooth = not (
            dome and abs(face.normal.z) > 0.99 and face.calc_center_median().z < 1e-4
        )
    bmesh.ops.transform(bm, matrix=spin_matrix(part), verts=bm.verts)
    return bm


def build_box(part):
    w, h, d = part["s"]
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bmesh.ops.scale(bm, vec=(w, d, h), verts=bm.verts)
    bevel = min(part.get("round", 0), w / 2 * 0.98, h / 2 * 0.98, d / 2 * 0.98)
    if bevel > 0.02:
        bmesh.ops.bevel(
            bm,
            geom=bm.edges[:] + bm.verts[:],
            offset=bevel,
            segments=2,
            affect="EDGES",
            profile=0.5,
            clamp_overlap=True,
        )
        for face in bm.faces:
            face.smooth = True
    bmesh.ops.transform(bm, matrix=spin_matrix(part), verts=bm.verts)
    return bm


def build_cyl(part):
    rb, rt = part["r"]
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=24,
        radius1=max(rb, 1e-4),
        radius2=max(rt, 1e-4),
        depth=part["h"],
    )
    bmesh.ops.scale(bm, vec=(1, part.get("dz", 1), 1), verts=bm.verts)
    for face in bm.faces:
        face.smooth = abs(face.normal.z) < 0.98
    bmesh.ops.transform(bm, matrix=spin_matrix(part), verts=bm.verts)
    return bm


def build_torus(part, major_segments=28, minor_segments=8):
    big, small = part["R"], part["r"]
    bm = bmesh.new()
    rings = []
    for i in range(major_segments):
        a = i / major_segments * math.tau
        ring = []
        for j in range(minor_segments):
            b = j / minor_segments * math.tau
            r = big + small * math.cos(b)
            ring.append(bm.verts.new((r * math.cos(a), r * math.sin(a), small * math.sin(b))))
        rings.append(ring)
    for i in range(major_segments):
        for j in range(minor_segments):
            a, b = rings[i], rings[(i + 1) % major_segments]
            face = bm.faces.new(
                (a[j], b[j], b[(j + 1) % minor_segments], a[(j + 1) % minor_segments])
            )
            face.smooth = True
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    tilt = Matrix.Rotation(math.radians(90 - part["tilt"]), 4, "X")
    bmesh.ops.transform(bm, matrix=spin_matrix(part) @ tilt, verts=bm.verts)
    return bm


def build_poly(part):
    pts = (
        catmull_rom(part["pts"], closed=True) if part["smooth"] else [tuple(p) for p in part["pts"]]
    )
    outline = [(x, -y) for x, y in pts]
    z, d = part["z"], part["d"]
    return extrude_outline(outline, -(z + d / 2), -(z - d / 2))


def build_tube(part, sides=8):
    pts = (
        catmull_rom([tuple(p) for p in part["pts"]], closed=False)
        if part["smooth"]
        else part["pts"]
    )
    path = [svg_to_blender(*p) for p in pts]
    path = [p for i, p in enumerate(path) if i == 0 or (p - path[i - 1]).length > 1e-4]
    radius = part["w"] / 2
    bm = bmesh.new()
    if len(path) >= 2:
        tangent = (path[1] - path[0]).normalized()
        helper = Vector((0, 1, 0)) if abs(tangent.y) < 0.9 else Vector((1, 0, 0))
        normal = tangent.cross(helper).normalized()
        rings = []
        for i, point in enumerate(path):
            nxt = path[min(i + 1, len(path) - 1)] - path[max(i - 1, 0)]
            new_tangent = nxt.normalized()
            # Parallel transport keeps the ring from twisting along the curve.
            normal = (normal - new_tangent * normal.dot(new_tangent)).normalized()
            binormal = new_tangent.cross(normal)
            rings.append(
                [
                    bm.verts.new(point + (normal * math.cos(a) + binormal * math.sin(a)) * radius)
                    for a in (k / sides * math.tau for k in range(sides))
                ]
            )
        for a, b in zip(rings, rings[1:], strict=False):
            for k in range(sides):
                face = bm.faces.new((a[k], a[(k + 1) % sides], b[(k + 1) % sides], b[k]))
                face.smooth = True
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for end in (path[0], path[-1]):
        bmesh.ops.create_uvsphere(
            bm, u_segments=8, v_segments=5, radius=radius, matrix=Matrix.Translation(end)
        )
    for face in bm.faces:
        face.smooth = True
    return bm


BUILDERS = {
    "sphere": build_sphere,
    "dome": lambda p: build_sphere(p, dome=True),
    "box": build_box,
    "cyl": build_cyl,
    "torus": build_torus,
    "poly": build_poly,
    "tube": build_tube,
}


# --- assembly ---------------------------------------------------------------------

MATERIALS = {}


def material(fill, glow, metal=False, gloss=False):
    finish = ":metal" if metal else ":gloss" if gloss else ""
    key = f"acc:{fill}{':glow' if glow else ''}{finish}"
    if key in MATERIALS:
        return MATERIALS[key]
    mat = bpy.data.materials.new(key)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    color = (0.6, 0.6, 0.6, 1)
    if fill.startswith("#"):
        srgb = [int(fill[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        color = (*[c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb], 1)
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = 0.55 if fill.startswith("#f") else 0.7
    if metal:
        bsdf.inputs["Metallic"].default_value = 1.0
        bsdf.inputs["Roughness"].default_value = 0.32
    elif gloss:
        bsdf.inputs["Roughness"].default_value = 0.22
    if glow:
        bsdf.inputs["Emission Color"].default_value = color
        bsdf.inputs["Emission Strength"].default_value = 1.4
    mat.diffuse_color = color
    MATERIALS[key] = mat
    return mat


def assemble(name, pieces):
    """Merge (bmesh, material) pieces into one object with one slot per material."""
    mesh = bpy.data.meshes.new(name)
    slots = []
    combined = bmesh.new()
    scratch = bpy.data.meshes.new(f"{name}.scratch")
    for piece, mat in pieces:
        if mat not in slots:
            slots.append(mat)
        index = slots.index(mat)
        for face in piece.faces:
            face.material_index = index
        piece.to_mesh(scratch)
        combined.from_mesh(scratch)
        piece.free()
    bpy.data.meshes.remove(scratch)
    combined.to_mesh(mesh)
    combined.free()
    for mat in slots:
        mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def master_point(meta, sx, sy):
    """Symbol (x, y) on the shape's 1 m master: Blender (x, z)."""
    scale = 1 / (meta["bottom"] - meta["top"])
    return Vector(((sx - 20) * scale, 0, (meta["bottom"] - sy) * scale))


def cut_to_prism(bm, meta, clip):
    """Keep the body inside the convex clip polygon, extended through the depth axis."""
    corners = [master_point(meta, x, y) for x, y in clip]
    centre = sum(corners, Vector()) / len(corners)
    for a, b in zip(corners, corners[1:] + corners[:1], strict=True):
        edge = b - a
        if edge.length < 1e-6:
            continue
        normal = Vector((edge.z, 0, -edge.x)).normalized()
        if normal.dot(centre - a) > 0:
            normal = -normal
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, plane_co=a, plane_no=normal, clear_outer=True)
    bm.normal_update()


def shell(bm, lift, thickness=0.008):
    """Float a surface patch above the body and give it a little thickness."""
    bm.normal_update()
    normals = {vert: vert.normal.copy() for vert in bm.verts}
    for vert, normal in normals.items():
        vert.co += normal * (lift + thickness)
    bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=thickness)
    for face in bm.faces:
        face.smooth = True
    return bm


def rim_piece(part, meta, anchor, body):
    """A hood: the body grown by w symbol units above maxY, open toward the face.

    A vertical cut through the front half leaves a clean opening on every
    body, where a cut by surface normal would fray on an irregular mesh.
    """
    scale = 1 / (meta["bottom"] - meta["top"])
    _, ay, k = anchor
    bm = body.copy()
    bm.normal_update()
    for vert in bm.verts:
        vert.co += vert.normal * part["w"] * scale
    if "maxY" in part:
        limit = master_point(meta, 20, ay + part["maxY"] * k)
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(
            bm, geom=geom, plane_co=limit, plane_no=Vector((0, 0, -1)), clear_outer=True
        )
    front = -min(vert.co.y for vert in bm.verts)
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    bmesh.ops.bisect_plane(
        bm,
        geom=geom,
        plane_co=Vector((0, -0.45 * front, 0)),
        plane_no=Vector((0, -1, 0)),
        clear_outer=True,
    )
    return shell(bm, 0.0, thickness=0.02)


def region_pieces(item, shape, meta, body):
    """Clothing as patches of the actual body surface, layered in authoring order."""
    ax, ay, k = meta["anchors"][item["slot"]]
    lift = SLOT_LIFT_M.get(item["slot"], 0.006)
    pieces = []
    order = 0
    for part in item["parts"]:
        if part.get("only") == "2d":
            continue
        if part["t"] == "rim":
            piece = rim_piece(part, meta, meta["anchors"][item["slot"]], body)
            pieces.append((piece, material(part["fill"], False)))
            continue
        if part["t"] != "region":
            continue
        bm = body.copy()
        cut_to_prism(bm, meta, [(ax + x * k, ay + y * k) for x, y in part["pts"]])
        if part["mode"] == "front":
            facing_away = [f for f in bm.faces if f.normal.y > -0.25]
            bmesh.ops.delete(bm, geom=facing_away, context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
        if not bm.faces:
            bm.free()
            continue
        base = 0.008 if part["mode"] == "wrap" else 0.016
        patch = shell(bm, base + lift + 0.004 * order)
        pieces.append((patch, material(part["fill"], part.get("glow", False))))
        order += 1
    return pieces


def fitted_pieces(item, meta, depths, surface):
    """Free parts placed on one shape and bent onto its surface, in master metres."""
    ax, ay, k = meta["anchors"][item["slot"]]
    scale = 1 / (meta["bottom"] - meta["top"])
    size = k * item.get("scale", 1) * scale
    anchor_depth = depths[item["slot"]]
    origin = master_point(meta, ax, ay) + Vector((0, -anchor_depth, 0))
    place = Matrix.Translation(origin) @ Matrix.Scale(size, 4)
    pieces = []
    for part in item["parts"]:
        if part["t"] in ("region", "rim", "smoke") or part.get("only") == "2d":
            continue
        bm = BUILDERS[part["t"]](part)
        bmesh.ops.transform(bm, matrix=place, verts=bm.verts)
        for vert in bm.verts:
            vert.co.y -= surface.depth_near(vert.co.x, vert.co.z) - anchor_depth
        finish = (part.get("glow", False), part.get("metal", False), part.get("gloss", False))
        pieces.append((bm, material(part["fill"], *finish)))
    return pieces


def main():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    outlines = json.loads((STUDY / "source/outlines.json").read_text(encoding="utf-8"))
    bodies = {shape: build_body(shape, outlines[shape], light=True) for shape in catalog["shapes"]}
    surfaces = {shape: Surface(body) for shape, body in bodies.items()}
    count = 0
    for item in catalog["items"]:
        pieces = [
            (
                BUILDERS[p["t"]](p),
                material(
                    p["fill"], p.get("glow", False), p.get("metal", False), p.get("gloss", False)
                ),
            )
            for p in item["parts"]
            if p["t"] not in ("region", "rim", "smoke") and p.get("only") != "2d"
        ]
        if pieces:
            assemble(f"acc_{item['id']}", pieces)
            count += 1
        for shape, meta in catalog["shapes"].items():
            made = []
            if item["regions"]:
                pieces = region_pieces(item, shape, meta, bodies[shape])
                if pieces:
                    made.append(assemble(f"acc_{item['id']}__{shape}", pieces))
            if item["slot"] in FITTED_SLOTS:
                pieces = fitted_pieces(item, meta, meta["depth"], surfaces[shape])
                if pieces:
                    made.append(assemble(f"acc_{item['id']}__{shape}__fit", pieces))
            for obj in made:
                count += 1
                if shape in FACETED:
                    # Clothing on flat facets stays flat, like the body under it.
                    obj.modifiers.new("Surface normals", "WEIGHTED_NORMAL").keep_sharp = True
    for body in bodies.values():
        body.free()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(
        filepath=str(STUDY / "source/accessories.blend"), check_existing=False
    )
    export = STUDY / "exports/accessories.glb"
    bpy.ops.export_scene.gltf(
        filepath=str(export),
        export_format="GLB",
        export_animations=False,
        export_materials="EXPORT",
        export_apply=True,
        # Flat colours only: no texture coordinates to ship.
        export_texcoords=False,
    )
    shutil.copyfile(export, RUNTIME_COPY)
    print("ACCESSORY_GEOMETRY_EXPORTED", count, export.stat().st_size)


if __name__ == "__main__":
    main()
