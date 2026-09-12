"""Author the isolated Communications Outpost; never approve or publish it.

Run with Blender --background --factory-startup --disable-autoexec --python
scripts/art/build_outpost_reference.py. Geometry uses canonical runtime metres;
conversion to Blender Z-up occurs only at the authoring boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import struct
import sys
from array import array
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/mars-outpost-reference"
ASSET_ID = "communications-outpost-reference"
TRIANGLE_TARGET = 300_000


def blender_point(point):
    """Convert runtime X-east/Y-up/Z-south coordinates into Blender axes."""
    return (point[0], -point[2], point[1])


def add(a, b):
    return tuple(a[i] + b[i] for i in range(3))


def scale(a, factor):
    return tuple(value * factor for value in a)


def lerp(a, b, fraction):
    return add(scale(a, 1 - fraction), scale(b, fraction))


def length(a):
    return math.sqrt(sum(value * value for value in a))


def normalized(a):
    magnitude = length(a)
    if magnitude < 1e-9:
        raise ValueError("a direction must have nonzero length")
    return scale(a, 1 / magnitude)


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def canonical_layout(path: Path):
    """Read shared placements; fail rather than exporting a competing layout."""
    definition = json.loads(path.read_text(encoding="utf-8"))
    if definition["units"] != "metres" or definition["axes"] != {
        "up": "+Y",
        "east": "+X",
        "north": "-Z",
    }:
        raise ValueError("the Outpost recipe requires the declared metre/Y-up axes")
    district = next(
        item for item in definition["districts"] if item["id"] == "communications-outpost"
    )
    origin = district["center"]
    buildings = {}
    for building in definition["buildings"]:
        if building["district_id"] == district["id"]:
            buildings[building["id"]] = {
                **building,
                "local": tuple(building["position"][i] - origin[i] for i in range(3)),
            }
    nodes = {
        node["id"]: tuple(node["position"][i] - origin[i] for i in range(3))
        for node in definition["navigation"]["nodes"]
    }
    return {
        "origin": origin,
        "size": district["size"],
        "seed": definition["seed"],
        "layout_version": definition["layout_version"],
        "definition_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "buildings": buildings,
        "nodes": nodes,
    }


def plateau_mesh(seed=6901, segments=128):
    """A closed, layered rock volume; no open rear, bottom or edge seams."""
    rng = random.Random(seed)  # noqa: S311 - reproducible terrain, not security.
    heights = [-0.12, -1.7, -5.5, -11, -18, -27, -37, -46, -55, -62]
    factors = [1.0, 1.015, 0.97, 1.02, 0.96, 1.015, 0.95, 0.90, 0.79, 0.63]
    wobble = [rng.uniform(-0.024, 0.024) for _ in range(segments)]
    vertices = []
    for level, (height, factor) in enumerate(zip(heights, factors, strict=True)):
        for index in range(segments):
            angle = math.tau * index / segments
            cosine, sine = math.cos(angle), math.sin(angle)
            x = math.copysign(abs(cosine) ** 0.58, cosine) * 60
            z = math.copysign(abs(sine) ** 0.58, sine) * 47.5
            fold = 1 + wobble[index] + 0.02 * math.sin(angle * 11 + level * 0.55)
            y = height if level == 0 else height + rng.uniform(-1.2, 1.2)
            vertices.append((x * factor * fold, y, z * factor * fold))
    faces = []
    for level in range(len(heights) - 1):
        for index in range(segments):
            following = (index + 1) % segments
            a, b = level * segments + index, level * segments + following
            faces.append((a, b, b + segments, a + segments))
    # Runtime coordinates form a right-handed basis; reverse top ring for +Y.
    faces.append(tuple(reversed(range(segments))))
    start = (len(heights) - 1) * segments
    faces.append(tuple(start + index for index in range(segments)))
    return vertices, faces


def deck_mesh(start, end, width=10, thickness=0.65):
    """Deck top endpoints match the navigation graph, including bridge grade."""
    tangent = normalized((end[0] - start[0], 0, end[2] - start[2]))
    side = (-tangent[2] * width / 2, 0, tangent[0] * width / 2)
    top = [add(start, side), add(end, side), add(end, scale(side, -1)), add(start, scale(side, -1))]
    vertices = top + [add(point, (0, -thickness, 0)) for point in top]
    faces = [(0, 1, 2, 3), (7, 6, 5, 4)]
    faces.extend((i, i + 4, (i + 1) % 4 + 4, (i + 1) % 4) for i in range(4))
    return vertices, faces


def dish_mesh(radius, depth, rings=20, segments=72, thickness=0.10):
    """Closed parabolic reflector shell, with a real back and rim."""
    vertices = [(0, 0, 0), (0, -thickness, 0)]
    for surface in (0, 1):
        for ring in range(1, rings + 1):
            r = radius * ring / rings
            for index in range(segments):
                angle = math.tau * index / segments
                vertices.append(
                    (
                        r * math.cos(angle),
                        depth * (r / radius) ** 2 - surface * thickness,
                        r * math.sin(angle),
                    )
                )
    front, back = 2, 2 + rings * segments
    faces = []
    for index in range(segments):
        following = (index + 1) % segments
        faces.extend(((0, front + following, front + index), (1, back + index, back + following)))
    for ring in range(rings - 1):
        for index in range(segments):
            following = (index + 1) % segments
            a, b = front + ring * segments + index, front + ring * segments + following
            c, d = back + ring * segments + index, back + ring * segments + following
            faces.extend(((a, b, b + segments, a + segments), (c + segments, d + segments, d, c)))
    for index in range(segments):
        following = (index + 1) % segments
        a = front + (rings - 1) * segments
        b = back + (rings - 1) * segments
        faces.append((a + index, a + following, b + following, b + index))
    return vertices, faces


def operations_walls(center=(-26, 0, 14), size=(20, 8, 14)):
    """Collision solids leave a genuine south door and open station approach."""
    x, y, z = center
    width, height, depth = size
    thickness, gap = 0.38, 3.0
    shoulder = (width - gap) / 2
    return [
        ("west", (x - width / 2 + thickness / 2, y + height / 2, z), (thickness, height, depth)),
        ("east", (x + width / 2 - thickness / 2, y + height / 2, z), (thickness, height, depth)),
        ("north", (x, y + height / 2, z - depth / 2 + thickness / 2), (width, height, thickness)),
        (
            "south-west",
            (x - gap / 2 - shoulder / 2, y + height / 2, z + depth / 2 - thickness / 2),
            (shoulder, height, thickness),
        ),
        (
            "south-east",
            (x + gap / 2 + shoulder / 2, y + height / 2, z + depth / 2 - thickness / 2),
            (shoulder, height, thickness),
        ),
        ("lintel", (x, y + 5.5, z + depth / 2 - thickness / 2), (gap, 5, thickness)),
        ("jamb-west", (x - 1.35, y + 1.5, z + depth / 2), (0.3, 3, 0.6)),
        ("jamb-east", (x + 1.35, y + 1.5, z + depth / 2), (0.3, 3, 0.6)),
    ]


def closed_edges(faces):
    counts = Counter()
    for face in faces:
        for index, a in enumerate(face):
            counts[tuple(sorted((a, face[(index + 1) % len(face)])))] += 1
    return all(count == 2 for count in counts.values())


def tower_sections():
    """Constant-width equipment decks replace the rejected continuous cone."""
    return [
        {"bottom": 5.8, "top": 20.5, "width": 7.8, "depth": 7.2},
        {"bottom": 20.5, "top": 35.0, "width": 5.6, "depth": 5.0},
        {"bottom": 35.0, "top": 47.0, "width": 3.5, "depth": 3.0},
    ]


def surface_grain(family, resolution=128):
    """New periodic material relief data, independent of private concept pixels."""
    seed = {"paint": 41, "metal": 87, "mineral": 129, "road": 219}[family]

    def noise(x, y, grid):
        x, y = x * grid / resolution, y * grid / resolution
        ix, iy = math.floor(x), math.floor(y)
        fx, fy = x - ix, y - iy
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)

        def sample(dx, dy):
            value = ((ix + dx) % grid) * 374761393 + ((iy + dy) % grid) * 668265263 + seed
            value = ((value ^ (value >> 13)) * 1274126177) & 0xFFFFFFFF
            return (value ^ (value >> 16)) / 0xFFFFFFFF

        a, b, c, d = sample(0, 0), sample(1, 0), sample(0, 1), sample(1, 1)
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy

    values = []
    for y in range(resolution):
        for x in range(resolution):
            coarse, fine = noise(x, y, 8), noise(x, y, 43)
            brush = 0.06 * math.sin(math.tau * y * 37 / resolution) if family == "metal" else 0
            values.append(0.68 * coarse + 0.32 * fine + brush)
    strength = {"paint": 0.35, "metal": 0.28, "mineral": 3.4, "road": 1.6}[family]
    normals = []
    for y in range(resolution):
        for x in range(resolution):
            dx = (
                values[y * resolution + (x + 1) % resolution]
                - values[y * resolution + (x - 1) % resolution]
            )
            dy = (
                values[((y + 1) % resolution) * resolution + x]
                - values[((y - 1) % resolution) * resolution + x]
            )
            normal = normalized((-dx * strength, -dy * strength, 1))
            normals.extend((normal[0] * 0.5 + 0.5, normal[1] * 0.5 + 0.5, normal[2] * 0.5 + 0.5, 1))
    return values, normals


def glb_attribute_values(document, binary, index):
    """Read actual accessor bytes, respecting both offsets and padded strides."""
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    if view.get("buffer", 0) != 0 or "sparse" in accessor:
        raise ValueError("reference attribute must use its embedded nonsparse buffer")
    formats = {5121: "B", 5123: "H", 5125: "I", 5126: "f"}
    components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    kind, component_type = accessor["type"], accessor["componentType"]
    if kind not in components or component_type not in formats:
        raise ValueError("unsupported reference accessor representation")
    unpack = "<" + formats[component_type] * components[kind]
    size = struct.calcsize(unpack)
    stride = view.get("byteStride", size)
    relative = accessor.get("byteOffset", 0)
    view_start = view.get("byteOffset", 0)
    count = accessor["count"]
    if count < 1 or stride < size or relative < 0 or view_start < 0:
        raise ValueError("invalid reference accessor bounds or stride")
    end = relative + (count - 1) * stride + size
    if end > view["byteLength"] or view_start + view["byteLength"] > len(binary):
        raise ValueError("reference accessor extends beyond its buffer view")
    divisor = {5121: 255, 5123: 65535}.get(component_type, 1)
    if not accessor.get("normalized", False):
        divisor = 1
    for position in range(count):
        values = struct.unpack_from(unpack, binary, view_start + relative + position * stride)
        yield tuple(value / divisor for value in values)


def validate_glb_material_attributes(raw):
    """Reject numeric corruption that a syntactically valid GLB can conceal."""
    if len(raw) < 20 or struct.unpack_from("<III", raw) != (0x46546C67, 2, len(raw)):
        raise ValueError("invalid reference GLB header")
    offset, document, binary = 12, None, None
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("truncated reference GLB chunk")
        size, kind = struct.unpack_from("<II", raw, offset)
        offset += 8
        if offset + size > len(raw):
            raise ValueError("truncated reference GLB chunk data")
        if kind == 0x4E4F534A:
            document = json.loads(raw[offset : offset + size])
        elif kind == 0x004E4942:
            binary = raw[offset : offset + size]
        offset += size
    if document is None or binary is None:
        raise ValueError("reference GLB needs embedded JSON and binary data")
    color_min, color_max, uv_max, colored = 1.0, 0.0, 0.0, 0
    for mesh in document.get("meshes", []):
        for primitive in mesh["primitives"]:
            attributes = primitive["attributes"]
            expected_count = document["accessors"][attributes["POSITION"]]["count"]
            for semantic in ("COLOR_0", "TEXCOORD_0", "NORMAL", "TANGENT"):
                if semantic not in attributes:
                    continue
                index = attributes[semantic]
                accessor = document["accessors"][index]
                if accessor["count"] != expected_count:
                    raise ValueError(f"{semantic} count does not match positions")
                if semantic == "COLOR_0":
                    colored += 1
                    if accessor["type"] not in {"VEC3", "VEC4"}:
                        raise ValueError("COLOR_0 requires RGB or RGBA values")
                    if accessor["componentType"] != 5126 and not accessor.get("normalized"):
                        raise ValueError("integer COLOR_0 data must be normalized")
                for values in glb_attribute_values(document, binary, index):
                    if not all(math.isfinite(value) for value in values):
                        raise ValueError(f"{semantic} contains nonfinite data")
                    if semantic == "COLOR_0":
                        if min(values) < 0 or max(values) > 1:
                            raise ValueError(
                                f"COLOR_0 outside linear [0, 1] in {mesh.get('name', 'mesh')}"
                            )
                        color_min, color_max = min(color_min, *values), max(color_max, *values)
                    elif semantic == "TEXCOORD_0":
                        uv_max = max(uv_max, *(abs(value) for value in values))
                    elif not 0.98 <= sum(value * value for value in values[:3]) <= 1.02:
                        raise ValueError(f"{semantic} is not a unit direction")
    return {
        "colored_primitives": colored,
        "linear_color_range": [color_min, color_max],
        "uv_abs_max": uv_max,
    }


def validate_source_path_records(records):
    """Validate field classes without including a rejected path in diagnostics."""
    for field, value in records:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        # Blender restores native separators after the unchanged // relative prefix.
        portable = value.replace("\\", "/") if value.startswith("//") else value
        if field in {"Image.filepath", "ImagePackedFile.filepath"}:
            valid = re.fullmatch(r"//materials/[a-z]+(?:-[a-z]+)*\.png", portable)
        elif field == "FileSelectParams.dir":
            valid = value == "//"
        elif field == "RenderData.pic":
            valid = portable == "//renders/"
        else:
            raise ValueError("unknown authoring path field class")
        if not valid:
            raise ValueError(f"{field} is not a portable authoring path")
    return {"validated_fields": len(records)}


def source_path_records(bpy_module):
    records = []
    for image in bpy_module.data.images:
        if not image.filepath:
            continue
        if not image.packed_files:
            raise ValueError("a file-backed authoring image is not packed")
        records.append(("Image.filepath", image.filepath))
        records.extend(
            ("ImagePackedFile.filepath", packed.filepath) for packed in image.packed_files
        )
    for screen in bpy_module.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "FILE_BROWSER" and space.params is not None:
                    records.append(("FileSelectParams.dir", space.params.directory))
    records.extend(("RenderData.pic", scene.render.filepath) for scene in bpy_module.data.scenes)
    return records


def sanitize_source_metadata(bpy_module):
    """Clear packed-image snapshots and file-browser defaults, preserving pixels."""

    def replace_field(owner, field, value):
        maximum = owner.bl_rna.properties[field].length_max
        # RNA can retain the tail of a fixed-size serialized char array. Replace
        # the entire capacity with neutral bytes before assigning the short path.
        neutral = "_" * (maximum - 1)
        if isinstance(value, bytes):
            neutral = neutral.encode("ascii")
        setattr(owner, field, neutral)
        setattr(owner, field, value)

    packed_count = 0
    for image in bpy_module.data.images:
        if not image.filepath:
            continue
        if not image.packed_files:
            raise ValueError("cannot sanitize an unpacked authoring image")
        filename = image.filepath.replace("\\", "/").rsplit("/", 1)[-1]
        portable = "//materials/" + filename
        validate_source_path_records([("Image.filepath", portable)])
        replace_field(image, "filepath", portable)
        for packed in image.packed_files:
            replace_field(packed, "filepath", portable)
            packed_count += 1
    browser_count = 0
    for screen in bpy_module.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "FILE_BROWSER" and space.params is not None:
                    replace_field(space.params, "directory", b"//")
                    replace_field(space.params, "filename", "")
                    browser_count += 1
    for scene in bpy_module.data.scenes:
        replace_field(scene.render, "filepath", "//renders/")
    result = validate_source_path_records(source_path_records(bpy_module))
    return {**result, "packed_image_files": packed_count, "file_browser_defaults": browser_count}


class Author:
    """Source objects stay editable; evaluated exports are batched by material."""

    def __init__(self, layout, study=STUDY):
        import bpy
        from mathutils import Vector

        self.bpy, self.Vector = bpy, Vector
        self.layout = layout
        self.study = study
        self.rng = random.Random(layout["seed"])  # noqa: S311 - authored geometry seed.
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        self.source = bpy.data.collections.new("Editable architecture")
        self.export = bpy.data.collections.new("Export")
        bpy.context.scene.collection.children.link(self.source)
        bpy.context.scene.collection.children.link(self.export)
        self.groups, self.materials = {}, {}
        self.colliders, self.anchors = [], []
        self.current = "terrain"
        self.palette()
        self.material_surface_maps()

    def palette(self):
        settings = {
            "cliff": ((0.29, 0.092, 0.034), 0.0, 0.96),
            "rock-highlight": ((0.42, 0.18, 0.077), 0.0, 0.9),
            "rock-shadow": ((0.19, 0.059, 0.027), 0.0, 0.98),
            "paving": ((0.24, 0.25, 0.24), 0.1, 0.83),
            "road": ((0.082, 0.096, 0.11), 0.08, 0.88),
            "concrete": ((0.39, 0.35, 0.29), 0.0, 0.84),
            "ceramic": ((0.73, 0.73, 0.67), 0.18, 0.34),
            "ceramic-warm": ((0.61, 0.61, 0.56), 0.22, 0.43),
            "structure": ((0.075, 0.095, 0.12), 0.74, 0.37),
            "metal": ((0.40, 0.46, 0.49), 0.82, 0.3),
            "gold": ((0.34, 0.23, 0.11), 0.8, 0.34),
            "rubber": ((0.025, 0.033, 0.041), 0.0, 0.75),
            "glass": ((0.04, 0.14, 0.2), 0.15, 0.2),
            "screen": ((0.024, 0.059, 0.084), 0.15, 0.25),
            "marking": ((0.67, 0.70, 0.66), 0.0, 0.8),
            "ochre": ((0.48, 0.29, 0.075), 0.1, 0.62),
            "blue": ((0.012, 0.30, 0.75), 0.2, 0.24),
            "warm-light": ((0.95, 0.64, 0.24), 0.0, 0.35),
            "painted-alloy": ((0.34, 0.39, 0.41), 0.36, 0.50),
            "roof-alloy": ((0.40, 0.42, 0.40), 0.35, 0.65),
            "dust-coated": ((0.34, 0.25, 0.17), 0.12, 0.86),
        }
        for name, (color, metallic, roughness) in settings.items():
            material = self.bpy.data.materials.new(name)
            material.diffuse_color = (*color, 1)
            material.use_nodes = True
            shader = material.node_tree.nodes.get("Principled BSDF")
            shader.inputs["Base Color"].default_value = (*color, 1)
            shader.inputs["Metallic"].default_value = metallic
            shader.inputs["Roughness"].default_value = roughness
            if name in {"blue", "warm-light"}:
                shader.inputs["Emission Color"].default_value = (*color, 1)
                shader.inputs["Emission Strength"].default_value = 3.0 if name == "blue" else 1.8
            if name == "glass":
                shader.inputs["Transmission Weight"].default_value = 0.62
                shader.inputs["IOR"].default_value = 1.45
            self.materials[name] = material

    def material_surface_maps(self):
        """Embed authored roughness/normal maps and material-aware color tinting."""
        folder = self.study / "source/materials"
        folder.mkdir(parents=True, exist_ok=True)
        resolution = 128
        grains = {
            family: surface_grain(family, resolution)
            for family in ("paint", "metal", "mineral", "road")
        }
        families = {
            "cliff": "mineral",
            "rock-highlight": "mineral",
            "rock-shadow": "mineral",
            "concrete": "mineral",
            "paving": "mineral",
            "road": "road",
            "ceramic": "paint",
            "ceramic-warm": "paint",
            "painted-alloy": "paint",
            "roof-alloy": "metal",
            "structure": "metal",
            "metal": "metal",
            "gold": "metal",
            "dust-coated": "mineral",
        }
        normal_images = {}
        self.material_families = families
        for family, (_, pixels) in grains.items():
            image = self.bpy.data.images.new(f"Authored {family} relief", resolution, resolution)
            image.colorspace_settings.name = "Non-Color"
            image.pixels.foreach_set(array("f", pixels))
            image.filepath_raw = str(folder / f"{family}-normal.png")
            image.file_format = "PNG"
            image.save()
            image.filepath = f"//materials/{family}-normal.png"
            image.pack()
            normal_images[family] = image
        for name, family in families.items():
            material = self.materials[name]
            shader = material.node_tree.nodes.get("Principled BSDF")
            roughness = shader.inputs["Roughness"].default_value
            pixels = array("f")
            for grain in grains[family][0]:
                value = min(0.99, max(0.08, roughness + (grain - 0.5) * 0.22))
                pixels.extend((value, value, value, 1))
            image = self.bpy.data.images.new(f"Authored {name} roughness", resolution, resolution)
            image.colorspace_settings.name = "Non-Color"
            image.pixels.foreach_set(pixels)
            image.filepath_raw = str(folder / f"{name}-roughness.png")
            image.file_format = "PNG"
            image.save()
            image.filepath = f"//materials/{name}-roughness.png"
            image.pack()
            nodes, links = material.node_tree.nodes, material.node_tree.links
            roughness_map = nodes.new("ShaderNodeTexImage")
            roughness_map.image = image
            links.new(roughness_map.outputs["Color"], shader.inputs["Roughness"])
            texture = nodes.new("ShaderNodeTexImage")
            texture.image = normal_images[family]
            normal = nodes.new("ShaderNodeNormalMap")
            normal.inputs["Strength"].default_value = 0.65 if family == "mineral" else 0.35
            links.new(texture.outputs["Color"], normal.inputs["Color"])
            links.new(normal.outputs["Normal"], shader.inputs["Normal"])
            color = nodes.new("ShaderNodeVertexColor")
            color.layer_name = "Surface tint"
            links.new(color.outputs["Color"], shader.inputs["Base Color"])

    def group(self, name):
        self.current = name
        if name not in self.groups:
            collection = self.bpy.data.collections.new(name)
            self.source.children.link(collection)
            self.groups[name] = collection

    def adopt(self, obj, name, material, smooth=False, bevel=0):
        obj.name = name
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        if self.current not in self.groups:
            self.group(self.current)
        self.groups[self.current].objects.link(obj)
        obj.data.materials.append(self.materials[material])
        if smooth:
            for polygon in obj.data.polygons:
                polygon.use_smooth = True
        if bevel:
            modifier = obj.modifiers.new("Manufactured edge radius", "BEVEL")
            modifier.width, modifier.segments = bevel, 3
            modifier.limit_method = "ANGLE"
            modifier = obj.modifiers.new("Weighted corner normals", "WEIGHTED_NORMAL")
            modifier.keep_sharp = True
        return obj

    def mesh(self, name, vertices, faces, material, smooth=False):
        mesh = self.bpy.data.meshes.new(name)
        mesh.from_pydata([blender_point(vertex) for vertex in vertices], [], faces)
        mesh.update()
        obj = self.bpy.data.objects.new(name, mesh)
        self.groups[self.current].objects.link(obj)
        return self.adopt(obj, name, material, smooth)

    def box(self, name, center, size, material, bevel=0.06, yaw=0):
        self.bpy.ops.mesh.primitive_cube_add(size=1, location=blender_point(center))
        obj = self.bpy.context.object
        obj.dimensions = (size[0], size[2], size[1])
        self.bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.rotation_euler.z = -yaw
        return self.adopt(obj, name, material, bevel=bevel)

    def cylinder(self, name, center, radius, height, material, top=None, vertices=48):
        self.bpy.ops.mesh.primitive_cone_add(
            vertices=vertices,
            radius1=radius,
            radius2=radius if top is None else top,
            depth=height,
            location=blender_point(center),
        )
        return self.adopt(self.bpy.context.object, name, material, smooth=True, bevel=0.035)

    def beam(self, name, start, end, radius, material, vertices=10):
        direction = self.Vector(blender_point(tuple(end[i] - start[i] for i in range(3))))
        self.bpy.ops.mesh.primitive_cylinder_add(
            vertices=vertices,
            radius=radius,
            depth=direction.length,
            location=blender_point(lerp(start, end, 0.5)),
        )
        obj = self.bpy.context.object
        obj.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
        return self.adopt(obj, name, material, smooth=True)

    def tube(self, name, points, radius, material, closed=False, resolution=2):
        curve = self.bpy.data.curves.new(name, "CURVE")
        curve.dimensions, curve.resolution_u = "3D", 1
        curve.bevel_depth, curve.bevel_resolution = radius, resolution
        curve.resolution_u = 1
        spline = curve.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for vertex, point in zip(spline.points, points, strict=True):
            vertex.co = (*blender_point(point), 1)
        spline.use_cyclic_u = closed
        obj = self.bpy.data.objects.new(name, curve)
        self.groups[self.current].objects.link(obj)
        curve.materials.append(self.materials[material])
        return obj

    def ring(self, name, center, radius, thickness, material, segments=64):
        return self.tube(
            name,
            [
                add(
                    center,
                    (
                        radius * math.cos(math.tau * i / segments),
                        0,
                        radius * math.sin(math.tau * i / segments),
                    ),
                )
                for i in range(segments)
            ],
            thickness,
            material,
            True,
        )

    def text(self, name, body, center, size, material, yaw=0):
        curve = self.bpy.data.curves.new(name, "FONT")
        curve.body, curve.size, curve.align_x, curve.align_y = body, size, "CENTER", "CENTER"
        curve.extrude, curve.resolution_u = 0.002, 2
        obj = self.bpy.data.objects.new(name, curve)
        self.groups[self.current].objects.link(obj)
        obj.location = blender_point(center)
        # Face runtime +Z (south); local text Y becomes runtime up.
        obj.rotation_euler = (math.pi / 2, 0, -yaw)
        curve.materials.append(self.materials[material])
        return obj

    def collider(self, identifier, center, size, **extras):
        self.colliders.append(
            {"id": identifier, "shape": "box", "center": list(center), "size": list(size), **extras}
        )

    def anchor(self, identifier, position, **extras):
        record = {"id": identifier, "position": list(position), **extras}
        previous = next((item for item in self.anchors if item["id"] == identifier), None)
        if previous is not None:
            if previous != record:
                raise ValueError(f"anchor identity has conflicting definitions: {identifier}")
            return
        self.anchors.append(record)

    def terrain(self):
        self.group("01 Rock strata and foundations")
        vertices, faces = plateau_mesh(self.layout["seed"])
        cliff = self.mesh("Layered closed Outpost plateau", vertices, faces, "cliff")
        # Color variation follows stone layers rather than random decoration.
        cliff.data.materials.append(self.materials["rock-highlight"])
        cliff.data.materials.append(self.materials["rock-shadow"])
        for polygon in cliff.data.polygons:
            height = polygon.center.z
            polygon.material_index = (
                1 if -19 < height < -15 or -39 < height < -35 else (2 if -31 < height < -27 else 0)
            )
        self.colliders.append(
            {
                "id": "plateau-surface",
                "shape": "plateau",
                "top_y": -0.12,
                "footprint": [[point[0], point[2]] for point in vertices[:128]],
                "bottom_y": min(point[1] for point in vertices),
                "walkable": True,
            }
        )
        for index in range(68):
            angle = math.tau * index / 68
            x = math.copysign(abs(math.cos(angle)) ** 0.58, math.cos(angle)) * 59
            z = math.copysign(abs(math.sin(angle)) ** 0.58, math.sin(angle)) * 46
            self.bpy.ops.mesh.primitive_ico_sphere_add(
                subdivisions=1, radius=1, location=blender_point((x, self.rng.uniform(-25, -3), z))
            )
            rock = self.bpy.context.object
            rock.scale = (
                self.rng.uniform(1.6, 3.8),
                self.rng.uniform(1.4, 3.1),
                self.rng.uniform(3, 8),
            )
            self.adopt(
                rock,
                f"Exposed vertical fracture {index:02d}",
                "rock-highlight" if index % 4 == 0 else "cliff",
            )
        # Landing footings account for the west route extending beyond the rock lip.
        for center, size in [((-58, -2.1, 10), (20, 4, 27)), ((4, -2.1, 43), (21, 4, 16))]:
            self.box("Cantilever arrival abutment", center, size, "concrete", 0.2)
        self.anchor(
            "outpost-origin",
            (0, 0, 0),
            kind="district",
            layout_version=self.layout["layout_version"],
        )

    def rounded_perimeter(self, y=0):
        points = []
        for cx, cz, start in [(41, 28, 0), (-41, 28, 90), (-41, -28, 180), (41, -28, 270)]:
            for step in range(13):
                angle = math.radians(start + step * 90 / 12)
                points.append((cx + 14 * math.cos(angle), y, cz + 14 * math.sin(angle)))
        return points

    def paths(self):
        self.group("02 Paving and protected routes")
        outer = self.rounded_perimeter(-0.03)
        self.mesh(
            "Continuous engineered terrace",
            [(0, -0.03, 0), *outer],
            [(0, 1 + (i + 1) % len(outer), i + 1) for i in range(len(outer))],
            "paving",
        )
        for index, start in enumerate(outer):
            end = outer[(index + 1) % len(outer)]
            vertices, faces = deck_mesh(
                add(start, (0, 0.035, 0)), add(end, (0, 0.035, 0)), 5.8, 0.14
            )
            self.mesh(f"Perimeter road segment {index:02d}", vertices, faces, "road")
        self.tube(
            "Inner perimeter lane marking",
            [(p[0] * 0.943, 0.035, p[2] * 0.936) for p in outer],
            0.035,
            "marking",
            True,
            1,
        )
        nodes = self.layout["nodes"]
        for label, first, last, width in [
            ("Arrival road", "outpost-west", "outpost-arrival", 10),
            ("Operations footpath", "outpost-arrival", "console-approach", 4),
            ("South transport link", "outpost-arrival", "outpost-south", 10),
        ]:
            start, end = nodes[first], nodes[last]
            vertices, faces = deck_mesh(
                add(start, (0, 0.006, 0)), add(end, (0, 0.006, 0)), width, 0.24
            )
            self.mesh(label, vertices, faces, "paving" if width == 4 else "road")
            self.anchor(first, start, kind="navigation")
            self.anchor(last, end, kind="navigation")
        # Seams are organized in service plazas, away from travel corridors.
        for z in (-17, -13, -9, 26, 30):
            self.tube(
                "Terrace expansion joint",
                [(-12, 0.003, z), (21, 0.003, z)],
                0.018,
                "rubber",
                resolution=1,
            )
        for index, point in enumerate(outer):
            if index % 3:
                continue
            x, _, z = point
            if x < -50 and -8 < z < 25 or z > 38 and -6 < x < 15:
                continue
            outward = normalized((x / 55, 0, z / 42))
            base = add(point, scale(outward, 3.3))
            self.lamp(f"Perimeter luminaire {index:02d}", base, 3.3)
        # A low retaining curb leaves the actual arrival/south junctions open.
        for index, start in enumerate(outer):
            end = outer[(index + 1) % len(outer)]
            mid = lerp(start, end, 0.5)
            if mid[0] < -50 and -8 < mid[2] < 25 or mid[2] > 38 and -8 < mid[0] < 18:
                continue
            raised = [(p[0] * 1.059, 0.45, p[2] * 1.076) for p in (start, end)]
            self.beam("Terrace edge guardrail", raised[0], raised[1], 0.07, "metal")
            self.beam(
                "Terrace edge post",
                (raised[0][0], 0, raised[0][2]),
                add(raised[0], (0, 0.65, 0)),
                0.065,
                "structure",
            )

    def lamp(self, name, base, height):
        self.box(name + " foot", add(base, (0, 0.08, 0)), (0.42, 0.16, 0.42), "structure", 0.04)
        self.box(
            name + " mast", add(base, (0, height / 2, 0)), (0.10, height, 0.16), "metal", 0.015
        )
        self.box(
            name + " housing", add(base, (0, height, 0)), (0.65, 0.15, 0.42), "structure", 0.04
        )
        self.box(
            name + " diffuser",
            add(base, (0, height - 0.081, 0)),
            (0.51, 0.025, 0.31),
            "warm-light",
            0.01,
        )

    def roof_plant(self, name, center, width, depth, roof_y):
        """Mounted roof plant stays within the building's existing footprint."""
        x, _, z = center
        for sign in (-1, 1):
            self.box(
                name + " parapet long side",
                (x, roof_y + 0.20, z + sign * (depth / 2 - 0.20)),
                (width - 0.1, 0.4, 0.26),
                "ceramic-warm",
                0.05,
            )
            self.box(
                name + " parapet return",
                (x + sign * (width / 2 - 0.2), roof_y + 0.20, z),
                (0.26, 0.4, depth - 0.2),
                "ceramic-warm",
                0.05,
            )
        self.box(
            name + " rooftop mechanical skid",
            (x + 0.5, roof_y + 0.32, z - 0.4),
            (width * 0.48, 0.52, depth * 0.44),
            "painted-alloy",
            0.12,
        )
        fan_radius = min(width, depth) * 0.13
        for sign in (-1, 1):
            fan = (x + 0.5 + sign * width * 0.14, roof_y + 0.64, z - 0.4)
            self.cylinder(
                name + " ventilation fan shroud", fan, fan_radius, 0.22, "metal", vertices=40
            )
            self.cylinder(
                name + " recessed fan opening",
                add(fan, (0, 0.11, 0)),
                fan_radius * 0.82,
                0.04,
                "structure",
                vertices=40,
            )
            for fraction in (0.25, 0.52, 0.78):
                self.ring(
                    name + " fan protection ring",
                    add(fan, (0, 0.15, 0)),
                    fan_radius * fraction,
                    0.022,
                    "metal",
                    40,
                )
            for angle in (0, math.pi / 2):
                direction = (math.cos(angle), 0, math.sin(angle))
                self.beam(
                    name + " fan cross brace",
                    add(fan, add(scale(direction, -fan_radius * 0.82), (0, 0.16, 0))),
                    add(fan, add(scale(direction, fan_radius * 0.82), (0, 0.16, 0))),
                    0.024,
                    "metal",
                    8,
                )
        self.tube(
            name + " roof duct return",
            [
                (x - width * 0.30, roof_y + 0.24, z + 0.7),
                (x - width * 0.30, roof_y + 0.68, z + 0.7),
                (x - width * 0.10, roof_y + 0.68, z + 0.7),
            ],
            0.23,
            "metal",
            resolution=3,
        )
        for sign in (-1, 1):
            self.box(
                name + " equipment mounting saddle",
                (x + sign * width * 0.22, roof_y + 0.10, z - 0.4),
                (0.2, 0.20, depth * 0.54),
                "structure",
                0.04,
            )

    def bridge(self):
        self.group("03 Colony bridge and pier structure")
        start, end = self.layout["nodes"]["hub-east"], self.layout["nodes"]["outpost-west"]
        vertices, faces = deck_mesh(start, end)
        self.mesh("Graded 10 metre bridge deck", vertices, faces, "road")
        direction = normalized((end[0] - start[0], 0, end[2] - start[2]))
        side = (-direction[2], 0, direction[0])
        for sign in (-1, 1):
            offset = scale(side, sign * 4.8)
            for height, radius, material in [
                (1.20, 0.075, "metal"),
                (0.58, 0.055, "metal"),
                (0.1, 0.08, "concrete"),
            ]:
                self.beam(
                    "Continuous bridge railing",
                    add(start, add(offset, (0, height, 0))),
                    add(end, add(offset, (0, height, 0))),
                    radius,
                    material,
                    12,
                )
            for index in range(28):
                center = add(lerp(start, end, index / 27), offset)
                self.beam(
                    "Bridge railing upright", center, add(center, (0, 1.23, 0)), 0.055, "structure"
                )
                if index % 4 == 0:
                    self.box(
                        "Bridge pedestrian light",
                        add(center, (0, 0.8, 0)),
                        (0.14, 0.35, 0.14),
                        "warm-light",
                        0.015,
                    )
            girder_start = add(start, add(scale(side, sign * 3.8), (0, -1.1, 0)))
            girder_end = add(end, add(scale(side, sign * 3.8), (0, -1.1, 0)))
            self.beam(
                "Longitudinal bridge box girder", girder_start, girder_end, 0.43, "structure", 4
            )
            self.beam(
                "Sidewalk segregation strip",
                add(start, scale(side, sign * 3.5)),
                add(end, scale(side, sign * 3.5)),
                0.05,
                "marking",
                6,
            )
        for index in range(1, 7):
            point = lerp(start, end, index / 7)
            bottom = -58
            self.box(
                f"Pier {index} spread footing",
                (point[0], bottom + 0.8, point[2]),
                (7.2, 1.6, 11),
                "concrete",
                0.2,
            )
            for sign in (-1, 1):
                offset = scale(side, sign * 3.0)
                lower = (point[0] + offset[0], bottom + 1.6, point[2] + offset[2])
                upper = add(point, add(offset, (0, -1.1, 0)))
                self.beam(f"Pier {index} reinforced column", lower, upper, 0.95, "concrete", 8)
            self.beam(
                f"Pier {index} transverse cap",
                add(point, add(scale(side, -5), (0, -1.4, 0))),
                add(point, add(scale(side, 5), (0, -1.4, 0))),
                0.7,
                "concrete",
                4,
            )
        for index in range(1, 27):
            point = lerp(start, end, index / 27)
            self.beam(
                "Deck transverse rib",
                add(point, add(scale(side, -4.7), (0, -0.8, 0))),
                add(point, add(scale(side, 4.7), (0, -0.8, 0))),
                0.15,
                "metal",
                4,
            )
            if index % 2:
                tangent = scale(direction, 1.8)
                self.beam(
                    "Bridge centre dash",
                    add(point, scale(tangent, -0.5)),
                    add(point, scale(tangent, 0.5)),
                    0.045,
                    "marking",
                    6,
                )
        self.colliders.append(
            {
                "id": "bridge-deck",
                "shape": "graded-deck",
                "start": list(start),
                "end": list(end),
                "width": 10,
                "thickness": 0.65,
                "walkable": True,
            }
        )
        self.anchor("bridge-colony-end", start, kind="navigation")
        self.anchor("bridge-outpost-end", end, kind="navigation")

    def tower(self):
        self.group("04 Stepped communications mast")
        center = self.layout["buildings"]["communications-mast"]["local"]
        self.cylinder(
            "Tower spread foundation", add(center, (0, 0.18, 0)), 9.2, 0.36, "concrete", vertices=80
        )
        self.cylinder(
            "Equipment base weather skirt",
            add(center, (0, 0.55, 0)),
            7,
            1.1,
            "dust-coated",
            vertices=12,
        )
        self.cylinder(
            "Substantial communications service drum",
            add(center, (0, 3.2, 0)),
            6.8,
            5.5,
            "painted-alloy",
            vertices=12,
        )
        self.cylinder(
            "Service drum roof shoulder", add(center, (0, 6, 0)), 7, 0.4, "roof-alloy", vertices=12
        )
        for index in range(12):
            angle = math.tau * index / 12
            normal = (math.sin(angle), 0, math.cos(angle))
            tangent = (math.cos(angle), 0, -math.sin(angle))
            side = add(center, scale(normal, 6.58))
            self.box(
                "Drum recessed equipment bay",
                add(side, (0, 3, 0)),
                (2.7, 3.9, 0.22),
                "structure",
                0.12,
                angle,
            )
            self.box(
                "Drum ceramic access hatch",
                add(side, add(scale(normal, 0.13), (0, 3.05, 0))),
                (2.35, 3.45, 0.16),
                "ceramic-warm" if index % 3 else "ceramic",
                0.10,
                angle,
            )
            for sign in (-1, 1):
                self.box(
                    "Drum hatch vertical lock rail",
                    add(
                        side,
                        add(scale(tangent, sign * 1.01), add(scale(normal, 0.23), (0, 2.9, 0))),
                    ),
                    (0.11, 2.7, 0.09),
                    "metal",
                    0.02,
                    angle,
                )
            self.box(
                "Drum hatch inspection window",
                add(side, add(scale(normal, 0.23), (0, 3.8, 0))),
                (1.45, 0.48, 0.06),
                "glass",
                0.045,
                angle,
            )
            self.box(
                "Drum illuminated bay header",
                add(side, add(scale(normal, 0.22), (0, 5.2, 0))),
                (1.7, 0.12, 0.1),
                "warm-light",
                0.025,
                angle,
            )
        for level, section in enumerate(tower_sections()):
            bottom, top = section["bottom"], section["top"]
            width, depth = section["width"], section["depth"]
            middle = (bottom + top) / 2
            self.box(
                f"Core equipment section {level}",
                add(center, (0, middle, 0)),
                (width, top - bottom, depth),
                "painted-alloy",
                0.28,
            )
            self.box(
                f"Core recessed level collar {level}",
                add(center, (0, bottom + 0.3, 0)),
                (width + 0.32, 0.6, depth + 0.32),
                "structure",
                0.14,
            )
            self.box(
                f"Core overhanging weather cap {level}",
                add(center, (0, top - 0.18, 0)),
                (width + 0.48, 0.36, depth + 0.48),
                "roof-alloy",
                0.14,
            )
            for sign in (-1, 1):
                for panel_index in range(3):
                    panel_y = bottom + 2.7 + panel_index * (top - bottom - 2) / 3
                    panel_height = (top - bottom - 2.5) / 3
                    self.box(
                        f"South-north removable tower bay {level}-{panel_index}",
                        add(center, (0, panel_y, sign * (depth / 2 + 0.075))),
                        (width * 0.72, panel_height, 0.14),
                        "ceramic-warm" if panel_index % 2 else "ceramic",
                        0.09,
                    )
                    self.box(
                        "Tower bay recessed vertical gasket",
                        add(center, (width * 0.31, panel_y, sign * (depth / 2 + 0.16))),
                        (0.055, panel_height * 0.86, 0.035),
                        "rubber",
                        0.01,
                    )
                self.box(
                    f"Lateral spine panel {level}",
                    add(center, (sign * (width / 2 + 0.05), middle, 0)),
                    (0.14, top - bottom - 1.1, depth * 0.54),
                    "ceramic-warm",
                    0.08,
                )
                for rise in (2.0, top - bottom - 2.0):
                    self.box(
                        "Tower coupling access cover",
                        add(center, (sign * (width / 2 + 0.14), bottom + rise, 0)),
                        (0.18, 1.4, 1.5),
                        "metal",
                        0.06,
                    )
        # Four independently staggered aerial racks define the communications silhouette.
        for index, angle in enumerate(
            (0.15, math.pi / 2 + 0.15, math.pi + 0.15, math.pi * 1.5 + 0.15)
        ):
            normal = (math.sin(angle), 0, math.cos(angle))
            tangent = (math.cos(angle), 0, -math.sin(angle))
            radius = 5.25
            bottom, top = 7.0, 29 + (index % 3) * 5
            low = add(center, add(scale(normal, radius), (0, bottom, 0)))
            high = add(center, add(scale(normal, radius), (0, top, 0)))
            for sign in (-1, 1):
                self.beam(
                    "Aerial rack paired rail",
                    add(low, scale(tangent, sign * 0.48)),
                    add(high, scale(tangent, sign * 0.48)),
                    0.11,
                    "metal",
                    10,
                )
            for step in range(int((top - bottom) / 2.5)):
                a = add(low, add(scale(tangent, -0.48 if step % 2 else 0.48), (0, step * 2.5, 0)))
                b = add(
                    low, add(scale(tangent, 0.48 if step % 2 else -0.48), (0, (step + 1) * 2.5, 0))
                )
                self.beam("Aerial rack diagonal truss", a, b, 0.05, "structure", 8)
            for panel_index in range(3):
                height = top - 2.1 - panel_index * 6.2
                at = add(center, add(scale(normal, radius + 0.12), (0, height, 0)))
                self.box(
                    "Staggered RF antenna module", at, (1.35, 3.8, 0.62), "ceramic", 0.2, angle
                )
                self.box(
                    "RF module backing and heat sink",
                    add(at, scale(normal, -0.40)),
                    (1.15, 3.55, 0.25),
                    "painted-alloy",
                    0.08,
                    angle,
                )
                for offset in (-0.32, 0, 0.32):
                    self.beam(
                        "RF cooling rib",
                        add(
                            at, add(scale(tangent, offset), add(scale(normal, -0.57), (0, -1.4, 0)))
                        ),
                        add(
                            at, add(scale(tangent, offset), add(scale(normal, -0.57), (0, 1.4, 0)))
                        ),
                        0.035,
                        "metal",
                        8,
                    )
            self.beam(
                "Aerial rack base brace",
                add(center, add(scale(normal, 6.35), (0, 0.8, 0))),
                low,
                0.24,
                "painted-alloy",
                8,
            )
        # Open upper lattice carries short antenna panels; it is not a cone cladding.
        for sign_x in (-1, 1):
            for sign_z in (-1, 1):
                self.beam(
                    "Upper mast structural chord",
                    add(center, (sign_x * 1.18, 46.8, sign_z * 1.05)),
                    add(center, (sign_x * 0.58, 57, sign_z * 0.52)),
                    0.09,
                    "metal",
                    10,
                )
        for step in range(5):
            y = 47 + step * 2
            radius = 1.18 - step * 0.11
            for sign in (-1, 1):
                self.beam(
                    "Upper mast bracing",
                    add(center, (-radius, y, sign * radius)),
                    add(center, (radius - 0.1, y + 2, sign * (radius - 0.1))),
                    0.045,
                    "structure",
                    8,
                )
        for sign, height in [(-1, 50), (1, 54)]:
            self.box(
                "Upper point-to-point aerial",
                add(center, (sign * 1.6, height, 0)),
                (0.6, 3.0, 0.72),
                "ceramic",
                0.16,
            )
            self.beam(
                "Upper aerial mount",
                add(center, (0, height - 0.4, 0)),
                add(center, (sign * 1.6, height - 0.4, 0)),
                0.09,
                "metal",
            )
        self.cylinder("Top antenna shaft", add(center, (0, 59.2, 0)), 0.20, 4.4, "metal", top=0.11)
        self.cylinder(
            "Blue beacon enclosure", add(center, (0, 61.6, 0)), 0.43, 0.5, "blue", top=0.32
        )
        self.cylinder("Lightning tip", add(center, (0, 61.94, 0)), 0.07, 0.12, "metal", top=0.01)
        for height, x in ((16, 4.2), (32, -3.0), (45, 1.9)):
            self.box(
                "Sparse blue relay hardware",
                add(center, (x, height, 0.5)),
                (0.18, 0.95, 0.20),
                "blue",
                0.06,
            )
        self.box(
            "Blank mast brand plate",
            add(center, (0, 12.6, 3.78)),
            (3.4, 2.8, 0.12),
            "ceramic",
            0.10,
        )
        self.anchor(
            "mast-brand-slot",
            add(center, (0, 12.6, 3.86)),
            kind="branding-slot",
            verified_source=False,
        )
        self.collider("communications-mast", add(center, (0, 31, 0)), (14, 62, 14))

    def reflector(self, identifier, radius, depth, height, aim):
        self.group("05 " + identifier)
        center = self.layout["buildings"][identifier]["local"]
        self.cylinder(
            identifier + " foundation",
            add(center, (0, 0.25, 0)),
            radius * 0.7,
            0.5,
            "concrete",
            vertices=64,
        )
        self.cylinder(
            identifier + " equipment house",
            add(center, (0, 2, 0)),
            radius * 0.61,
            3.6,
            "ceramic-warm",
            vertices=12,
        )
        self.ring(
            identifier + " slew bearing", add(center, (0, 4, 0)), radius * 0.53, 0.25, "metal"
        )
        self.cylinder(
            identifier + " azimuth pedestal",
            add(center, (0, 6.4, 0)),
            2.5,
            4.5,
            "structure",
            top=1.7,
            vertices=24,
        )
        direction = normalized(aim)
        horizontal = normalized(cross((0, 1, 0), direction))
        vertical = normalized(cross(direction, horizontal))
        origin = add(center, (0, height, 0))

        def position(point):
            return add(
                origin,
                add(
                    scale(horizontal, point[0]),
                    add(scale(direction, point[1]), scale(vertical, point[2])),
                ),
            )

        vertices, faces = dish_mesh(radius, depth)
        self.mesh(
            identifier + " parabolic front and rear shell",
            [position(point) for point in vertices],
            faces,
            "ceramic",
            True,
        )
        for fraction in (0.33, 0.67, 1.0):
            ring_radius = radius * fraction
            self.tube(
                identifier + " reflector seam",
                [
                    position(
                        (
                            ring_radius * math.cos(math.tau * i / 72),
                            depth * fraction**2 + 0.018,
                            ring_radius * math.sin(math.tau * i / 72),
                        )
                    )
                    for i in range(72)
                ],
                0.018 if fraction < 1 else 0.075,
                "metal",
                True,
                1,
            )
        for index in range(16):
            angle = math.tau * index / 16
            self.tube(
                identifier + " radial panel seam",
                [
                    position(
                        (
                            radius * fraction * math.cos(angle),
                            depth * fraction**2 + 0.021,
                            radius * fraction * math.sin(angle),
                        )
                    )
                    for fraction in [i / 20 for i in range(1, 21)]
                ],
                0.015,
                "ceramic-warm",
                resolution=1,
            )
            self.tube(
                identifier + " rear structural rib",
                [
                    position(
                        (
                            radius * fraction * math.cos(angle),
                            depth * fraction**2 - 0.20 - 0.25 * (1 - fraction),
                            radius * fraction * math.sin(angle),
                        )
                    )
                    for fraction in [i / 12 for i in range(13)]
                ],
                0.09,
                "structure",
                resolution=1,
            )
        for fraction in (0.35, 0.72):
            self.tube(
                identifier + " rear circumferential brace",
                [
                    position(
                        (
                            radius * fraction * math.cos(math.tau * i / 64),
                            depth * fraction**2 - 0.28,
                            radius * fraction * math.sin(math.tau * i / 64),
                        )
                    )
                    for i in range(64)
                ],
                0.13,
                "metal",
                True,
                1,
            )
        focus = position((0, radius**2 / (4 * depth), 0))
        for index in range(3):
            angle = math.tau * index / 3
            rim = position(
                (radius * 0.87 * math.cos(angle), depth * 0.87**2, radius * 0.87 * math.sin(angle))
            )
            self.beam(identifier + " feed support strut", rim, focus, 0.075, "structure", 10)
        self.beam(
            identifier + " receiver horn",
            add(focus, scale(direction, -0.55)),
            add(focus, scale(direction, 0.22)),
            0.26,
            "gold",
            16,
        )
        for sign in (-1, 1):
            lower = add(center, (sign * 2.1, 7.5, 0))
            upper = position((sign * 2.9, -0.48, 0))
            self.beam(identifier + " elevation yoke", lower, upper, 0.46, "structure", 8)
            self.beam(
                identifier + " hydraulic actuator",
                add(center, (sign * 1.3, 5.2, -0.8)),
                position((sign * 2, -0.6, -radius * 0.5)),
                0.16,
                "metal",
                12,
            )
        for index in range(6):
            angle = math.tau * index / 6
            side = (math.sin(angle), 0, math.cos(angle))
            panel = add(center, add(scale(side, radius * 0.58), (0, 1.9, 0)))
            self.box(
                identifier + " radial service panel", panel, (2.1, 2.35, 0.2), "ceramic", 0.1, angle
            )
        self.collider(
            identifier + " base", add(center, (0, 4.5, 0)), (radius * 1.3, 9, radius * 1.3)
        )
        self.anchor(
            identifier + " mechanism", origin, kind="exterior-equipment", access="not-accessible"
        )

    def radome(self, identifier, radius, total_height):
        self.group("06 " + identifier)
        center = self.layout["buildings"][identifier]["local"]
        drum_height = total_height - radius
        self.cylinder(
            identifier + " slab",
            add(center, (0, 0.2, 0)),
            radius + 0.75,
            0.4,
            "concrete",
            vertices=72,
        )
        self.cylinder(
            identifier + " support drum",
            add(center, (0, drum_height / 2, 0)),
            radius,
            drum_height,
            "ceramic-warm",
            vertices=48,
        )
        self.ring(
            identifier + " dark weather seal",
            add(center, (0, drum_height, 0)),
            radius,
            0.13,
            "rubber",
        )
        vertices = [add(center, (0, total_height, 0))]
        faces = []
        rings, segments = 12, 48
        for ring in range(1, rings + 1):
            phi = ring / rings * math.pi / 2
            for index in range(segments):
                angle = math.tau * index / segments
                vertices.append(
                    add(
                        center,
                        (
                            radius * math.sin(phi) * math.cos(angle),
                            drum_height + radius * math.cos(phi),
                            radius * math.sin(phi) * math.sin(angle),
                        ),
                    )
                )
        for index in range(segments):
            faces.append((0, 1 + (index + 1) % segments, 1 + index))
        for ring in range(rings - 1):
            for index in range(segments):
                following = (index + 1) % segments
                a, b = 1 + ring * segments + index, 1 + ring * segments + following
                faces.extend(((a, b, a + segments), (b, b + segments, a + segments)))
        self.mesh(identifier + " double-sided RF shell", vertices, faces, "ceramic", True)
        # Thin raised triangular seams convey assembled RF panels at all angles.
        for ring in range(2, rings + 1, 2):
            phi = ring / rings * math.pi / 2
            r, y = radius * math.sin(phi) + 0.012, drum_height + radius * math.cos(phi)
            self.ring(
                identifier + " latitudinal panel seal",
                add(center, (0, y, 0)),
                r,
                0.027,
                "ceramic-warm",
                48,
            )
        for index in range(16):
            angle = math.tau * index / 16
            points = []
            for ring in range(1, rings + 1):
                phi = ring / rings * math.pi / 2
                offset = 0.055 * (ring % 2)
                points.append(
                    add(
                        center,
                        (
                            (radius + 0.015) * math.sin(phi) * math.cos(angle + offset),
                            drum_height + (radius + 0.015) * math.cos(phi),
                            (radius + 0.015) * math.sin(phi) * math.sin(angle + offset),
                        ),
                    )
                )
            self.tube(
                identifier + " meridian panel seal", points, 0.028, "ceramic-warm", resolution=1
            )
        for index in range(12):
            angle = math.tau * index / 12
            x, z = radius * math.sin(angle), radius * math.cos(angle)
            self.box(
                identifier + " base pilaster",
                add(center, (x, drum_height / 2, z)),
                (0.36, drum_height - 0.3, 0.4),
                "metal",
                0.035,
                angle,
            )
            if index % 3 == 0:
                self.box(
                    identifier + " service status light",
                    add(center, (x * 1.025, drum_height - 1.3, z * 1.025)),
                    (0.7, 0.16, 0.16),
                    "warm-light",
                    0.02,
                    angle,
                )
        self.box(
            identifier + " sealed maintenance door",
            add(center, (0, 1.6, radius + 0.05)),
            (2.5, 3.1, 0.18),
            "structure",
            0.1,
        )
        self.collider(
            identifier,
            add(center, (0, total_height / 2, 0)),
            (radius * 2, total_height, radius * 2),
        )

    def operations(self):
        self.group("07 Operations shell and working interior")
        building = self.layout["buildings"]["operations"]
        center, size = building["local"], building["size"]
        x, y, z = center
        width, height, depth = size
        self.box(
            "Operations load-bearing floor",
            add(center, (0, -0.22, 0)),
            (width, 0.44, depth),
            "concrete",
            0.06,
        )
        self.box(
            "Interior finished floor",
            add(center, (0, -0.018, 0)),
            (width - 0.8, 0.036, depth - 0.8),
            "paving",
            0,
        )
        for name, wall_center, wall_size in operations_walls(center, size):
            self.collider("operations-" + name, wall_center, wall_size)
            if name.startswith("south-"):
                self.box(
                    "Operations " + name + " sill",
                    (wall_center[0], y + 0.48, wall_center[2]),
                    (wall_size[0], 0.96, wall_size[2]),
                    "ceramic",
                    0.045,
                )
                self.box(
                    "Operations " + name + " clerestory",
                    (wall_center[0], y + 2.15, wall_center[2]),
                    (wall_size[0], 2.25, 0.12),
                    "glass",
                    0.02,
                )
                self.box(
                    "Operations " + name + " upper wall",
                    (wall_center[0], y + 5.65, wall_center[2]),
                    (wall_size[0], 4.7, wall_size[2]),
                    "ceramic",
                    0.045,
                )
                for offset in (-0.3, 0, 0.3):
                    self.box(
                        "Operations glazing mullion",
                        (wall_center[0] + wall_size[0] * offset, y + 2.15, wall_center[2] + 0.04),
                        (0.085, 2.34, 0.2),
                        "metal",
                        0.015,
                    )
            else:
                self.box(
                    "Operations " + name,
                    wall_center,
                    wall_size,
                    "structure" if name.startswith("jamb") else "ceramic",
                    0.07 if name.startswith("jamb") else 0.045,
                )
        self.box("Open door head", (x, y + 3.15, z + depth / 2), (3, 0.3, 0.6), "structure", 0.05)
        for sign in (-1, 1):
            self.box(
                "Door parked in open position",
                (x + sign * 2.35, y + 1.5, z + depth / 2 + 0.27),
                (1.4, 2.95, 0.12),
                "metal",
                0.07,
            )
            self.box(
                "Door amber hazard strip",
                (x + sign * 1.48, y + 1.4, z + depth / 2 + 0.32),
                (0.1, 2.6, 0.018),
                "ochre",
                0.008,
            )
        self.box(
            "Operations roof structure",
            (x, y + 7.75, z),
            (width + 0.4, 0.5, depth + 0.4),
            "painted-alloy",
            0.16,
        )
        self.box(
            "Operations roof ceramic cap",
            (x, y + 8, z),
            (width - 0.2, 0.12, depth - 0.2),
            "roof-alloy",
            0.08,
        )
        self.collider("operations-roof", (x, y + 7.8, z), (width, 0.4, depth))
        self.box(
            "Entrance canopy", (x, y + 3.65, z + depth / 2 + 0.95), (5, 0.24, 2.3), "ceramic", 0.10
        )
        self.box(
            "Entrance light",
            (x, y + 3.51, z + depth / 2 + 0.8),
            (3.9, 0.035, 0.12),
            "warm-light",
            0.012,
        )
        self.text(
            "Operations wayfinding",
            "OPERATIONS",
            (x, y + 5.05, z + depth / 2 + 0.04),
            0.73,
            "structure",
        )
        self.text(
            "Station number", "COMMS 01", (x, y + 4.13, z + depth / 2 + 0.04), 0.3, "structure"
        )
        for dx in (-8.5, 8.5):
            self.box(
                "Front weather-protected service recess",
                (x + dx, y + 5.6, z + depth / 2 + 0.1),
                (1, 2.2, 0.16),
                "structure",
                0.08,
            )
            for dy in (5.1, 5.45, 5.8, 6.15):
                self.box(
                    "Recess louver",
                    (x + dx, y + dy, z + depth / 2 + 0.23),
                    (0.78, 0.065, 0.1),
                    "metal",
                    0.01,
                )
        self.roof_plant("Operations", center, width, depth, y + 8.06)
        for sign in (-1, 1):
            for panel_index in range(4):
                panel_z = z - 4.8 + panel_index * 3.2
                side_x = x + sign * (width / 2 - 0.10)
                self.box(
                    "Operations side service cassette gasket",
                    (side_x, y + 3.7, panel_z),
                    (0.19, 5.7, 2.86),
                    "rubber",
                    0.07,
                )
                self.box(
                    "Operations side alloy service cassette",
                    (side_x + sign * 0.04, y + 3.7, panel_z),
                    (0.16, 5.42, 2.6),
                    "ceramic-warm" if panel_index % 2 else "ceramic",
                    0.07,
                )
                self.box(
                    "Cassette flush fastener strip",
                    (side_x + sign * 0.13, y + 5.7, panel_z),
                    (0.05, 0.10, 2.22),
                    "metal",
                    0.008,
                )
            self.box(
                "Operations plinth dust rail",
                (x + sign * (width / 2 - 0.06), y + 0.27, z),
                (0.24, 0.45, depth - 0.35),
                "dust-coated",
                0.05,
            )
        for dx in (-5.8, 5.8):
            self.box(
                "Recessed entry access panel gasket",
                (x + dx, y + 5.5, z + depth / 2 + 0.015),
                (3.6, 2.85, 0.05),
                "rubber",
                0.09,
            )
            self.box(
                "Recessed entry access panel",
                (x + dx, y + 5.5, z + depth / 2 + 0.045),
                (3.34, 2.58, 0.04),
                "ceramic-warm",
                0.07,
            )
        for sign in (-1, 1):
            self.box(
                "Airlock frame outer reveal",
                (x + sign * 1.73, y + 1.6, z + depth / 2 + 0.32),
                (0.18, 3.2, 0.30),
                "ceramic-warm",
                0.05,
            )
            self.box(
                "Airlock jamb recessed gasket",
                (x + sign * 1.34, y + 1.46, z + depth / 2 + 0.33),
                (0.18, 2.92, 0.035),
                "rubber",
                0.02,
            )
        # Functional desk is north of the canonical player/agent anchor, never on it.
        desk = (x, y + 0.87, z - 2.9)
        self.box("Communications desk worktop", desk, (5.4, 0.15, 1.5), "ceramic-warm", 0.12)
        for dx in (-2.1, 2.1):
            self.box(
                "Desk pedestal", (x + dx, y + 0.42, z - 2.9), (0.6, 0.84, 1.1), "structure", 0.1
            )
        self.box(
            "Communications workstation display housing",
            (x, y + 1.65, z - 3.34),
            (4.9, 1.45, 0.20),
            "structure",
            0.12,
        )
        self.box(
            "Blank authoritative display surface",
            (x, y + 1.65, z - 3.22),
            (4.5, 1.15, 0.035),
            "screen",
            0.03,
        )
        self.box(
            "Desk keyboard inset", (x, y + 0.96, z - 2.60), (1.9, 0.045, 0.45), "rubber", 0.035
        )
        self.collider("console-desk", (x, y + 0.85, z - 2.9), (5.4, 1.7, 1.5))
        for dx in (-7.3, 7.3):
            self.box(
                "Interior equipment rack",
                (x + dx, y + 1.35, z - 4.8),
                (1.8, 2.7, 1.3),
                "structure",
                0.12,
            )
            self.collider("equipment-rack-" + str(dx), (x + dx, y + 1.35, z - 4.8), (1.8, 2.7, 1.3))
            for level in range(5):
                self.box(
                    "Rack service drawer",
                    (x + dx, y + 0.35 + level * 0.48, z - 4.12),
                    (1.55, 0.37, 0.07),
                    "metal",
                    0.025,
                )
        for dx in (-5, 5):
            self.box(
                "Interior ceiling light",
                (x + dx, y + 7.48, z),
                (0.45, 0.05, 8),
                "warm-light",
                0.015,
            )
        self.anchor(
            "communications-console",
            center,
            kind="station",
            station_id="communications-console",
            backend_authoritative=True,
        )
        self.anchor(
            "console-display",
            (x, y + 1.65, z - 3.19),
            kind="display",
            station_id="communications-console",
        )
        self.anchor(
            "operations-door",
            (x, y, z + depth / 2),
            kind="door",
            clear_width=2.4,
            clear_height=3.0,
            state="open",
        )
        self.anchor(
            "operations-brand-slot",
            (x + 5.8, y + 5.7, z + depth / 2 + 0.2),
            kind="branding-slot",
            verified_source=False,
        )

    def service(self):
        self.group("08 Power and service structures")
        center = self.layout["buildings"]["service-east"]["local"]
        x, _, z = center
        self.box("Service tower foundation", (x, 0.15, z), (12, 0.3, 12), "concrete", 0.15)
        self.box("Service tower shell", (x, 5.7, z), (11, 11.4, 11), "ceramic-warm", 0.45)
        self.box("Service tower roof", (x, 11.6, z), (11.5, 0.4, 11.5), "roof-alloy", 0.2)
        self.roof_plant("Service tower", center, 11.5, 11.5, 11.82)
        for sign in (-1, 1):
            self.box(
                "Service tower vertical spine",
                (x + sign * 4.6, 5.8, z + 5.55),
                (0.65, 10.8, 0.35),
                "structure",
                0.1,
            )
            for height in (3.0, 6.0, 9.0):
                self.box(
                    "Service tower service panel",
                    (x + sign * 2.1, height, z + 5.55),
                    (2.1, 2.1, 0.22),
                    "ceramic",
                    0.12,
                )
                self.box(
                    "Service panel inspection slit",
                    (x + sign * 2.1, height + 0.4, z + 5.68),
                    (0.6, 0.12, 0.06),
                    "warm-light",
                    0.01,
                )
        self.collider("service-east", (x, 6, z), (12, 12, 12))
        for index, (px, pz) in enumerate([(3, -24), (14, -24), (13, 20)]):
            self.box(
                f"Service module {index} plinth", (px, 0.15, pz), (8, 0.3, 7), "concrete", 0.12
            )
            self.box(
                f"Service module {index} shell",
                (px, 2.4, pz),
                (7.4, 4.5, 6.4),
                "ceramic-warm",
                0.32,
            )
            self.box(
                f"Service module {index} roof", (px, 4.8, pz), (7.7, 0.35, 6.7), "roof-alloy", 0.12
            )
            self.box(
                f"Service module {index} sealed door",
                (px, 1.6, pz + 3.25),
                (2.1, 3.1, 0.15),
                "structure",
                0.1,
            )
            self.box(
                f"Service module {index} vent",
                (px + 2.4, 3, pz + 3.3),
                (1.2, 1.2, 0.2),
                "metal",
                0.04,
            )
            self.roof_plant(f"Service module {index}", (px, 0, pz), 7.7, 6.7, 4.98)
            for sign in (-1, 1):
                self.box(
                    "Module insulated corner reveal",
                    (px + sign * 3.52, 2.5, pz + 3.08),
                    (0.24, 4.1, 0.28),
                    "painted-alloy",
                    0.06,
                )
                self.box(
                    "Module lower dust flashing",
                    (px + sign * 3.68, 0.6, pz),
                    (0.16, 0.48, 6.25),
                    "dust-coated",
                    0.03,
                )
            for offset in (-0.72, -0.36, 0, 0.36, 0.72):
                self.box(
                    "Module vent horizontal blade",
                    (px + 2.4, 3 + offset * 0.6, pz + 3.42),
                    (1.06, 0.07, 0.16),
                    "painted-alloy",
                    0.02,
                )
            self.collider(f"service-module-{index}", (px, 2.5, pz), (8, 5, 7))
        for x, z in [(-7, -25), (23, -9), (18, 23)]:
            self.cylinder("Auxiliary mast footing", (x, 0.4, z), 0.85, 0.8, "concrete", vertices=16)
            self.cylinder(
                "Auxiliary aerial mast", (x, 6.1, z), 0.17, 11.4, "metal", top=0.07, vertices=12
            )
            for height in (3, 7.5):
                self.box(
                    "Auxiliary aerial panel",
                    (x + 0.26, height, z),
                    (0.20, 1.65, 0.36),
                    "ceramic",
                    0.035,
                )
            self.cylinder("Auxiliary blue beacon", (x, 11.9, z), 0.13, 0.25, "blue", vertices=16)
        # Deliberate utility runs along the north service corridor, behind doors.
        for index in range(3):
            self.tube(
                "North utility distribution pipe",
                [
                    (-10, 0.6 + index * 0.28, -18),
                    (-10, 0.6 + index * 0.28, -19.5),
                    (23, 0.6 + index * 0.28, -19.5),
                    (25, 0.6 + index * 0.28, -16),
                ],
                0.09,
                "metal",
                resolution=2,
            )
        for x in (-8, -2, 4, 10, 16, 22):
            self.box("Utility support saddle", (x, 0.42, -19.5), (0.22, 0.84, 1), "structure", 0.04)

    def signage(self):
        self.group("09 Wayfinding and blank branding slots")
        self.box("Outpost name sign structure", (27, 2.0, 35), (25, 3.5, 0.45), "structure", 0.18)
        self.text(
            "Outpost identification", "COMMUNICATIONS OUTPOST", (27, 2.25, 35.26), 0.95, "ceramic"
        )
        self.text(
            "District wayfinding",
            "EASTERN DISTRICT  /  OPERATIONS",
            (27, 1.25, 35.26),
            0.36,
            "ceramic-warm",
        )
        for x in (17, 37):
            self.box("Name sign stanchion", (x, 0.8, 35), (0.6, 1.6, 0.7), "metal", 0.06)
        self.collider("district-sign", (27, 2, 35), (25, 3.5, 0.5))
        self.box("Reserved brand plate", (10, 1.6, 35), (3, 2.8, 0.3), "ceramic-warm", 0.12)
        self.anchor(
            "district-brand-slot", (10, 1.6, 35.2), kind="branding-slot", verified_source=False
        )

    def export_batches(self):
        bpy = self.bpy
        depsgraph = bpy.context.evaluated_depsgraph_get()
        batches = defaultdict(
            lambda: {"vertices": [], "faces": [], "smooth": [], "uvs": [], "colors": []}
        )
        source_count = 0
        for obj in list(self.source.all_objects):
            if obj.type not in {"MESH", "CURVE", "FONT"}:
                continue
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            if mesh is None:
                continue
            source_count += 1
            try:
                matrix = obj.matrix_world
                normal_matrix = matrix.to_3x3().inverted().transposed()
                transformed = [tuple(matrix @ vertex.co) for vertex in mesh.vertices]
                # Split by material with compact vertex remapping per source object.
                for material_index in {polygon.material_index for polygon in mesh.polygons}:
                    material = (
                        mesh.materials[material_index]
                        if len(mesh.materials) > material_index
                        else obj.data.materials[0]
                    )
                    batch = batches[material.name]
                    family = self.material_families.get(material.name)
                    tile_metres = {"paint": 1.0, "metal": 0.8, "mineral": 1.8, "road": 1.5}.get(
                        family, 1
                    )
                    base = material.diffuse_color[:3]
                    part_tint = 0.96 + 0.07 * (
                        math.sin(sum(map(ord, obj.name)) * 0.713) * 0.5 + 0.5
                    )
                    remap = {}
                    for polygon in mesh.polygons:
                        if polygon.material_index != material_index:
                            continue
                        face = []
                        uvs, colors = [], []
                        normal = normal_matrix @ polygon.normal
                        dominant = max(range(3), key=lambda axis: abs(normal[axis]))
                        axes = ((1, 2), (0, 2), (0, 1))[dominant]
                        for old in polygon.vertices:
                            if old not in remap:
                                remap[old] = len(batch["vertices"])
                                batch["vertices"].append(transformed[old])
                            face.append(remap[old])
                            point = transformed[old]
                            uvs.append((point[axes[0]] / tile_metres, point[axes[1]] / tile_metres))
                            dust = 0
                            if family in {"paint", "metal"}:
                                dust = min(0.16, max(0, 1.2 - point[2]) * 0.085)
                                if normal.z > 0.7:
                                    dust += 0.035
                            fine = 1 + 0.023 * math.sin(
                                point[0] * 2.7 + point[1] * 3.1 + point[2] * 0.51
                            )
                            tint = [
                                min(
                                    1,
                                    max(
                                        0,
                                        base[i] * part_tint * fine * (1 - dust)
                                        + (0.34, 0.22, 0.13)[i] * dust,
                                    ),
                                )
                                for i in range(3)
                            ]
                            colors.append((*tint, 1))
                        batch["faces"].append(face)
                        batch["smooth"].append(polygon.use_smooth)
                        batch["uvs"].append(uvs)
                        batch["colors"].append(colors)
            finally:
                evaluated.to_mesh_clear()
        triangles = 0
        for name, batch in sorted(batches.items()):
            mesh = bpy.data.meshes.new("Batched " + name)
            mesh.from_pydata(batch["vertices"], [], batch["faces"])
            mesh.materials.append(self.materials[name])
            mesh.uv_layers.new(name="Surface metres")
            if name in self.material_families:
                mesh.color_attributes.new(name="Surface tint", type="FLOAT_COLOR", domain="CORNER")
            # Creating another custom-data layer invalidates retained Blender RNA
            # handles. Reacquire both after allocation or UV writes corrupt colors.
            uv = mesh.uv_layers["Surface metres"]
            color = mesh.color_attributes.get("Surface tint")
            for polygon, smooth, uvs, colors in zip(
                mesh.polygons, batch["smooth"], batch["uvs"], batch["colors"], strict=True
            ):
                polygon.use_smooth = smooth
                for index, loop in enumerate(polygon.loop_indices):
                    uv.data[loop].uv = uvs[index]
                    if color is not None:
                        color.data[loop].color = colors[index]
            for polygon, uvs in zip(mesh.polygons, batch["uvs"], strict=True):
                for index, loop in enumerate(polygon.loop_indices):
                    actual = mesh.uv_layers["Surface metres"].data[loop].uv
                    if any(abs(actual[axis] - uvs[index][axis]) > 0.0001 for axis in range(2)):
                        raise ValueError(f"UV data was overwritten in {name}")
            if color is not None:
                for value in mesh.color_attributes["Surface tint"].data:
                    if not all(math.isfinite(c) and 0 <= c <= 1 for c in value.color):
                        raise ValueError(f"linear color data was overwritten in {name}")
            mesh.update()
            mesh.calc_loop_triangles()
            triangles += len(mesh.loop_triangles)
            obj = bpy.data.objects.new("Outpost / " + name, mesh)
            self.export.objects.link(obj)
            obj["asset_id"], obj["material_batch"] = ASSET_ID, name
            obj["role"] = "visual"
        for record in [*self.colliders, *self.anchors]:
            obj = bpy.data.objects.new(record["id"], None)
            obj.empty_display_type = "CUBE" if "shape" in record else "ARROWS"
            obj.empty_display_size = 0.35
            obj.location = blender_point(
                record.get("center", record.get("position", record.get("start", (0, 0, 0))))
            )
            obj["mars_kind"] = "collider" if "shape" in record else "anchor"
            obj["mars_contract"] = json.dumps(record, sort_keys=True)
            self.export.objects.link(obj)
        self.source.hide_viewport = True
        self.source.hide_render = True
        return {
            "source_objects": source_count,
            "export_meshes": len(batches),
            "triangles": triangles,
        }

    def build(self):
        self.terrain()
        self.paths()
        self.bridge()
        self.tower()
        self.reflector("dish-west", 10.1, 2.5, 14.8, (-0.45, 0.75, 0.50))
        self.reflector("dish-east", 12.0, 3.1, 18.2, (0.50, 0.80, 0.25))
        self.radome("radome-north", 8.5, 17)
        self.radome("radome-south", 10.2, 18)
        self.operations()
        self.service()
        self.signage()
        return self.export_batches()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--definition", type=Path, default=ROOT / "jarvis/society/mars/definition.json"
    )
    parser.add_argument("--study", type=Path, default=STUDY)
    parser.add_argument("--validate-export", type=Path)
    parser.add_argument("--sanitize-source", action="store_true")
    parser.add_argument("--validate-source", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
    if args.validate_export is not None:
        print(
            json.dumps(
                validate_glb_material_attributes(args.validate_export.read_bytes()), indent=2
            )
        )
        return
    study = args.study.resolve()
    if not study.is_relative_to((ROOT / "art/studies").resolve()):
        raise ValueError("authored sources must stay in this repository's isolated studies")
    manifest_path = study / "study.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["approval"]["status"] != "pending" and not args.validate_source:
        raise ValueError("do not overwrite an approved reference with an unreviewed rebuild")
    if args.sanitize_source or args.validate_source:
        import bpy

        source_path = study / "source/communications-outpost.blend"
        bpy.ops.wm.open_mainfile(filepath=str(source_path), load_ui=True, use_scripts=False)
        if args.sanitize_source:
            metadata = sanitize_source_metadata(bpy)
            bpy.context.preferences.filepaths.save_version = 0
            bpy.ops.wm.save_as_mainfile(
                filepath=str(source_path),
                check_existing=False,
                relative_remap=False,
                compress=False,
            )
            contract_path = study / "source/geometry-contract.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract.update(
                source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                source_bytes=source_path.stat().st_size,
                source_metadata_validation=metadata,
            )
            contract_path.write_text(
                json.dumps(contract, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
        else:
            metadata = validate_source_path_records(source_path_records(bpy))
        triangles, meshes = 0, 0
        for obj in bpy.data.collections["Export"].all_objects:
            if obj.type == "MESH":
                obj.data.calc_loop_triangles()
                triangles += len(obj.data.loop_triangles)
                meshes += 1
        print(json.dumps({**metadata, "export_meshes": meshes, "triangles": triangles}, indent=2))
        return
    layout = canonical_layout(args.definition)
    author = Author(layout, study)
    report = author.build()
    if report["triangles"] > TRIANGLE_TARGET:
        raise ValueError(f"reference triangle target exceeded: {report['triangles']}")
    source_path = study / "source/communications-outpost.blend"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    author.bpy.context.scene.unit_settings.system = "METRIC"
    author.bpy.context.scene.unit_settings.scale_length = 1
    author.bpy.context.scene["runtime_origin"] = list(layout["origin"])
    author.bpy.context.scene["layout_version"] = layout["layout_version"]
    author.bpy.context.scene["seed"] = layout["seed"]
    author.bpy.context.preferences.filepaths.save_version = 0
    source_metadata = sanitize_source_metadata(author.bpy)
    author.bpy.ops.wm.save_as_mainfile(
        filepath=str(source_path), check_existing=False, relative_remap=False, compress=False
    )
    report.update(
        {
            "asset_id": ASSET_ID,
            "stage": "authored-reference-unreviewed",
            "blender_version": author.bpy.app.version_string,
            "seed": layout["seed"],
            "layout_version": layout["layout_version"],
            "definition_sha256": layout["definition_sha256"],
            "world_translation": layout["origin"],
            "source_axes": "Blender Z-up; runtime (x,y,z) maps to Blender (x,-z,y)",
            "export_axes": "Runtime Y-up via glTF exporter export_yup=True",
            "triangle_target": TRIANGLE_TARGET,
            "source_bytes": source_path.stat().st_size,
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "source_metadata_validation": source_metadata,
            "colliders": author.colliders,
            "anchors": author.anchors,
            "runtime_evidence": [],
            "limitations": [
                "Runtime appearance, movement and collision integration are not yet verified.",
                "Source includes no character, rover, approved logo or fabricated task activity.",
                "No visual approval or production-catalog promotion is granted.",
                "Material batching reduces object selection granularity; "
                "stable anchors remain separate.",
                "Editable source rebuilds may vary in Blender metadata; "
                "byte identity is not promised.",
            ],
        }
    )
    (study / "source/geometry-contract.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    asset = {
        "id": ASSET_ID,
        "kind": "building",
        "source": "source/communications-outpost.blend",
        "collection": "Export",
        "export": "exports/communications-outpost.glb",
    }
    manifest["assets"] = [item for item in manifest["assets"] if item["id"] != ASSET_ID] + [asset]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in [
                    "asset_id",
                    "source_objects",
                    "export_meshes",
                    "triangles",
                    "source_bytes",
                    "stage",
                ]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
