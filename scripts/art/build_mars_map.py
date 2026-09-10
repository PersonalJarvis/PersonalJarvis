"""Author an isolated terrain/circulation blockout, not finished Mars assets.

Run Blender with --background --factory-startup --disable-autoexec --python
scripts/art/build_mars_map.py. The study manifest drives the separate GLB export.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import delaunay_2d_cdt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mars_map import height_at, load_layout, validate_layout  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/mars-base-study"
PAD_SEGMENTS = 128


def xyz(point):
    """Convert the study's Three.js Y-up positions to Blender Z-up."""
    return (point[0], -point[2], point[1])


def material(name, color, emission=0):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    bsdf = result.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = 0.94
    bsdf.inputs["Emission Color"].default_value = (*color, 1)
    bsdf.inputs["Emission Strength"].default_value = emission
    return result


def mesh_object(name, vertices, faces, mat, collection):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    mesh.materials.append(mat)
    return obj


def collection(name, parent=None):
    result = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(result)
    return result


def move_object(obj, destination):
    for parent in list(obj.users_collection):
        parent.objects.unlink(obj)
    destination.objects.link(obj)


def route_sample(nodes, link, t, lateral=0):
    a, b = (nodes[link[key]]["position"] for key in ("a", "b"))
    dx, dz = b[0] - a[0], b[2] - a[2]
    length = math.hypot(dx, dz)
    return (
        a[0] + dx * t - dz / length * lateral,
        a[2] + dz * t + dx / length * lateral,
    )


def constrained_terrain(layout):
    """Triangulate roads and pads as part of the heightfield, not overlaid meshes."""
    coords = []
    ids = {}
    edges = set()

    def vertex(x, z):
        key = (round(x, 6), round(z, 6))
        if key not in ids:
            ids[key] = len(coords)
            coords.append(Vector((x, z)))
        return ids[key]

    def edge(a, b):
        if a != b:
            edges.add(tuple(sorted((a, b))))

    # The original nonuniform grid supplies outer geology detail. Constraints
    # replace its arbitrary diagonals wherever circulation needs exact contact.
    xs = list(range(-800, -400, 25)) + list(range(-400, 401, 4)) + list(range(425, 801, 25))
    zs = list(range(-640, -300, 20)) + list(range(-300, 301, 4)) + list(range(320, 641, 20))
    for z in zs:
        for x in xs:
            vertex(x, z)
    corners = [
        vertex(xs[0], zs[0]),
        vertex(xs[-1], zs[0]),
        vertex(xs[-1], zs[-1]),
        vertex(xs[0], zs[-1]),
    ]
    for index, a in enumerate(corners):
        edge(a, corners[(index + 1) % len(corners)])
    nodes = {node["id"]: node for node in layout["nodes"]}
    for node in nodes.values():
        x, _, z = node["position"]
        vertex(x, z)
        radius = node.get("radius", 0)
        if radius <= 0:
            continue
        ring = [
            vertex(
                x + radius * math.cos(i * math.tau / PAD_SEGMENTS),
                z + radius * math.sin(i * math.tau / PAD_SEGMENTS),
            )
            for i in range(PAD_SEGMENTS)
        ]
        for index, a in enumerate(ring):
            edge(a, ring[(index + 1) % len(ring)])

    for link in layout["links"]:
        if link["kind"] != "ground":
            continue
        a, b = (nodes[link[key]] for key in ("a", "b"))
        length = math.dist(a["position"][::2], b["position"][::2])
        count = max(2, math.ceil(length / 2))
        samples = {i / count for i in range(count + 1)}
        # Without these transverse edges, a triangle can straddle the break
        # between a flat pad and its ramp even when its vertices are on the road.
        samples.update(
            (min(1, a.get("radius", 0) / length), max(0, 1 - b.get("radius", 0) / length))
        )
        columns = []
        for lateral in (-link["width"] / 2, 0, link["width"] / 2):
            column = [vertex(*route_sample(nodes, link, t, lateral)) for t in sorted(samples)]
            for first, second in zip(column, column[1:], strict=False):
                edge(first, second)
            columns.append(column)
        for row in zip(*columns, strict=True):
            edge(row[0], row[1])
            edge(row[1], row[2])

    planar, _, triangles, _, _, _ = delaunay_2d_cdt(coords, sorted(edges), [], 0, 1e-5, False)
    vertices = [xyz((p.x, height_at(layout, p.x, p.y), p.y)) for p in planar]
    # CDT is counter-clockwise in X/Z; converting Z to Blender -Y reverses it.
    faces = [tuple(reversed(face)) for face in triangles]
    if any(len(face) != 3 for face in faces):
        raise ValueError("Constrained terrain must contain triangles only")
    return vertices, faces


