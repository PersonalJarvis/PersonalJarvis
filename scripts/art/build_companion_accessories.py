"""Build the companion accessory meshes from the shared accessory catalog.

Input: jarvis/ui/web/frontend/src/components/society/companion/accessories.json
(written by companion_accessories.py) and source/outlines.json.
Run inside Blender with --background --factory-startup --python this_file.

Output nodes in accessories.glb:
  acc_<id>          free parts in symbol units around the slot anchor; the
                    runtime places and scales them per shape.
  acc_<id>__<shape> clothing patches of the body surface, already in the
                    shape's 1 m unit-master space (same frame as companions.glb).
Materials are named acc:<fill>[:glow]; fills body/bodyDark/bodyLight are tinted
from the agent colour at runtime.
"""

import json
import math
import shutil
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/agent-symbol-companions"
CATALOG = ROOT / "jarvis/ui/web/frontend/src/components/society/companion/accessories.json"
RUNTIME_COPY = ROOT / "jarvis/ui/web/frontend/src/assets/society/companions/accessories.glb"

FRONT_M = 0.25
BODY_BEVEL_M = 0.065
SLOT_LIFT_M = {"outfit": 0.0, "neck": 0.006, "head": 0.006}


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


def offset_polygon(poly, distance):
    """Move each vertex along its averaged outward edge normal (positive = grow)."""
    sign = 1 if signed_area(poly) > 0 else -1
    n = len(poly)
    out = []
    for i in range(n):
        prev, cur, nxt = poly[i - 1], poly[i], poly[(i + 1) % n]
        normals = []
        for a, b in ((prev, cur), (cur, nxt)):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy) or 1
            normals.append((dy / length * sign, -dx / length * sign))
        nx, ny = normals[0][0] + normals[1][0], normals[0][1] + normals[1][1]
        length = math.hypot(nx, ny) or 1
        out.append((cur[0] + nx / length * distance, cur[1] + ny / length * distance))
    return out


def clip_polygon(subject, clip):
    """Sutherland-Hodgman: subject may be concave, clip must be convex."""
    orient = 1 if signed_area(clip) > 0 else -1

    def inside(p, a, b):
        return orient * ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) >= 0

    def cross_point(p, q, a, b):
        x1, y1, x2, y2 = p[0], p[1], q[0], q[1]
        x3, y3, x4, y4 = a[0], a[1], b[0], b[1]
        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4) or 1e-9
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    output = list(subject)
    for a, b in zip(clip, clip[1:] + clip[:1], strict=True):
        source, output = output, []
        for i, cur in enumerate(source):
            prev = source[i - 1]
            if inside(cur, a, b):
                if not inside(prev, a, b):
                    output.append(cross_point(prev, cur, a, b))
                output.append(cur)
            elif inside(prev, a, b):
                output.append(cross_point(prev, cur, a, b))
        if not output:
            return []
    return output


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


def rim_piece(part, meta, anchor, outline):
    """A hood: a band around the silhouette, open below maxY (symbol y)."""
    top, bottom = meta["top"], meta["bottom"]
    scale = 1 / (bottom - top)
    _, ay, k = anchor
    limit = ay + part["maxY"] * k if "maxY" in part else math.inf
    outer = offset_polygon(outline, part["w"])
    inner = offset_polygon(outline, -0.3)
    y_front, y_back = -0.12, FRONT_M + 0.05
    bm = bmesh.new()

    def vert(p, y):
        return bm.verts.new(((p[0] - 20) * scale, y, (bottom - p[1]) * scale))

    n = len(outline)
    for i in range(n):
        j = (i + 1) % n
        if max(outer[i][1], outer[j][1], inner[i][1], inner[j][1]) > limit:
            continue
        of, oj, inf, inj = (
            vert(outer[i], y_front),
            vert(outer[j], y_front),
            vert(inner[i], y_front),
            vert(inner[j], y_front),
        )
        ob, obj, inb, injb = (
            vert(outer[i], y_back),
            vert(outer[j], y_back),
            vert(inner[i], y_back),
            vert(inner[j], y_back),
        )
        bm.faces.new((inf, inj, oj, of))
        bm.faces.new((of, oj, obj, ob))
        bm.faces.new((ob, obj, injb, inb))
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for face in bm.faces:
        face.smooth = True
    return bm


def region_pieces(item, shape, meta, outline):
    top, bottom = meta["top"], meta["bottom"]
    scale = 1 / (bottom - top)
    ax, ay, k = meta["anchors"][item["slot"]]
    lift = SLOT_LIFT_M.get(item["slot"], 0.006)
    body_inset = offset_polygon(outline, -(BODY_BEVEL_M + 0.006) / scale)
    pieces = []
    order = 0
    for part in item["parts"]:
        if part.get("only") == "2d":
            continue
        if part["t"] == "rim":
            pieces.append(
                (
                    rim_piece(part, meta, meta["anchors"][item["slot"]], outline),
                    material(part["fill"], False),
                )
            )
            continue
        if part["t"] != "region":
            continue
        clip = [(ax + x * k, ay + y * k) for x, y in part["pts"]]
        if part["mode"] == "wrap":
            area = clip_polygon(outline, clip)
            grow = 0.008 + lift + 0.003 * order
            if len(area) < 3:
                continue
            area = offset_polygon(area, (grow + 0.004) / scale)
            depth = (-(FRONT_M + grow), FRONT_M + grow)
        else:
            area = clip_polygon(body_inset, clip)
            if len(area) < 3:
                continue
            depth = (-(FRONT_M + 0.02 + lift + 0.003 * order), -(FRONT_M - 0.01))
        order += 1
        flat = [((x - 20) * scale, (bottom - y) * scale) for x, y in area]
        bm = extrude_outline(flat, depth[0], depth[1])
        if part["mode"] == "wrap":
            bmesh.ops.bevel(
                bm,
                geom=[e for e in bm.edges if not all(abs(v.co.y) < FRONT_M for v in e.verts)],
                offset=0.045,
                segments=2,
                affect="EDGES",
                profile=0.5,
                clamp_overlap=True,
            )
            for face in bm.faces:
                face.smooth = abs(face.normal.y) < 0.99
        pieces.append((bm, material(part["fill"], part.get("glow", False))))
    return pieces


def main():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    outlines = json.loads((STUDY / "source/outlines.json").read_text(encoding="utf-8"))
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
            if p["t"] not in ("region", "rim") and p.get("only") != "2d"
        ]
        if pieces:
            assemble(f"acc_{item['id']}", pieces)
            count += 1
        if item["regions"]:
            for shape, meta in catalog["shapes"].items():
                # Every second outline sample keeps the silhouette and halves clothing size.
                outline = [tuple(p) for p in outlines[shape][::2]]
                pieces = region_pieces(item, shape, meta, outline)
                if pieces:
                    assemble(f"acc_{item['id']}__{shape}", pieces)
                    count += 1
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
    )
    shutil.copyfile(export, RUNTIME_COPY)
    print("ACCESSORY_GEOMETRY_EXPORTED", count, export.stat().st_size)


if __name__ == "__main__":
    main()
