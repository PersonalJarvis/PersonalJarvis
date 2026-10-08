"""Volumetric agent-symbol bodies, shared by the companion and accessory builders.

Each flat profile silhouette becomes a real solid whose front view still
matches the SVG outline: the circle a sphere, the drop a teardrop of
revolution, the triangle a square pyramid, the hexagon a six-sided crystal,
the squircle a rounded cube, the pill a capsule and the cloud a union of puffs.

Bodies are built in symbol units (X = svg x, Z = -svg y, Y = depth with the
front toward -Y) and then moved into the 1 m unit-master frame used by
companions.glb: x = (svg_x - 20) * scale, z = (bottom - svg_y) * scale.
Run only inside Blender (bmesh/bpy/mathutils).
"""

import math

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

EYE_Y = {"cloud": 23, "triangle": 23, "drop": 25}
EYE_X = (15.4, 24.6)
# Shapes whose flat faces keep flat shading (bevels and curved parts stay smooth).
FACETED = {"triangle", "hexagon"}


def frame(outline):
    """(bottom svg y, metres per symbol unit) of a shape's outline."""
    ys = [y for _, y in outline]
    return max(ys), 1 / (max(ys) - min(ys))


def to_master(outline):
    bottom, scale = frame(outline)
    return Matrix.Scale(scale, 4) @ Matrix.Translation((-20, 0, bottom))


def symbol_to_master(outline, sx, sy, depth=0.0):
    """A symbol point (depth toward the viewer, symbol units) in master metres."""
    bottom, scale = frame(outline)
    return Vector(((sx - 20) * scale, -depth * scale, (bottom - sy) * scale))


# --- solids in symbol units -----------------------------------------------------


def quad_sphere(cuts):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
    for vert in bm.verts:
        vert.co.normalize()
    return bm


def superquadric(center, field, degree, cuts=18):
    """A star-shaped solid: field(direction) is homogeneous of the given degree."""
    bm = quad_sphere(cuts)
    for vert in bm.verts:
        direction = vert.co.copy()
        vert.co = direction * field(direction) ** (-1 / degree) + center
    return bm