def terrain_material(layout, nodes, x, z):
    for node in nodes.values():
        px, _, pz = node["position"]
        if math.hypot(x - px, z - pz) <= node.get("radius", 0):
            return 2
    for link in layout["links"]:
        if link["kind"] != "ground":
            continue
        a, b = (nodes[link[key]]["position"] for key in ("a", "b"))
        dx, dz = b[0] - a[0], b[2] - a[2]
        t = max(0, min(1, ((x - a[0]) * dx + (z - a[2]) * dz) / (dx * dx + dz * dz)))
        if math.hypot(x - a[0] - t * dx, z - a[2] - t * dz) <= link["width"] / 2:
            return 1
    return 0


def bridge_abutment(node, forward, lateral, half_width):
    """The exact polygonal pad boundary inside the bridge's width."""
    x, _, z = node["position"]
    radius = node.get("radius", 0)
    if radius <= half_width:
        raise ValueError("Bridge abutment pads must be wider than the deck")
    ring = [
        (
            x + radius * math.cos(i * math.tau / PAD_SEGMENTS),
            z + radius * math.sin(i * math.tau / PAD_SEGMENTS),
        )
        for i in range(PAD_SEGMENTS)
    ]
    points = {}

    def include(px, pz):
        u = (px - x) * forward[0] + (pz - z) * forward[1]
        v = (px - x) * lateral[0] + (pz - z) * lateral[1]
        if u >= 0 and abs(v) <= half_width + 1e-8:
            points[(round(px, 8), round(pz, 8))] = (v, px, pz)

    for index, (ax, az) in enumerate(ring):
        include(ax, az)
        bx, bz = ring[(index + 1) % len(ring)]
        av = (ax - x) * lateral[0] + (az - z) * lateral[1]
        bv = (bx - x) * lateral[0] + (bz - z) * lateral[1]
        if abs(bv - av) < 1e-10:
            continue
        for side in (-half_width, half_width):
            t = (side - av) / (bv - av)
            if 0 <= t <= 1:
                include(ax + (bx - ax) * t, az + (bz - az) * t)
    return [(px, pz) for _, px, pz in sorted(points.values())]


def mesh_contact_report(layout, ground, bridge_contacts):
    """Raycast the actual triangulated Blender terrain, never the ideal helper."""
    mesh = ground.data
    mesh.calc_loop_triangles()
    bvh = BVHTree.FromPolygons(
        [vertex.co.copy() for vertex in mesh.vertices],
        [tuple(triangle.vertices) for triangle in mesh.loop_triangles],
        all_triangles=True,
    )
    nodes = {node["id"]: node for node in layout["nodes"]}
    samples = []
    for link in layout["links"]:
        if link["kind"] != "ground":
            continue
        a, b = (nodes[link[key]]["position"] for key in ("a", "b"))
        count = max(2, math.ceil(math.dist(a[::2], b[::2])))
        for i in range(count + 1):
            for side in (-1, 0, 1):
                x, z = route_sample(nodes, link, i / count, side * link["width"] / 2)
                samples.append(("road_edge" if side else "road_centre", link["id"], x, z))
    for node in nodes.values():
        x, _, z = node["position"]
        samples.append(("pad_centre", node["id"], x, z))
        radius = node.get("radius", 0)
        if radius > 0:
            # Twice the authored boundary resolution also probes chord interiors.
            for index in range(PAD_SEGMENTS * 2):
                angle = index * math.tau / (PAD_SEGMENTS * 2)
                samples.append(
                    (
                        "pad_perimeter",
                        node["id"],
                        x + radius * math.cos(angle),
                        z + radius * math.sin(angle),
                    )
                )
    for index, (x, z) in enumerate(((114.85542, -130.90194), (-149.67218, -54.37015))):
        samples.append(("previous_contact_defect", str(index + 1), x, z))
    samples.extend(("bridge_abutment", identifier, x, z) for identifier, x, z in bridge_contacts)
    maximum = 0.0
    worst = None
    violations = []
    misses = 0
    category_maxima = {}
    previous_defects = []
    for kind, identifier, x, z in samples:
        expected = height_at(layout, x, z)
        location, _, _, _ = bvh.ray_cast(Vector((x, -z, 2000)), Vector((0, 0, -1)))
        measured = float(location.z) if location is not None else None
        deviation = abs(measured - expected) if measured is not None else None
        record = {
            "kind": kind,
            "id": identifier,
            "x": x,
            "z": z,
            "expected_y": expected,
            "mesh_y": measured,
            "deviation_m": deviation,
        }
        if kind == "previous_contact_defect":
            previous_defects.append(record)
        if deviation is None:
            misses += 1
            violations.append(record)
        else:
            category_maxima[kind] = max(category_maxima.get(kind, 0.0), deviation)
            if deviation > maximum:
                maximum, worst = deviation, record
            if deviation > 0.05:
                violations.append(record)
    return {
        "method": "BVH vertical rays against actual triangulated MarsBasin mesh",
        "samples": len(samples),
        "tolerance_m": 0.05,
        "max_deviation_m": maximum,
        "worst_sample": worst,
        "category_max_deviation_m": category_maxima,
        "previous_defect_samples": previous_defects,
        "misses": misses,
        "violation_count": len(violations),
        "violations": violations[:50],
        "passed": not violations,
    }