def revolve(profile, segments):
    """Spin an (radius, svg y) profile, top to bottom, around the x=20 axis.

    Vertex angles start at +X, so a six-segment spin turns a flat face to
    the front (-Y) and its silhouette spans exactly twice the radius.
    """
    bm = bmesh.new()
    rings = []
    for radius, sy in profile:
        if radius < 1e-6:
            rings.append([bm.verts.new((20, 0, -sy))])
            continue
        rings.append(
            [
                bm.verts.new(
                    (
                        20 + radius * math.cos(i / segments * math.tau),
                        radius * math.sin(i / segments * math.tau),
                        -sy,
                    )
                )
                for i in range(segments)
            ]
        )
    for upper, lower in zip(rings, rings[1:], strict=False):
        for i in range(segments):
            j = (i + 1) % segments
            if len(upper) == 1:
                bm.faces.new((upper[0], lower[j], lower[i]))
            elif len(lower) == 1:
                bm.faces.new((upper[i], upper[j], lower[0]))
            else:
                bm.faces.new((upper[i], upper[j], lower[j], lower[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def soften(bm, offset, segments):
    bmesh.ops.bevel(
        bm,
        geom=bm.edges[:] + bm.verts[:],
        offset=offset,
        segments=segments,
        affect="EDGES",
        profile=0.5,
        clamp_overlap=True,
    )


def drop_profile():
    """The SVG drop: a cubic shoulder from the tip, then a half-ellipse belly."""
    points = []
    p0, p1, p2, p3 = (0, 3), (0, 3), (14, 20), (14, 27)
    for i in range(22):
        t = i / 22
        mt = 1 - t
        points.append(
            tuple(
                mt**3 * a + 3 * mt * mt * t * b + 3 * mt * t * t * c + t**3 * d
                for a, b, c, d in zip(p0, p1, p2, p3, strict=True)
            )
        )
    for i in range(17):
        phi = i / 16 * math.pi / 2
        points.append((14 * math.cos(phi), 27 + 13.5 * math.sin(phi)))
    points[-1] = (0, 40.5)
    return points


def pyramid(segments):
    bm = bmesh.new()
    apex = bm.verts.new((20, 0, -5.5))
    corners = ((-1, -1), (1, -1), (1, 1), (-1, 1))
    base = [bm.verts.new((20 + x * 16, y * 16, -33.5)) for x, y in corners]
    bm.faces.new(base)
    for i in range(4):
        bm.faces.new((base[i], base[(i + 1) % 4], apex))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    soften(bm, 2.6, segments)
    return bm


def crystal(segments):
    bm = revolve([(0, 3.5), (14.5, 11.75), (14.5, 28.25), (0, 36.5)], 6)
    soften(bm, 1.6, segments)
    return bm


def cloud(voxel):
    """Three puffs plus a belly, fused by a voxel remesh and relaxed into soft seams."""
    bm = bmesh.new()
    lobes = [((10.5, 24.55), 7.5), ((19.5, 14.8), 9.78), ((29.5, 22.25), 9.76)]
    pieces = [
        superquadric(Vector((x, 0, -y)), lambda d, r=r: d.length_squared / (r * r), 2, cuts=10)
        for (x, y), r in lobes
    ]
    pieces.append(
        superquadric(
            Vector((20, -0.8, -25.2)),
            lambda d: (d.x / 13) ** 2 + (d.y / 9.6) ** 2 + (d.z / 6.8) ** 2,
            2,
            cuts=10,
        )
    )
    scratch = bpy.data.meshes.new("cloud.scratch")
    for piece in pieces:
        piece.to_mesh(scratch)
        bm.from_mesh(scratch)
        piece.free()
    bm.to_mesh(scratch)
    bm.free()
    obj = bpy.data.objects.new("cloud.scratch", scratch)
    bpy.context.collection.objects.link(obj)
    remesh = obj.modifiers.new("Fuse", "REMESH")
    remesh.mode = "VOXEL"
    remesh.voxel_size = voxel
    smooth = obj.modifiers.new("Soft seams", "SMOOTH")
    smooth.factor = 0.8
    smooth.iterations = 10
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    fused = bmesh.new()
    fused.from_mesh(evaluated.to_mesh())
    evaluated.to_mesh_clear()
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(scratch)
    return fused


def build_body(shape, outline, light=False):
    """The shape's solid as a bmesh in master metres, smooth-flagged faces.

    light=True builds a coarser copy of the same surface for clothing cut from
    it: the patches float a few millimetres above the body, far more than the
    coarser chords sink below it.
    """
    cuts = 9 if light else 18
    if shape == "circle":
        bm = superquadric(Vector((20, 0, -20)), lambda d: d.length_squared / 16.2**2, 2, cuts)
    elif shape == "squircle":
        bm = superquadric(
            Vector((20, 0, -20)),
            lambda d: (abs(d.x) ** 5 + abs(d.y / 0.92) ** 5 + abs(d.z) ** 5) / 16.2**5,
            5,
            cuts,
        )
    elif shape == "pill":
        bm = superquadric(
            Vector((20, 0, -20)),
            lambda d: ((d.y / 11.52) ** 2 + (d.z / 11.52) ** 2) ** 4 + (d.x / 16) ** 8,
            8,
            cuts,
        )
    elif shape == "drop":
        bm = revolve(drop_profile(), 24 if light else 48)
    elif shape == "triangle":
        bm = pyramid(3 if light else 6)
    elif shape == "hexagon":
        bm = crystal(3 if light else 5)
    elif shape == "cloud":
        bm = cloud(1.5 if light else 0.75)
    else:
        raise ValueError(f"unknown companion shape {shape!r}")
    bmesh.ops.transform(bm, matrix=to_master(outline), verts=bm.verts)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for face in bm.faces:
        face.smooth = True
    bm.normal_update()
    return bm


# --- surface queries ------------------------------------------------------------


class Surface:
    """Ray queries against one body in master metres (front = -Y)."""

    def __init__(self, bm):
        self.tree = BVHTree.FromBMesh(bm)

    def hit(self, x, z, front=True):
        origin = Vector((x, -10 if front else 10, z))
        location, normal, _, _ = self.tree.ray_cast(origin, Vector((0, 1 if front else -1, 0)))
        return (location, normal) if location is not None else None

    def depth(self, x, z, front=True):
        """Distance of the front (or back) surface from the centre plane, or None."""
        found = self.hit(x, z, front)
        return None if found is None else -found[0].y

    def depth_near(self, x, z, front=True):
        """Surface depth at x, z; outside the silhouette, the nearest depth inward."""
        for step in range(41):
            value = self.depth(x * (1 - step / 40), z, front)
            if value is not None:
                return value
        return 0.0