def main():
    layout = load_layout(STUDY / "layout.json")
    report = validate_layout(layout)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    map_collection = collection("MarsMap")
    terrain_collection = collection("Terrain", map_collection)
    routes_collection = collection("Circulation", map_collection)
    markers_collection = collection("BlockoutMarkers", map_collection)
    presentation = collection("ReviewOnly")
    soil = material("mars_terrain_vertex_color", (0.25, 0.09, 0.045))
    vertex_color = soil.node_tree.nodes.new("ShaderNodeVertexColor")
    vertex_color.layer_name = "TerrainColor"
    soil.node_tree.links.new(
        vertex_color.outputs["Color"],
        soil.node_tree.nodes.get("Principled BSDF").inputs["Base Color"],
    )
    road = material("route_blockout", (0.11, 0.17, 0.18))
    pad = material("reserved_site_blockout", (0.32, 0.38, 0.37))
    ink = material("review_annotation", (0.75, 0.9, 0.86), 0.35)

    vertices, faces = constrained_terrain(layout)
    ground = mesh_object("MarsBasin", vertices, faces, soil, terrain_collection)
    ground.data.materials.append(road)
    ground.data.materials.append(pad)
    colors = ground.data.color_attributes.new(
        name="TerrainColor", type="FLOAT_COLOR", domain="POINT"
    )
    for index, (x, y, h) in enumerate(vertices):
        grain = 0.5 + 0.5 * math.sin(x * 0.31 + y * 0.47) * math.sin(y * 0.21 - x * 0.17)
        strata = 0.5 + 0.5 * math.sin(h * 0.7 + x * 0.025)
        shade = 0.66 + grain * 0.16 + strata * 0.18
        colors.data[index].color = (0.32 * shade, 0.115 * shade, 0.052 * shade, 1)
    nodes = {node["id"]: node for node in layout["nodes"]}
    for polygon in ground.data.polygons:
        polygon.use_smooth = True
        center = polygon.center
        polygon.material_index = terrain_material(layout, nodes, center.x, -center.y)

    bridge_contacts = []
    for link in layout["links"]:
        if link["kind"] != "bridge":
            continue
        start, end = (nodes[link[key]] for key in ("a", "b"))
        a, b = start["position"], end["position"]
        dx, dz = b[0] - a[0], b[2] - a[2]
        length = math.hypot(dx, dz)
        forward = (dx / length, dz / length)
        lateral = (-dz / length, dx / length)
        start_arc = bridge_abutment(start, forward, lateral, link["width"] / 2)
        end_arc = bridge_abutment(end, (-forward[0], -forward[1]), lateral, link["width"] / 2)
        outline = [(x, a[1], z) for x, z in start_arc] + [
            (x, b[1], z) for x, z in reversed(end_arc)
        ]
        route_vertices = [xyz(point) for point in outline]
        route_faces = [tuple(range(len(route_vertices)))]
        bridge_contacts.extend((link["id"], x, z) for x, _, z in outline)
        obj = mesh_object(link["id"], route_vertices, route_faces, road, routes_collection)
        obj["route_id"] = link["id"]
        obj["layer"] = link["kind"]
        solid = obj.modifiers.new("Deck thickness", "SOLIDIFY")
        solid.thickness = 0.5
        solid.offset = -1
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=solid.name)

    for node in layout["nodes"]:
        marker = bpy.data.objects.new("Site_" + node["id"], None)
        marker.location = xyz(node["position"])
        marker.empty_display_type = "CIRCLE"
        marker.empty_display_size = node.get("radius", 1)
        marker["site_id"] = node["id"]
        marker["status"] = "placeholder_not_architecture"
        markers_collection.objects.link(marker)
        if "label" not in node:
            continue
        x, y, z = node["position"]
        bpy.ops.object.text_add(location=xyz((x - node["radius"] * 0.75, y + 0.4, z - 3)))
        label = bpy.context.object
        label.name = "Label_" + node["id"]
        label.data.body = node["label"] + "\n" + str(y) + " m"
        label.data.size = 4.2
        label.data.extrude = 0
        label.data.materials.append(ink)
        move_object(label, presentation)

    report["mesh_contact"] = mesh_contact_report(layout, ground, bridge_contacts)
    report["terrain_triangles"] = len(faces)
    report["terrain_vertices"] = len(vertices)
    bridge_triangles = 0
    for obj in routes_collection.objects:
        obj.data.calc_loop_triangles()
        bridge_triangles += len(obj.data.loop_triangles)
    report["bridge_triangles"] = bridge_triangles
    report["export_triangles"] = len(faces) + bridge_triangles
    report["ground_route_objects"] = 0
    report["reserved_pad_objects"] = 0
    report["export_collection"] = "MarsMap"
    report["runtime_verified"] = False
    report["stage"] = "Blender terrain blockout; no final architecture or agent runtime"
    (STUDY / "evidence/layout-check.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    if not report["mesh_contact"]["passed"]:
        raise ValueError(f"Actual terrain contact check failed: {report['mesh_contact']}")

    bpy.ops.object.light_add(type="SUN", location=(150, -250, 400))
    sun = bpy.context.object
    sun.rotation_euler = (math.radians(30), math.radians(-25), math.radians(-32))
    sun.data.energy = 3
    sun.data.angle = math.radians(7)
    move_object(sun, presentation)
    scene = bpy.context.scene
    scene.world.color = (0.22, 0.15, 0.12)
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 1100
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX"
    bpy.ops.object.camera_add(location=(460, -620, 630))
    camera = bpy.context.object
    camera.name = "ReviewCamera"
    camera.data.clip_end = 5000
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 720
    camera.rotation_euler = (
        (Vector((25, 0, 5)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    scene.camera = camera
    move_object(camera, presentation)
    scene["review_stage"] = "terrain-and-circulation-blockout"
    scene["units"] = "1 Blender unit = 1 metre"
    scene.unit_settings.system = "METRIC"
    scene.render.filepath = "//../evidence/terrain-overview.png"
    bpy.ops.wm.save_as_mainfile(filepath=str(STUDY / "source/mars-map.blend"))
    bpy.ops.render.render(write_still=True)

    camera.location = (25, 0, 1100)
    camera.rotation_euler = (0, 0, 0)
    camera.data.ortho_scale = 760
    scene.render.filepath = "//../evidence/terrain-plan.png"
    bpy.ops.render.render(write_still=True)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
