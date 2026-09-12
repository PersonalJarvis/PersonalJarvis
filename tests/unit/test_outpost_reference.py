"""Geometry and navigation contracts for the authored Outpost source recipe."""

from __future__ import annotations

import importlib.util
import json
import math
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "outpost_reference", ROOT / "scripts/art/build_outpost_reference.py"
)
assert SPEC and SPEC.loader
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)


def signed_volume(vertices, faces):
    total = 0
    for face in faces:
        origin = vertices[face[0]]
        for index in range(1, len(face) - 1):
            a, b = vertices[face[index]], vertices[face[index + 1]]
            cross = recipe.cross(a, b)
            total += sum(origin[axis] * cross[axis] for axis in range(3)) / 6
    return total


def inside_box(point, center, size):
    return all(abs(point[axis] - center[axis]) < size[axis] / 2 for axis in range(3))


def test_recipe_imports_without_blender_and_preserves_runtime_handedness():
    assert recipe.blender_point((0, 1, 0)) == (0, 0, 1)
    assert recipe.blender_point((0, 0, 1)) == (0, -1, 0)
    assert recipe.cross(
        recipe.blender_point((1, 0, 0)), recipe.blender_point((0, 1, 0))
    ) == recipe.blender_point((0, 0, 1))


def test_shared_definition_drives_outpost_station_and_bridge_locations():
    layout = recipe.canonical_layout(ROOT / "jarvis/society/mars/definition.json")
    assert layout["origin"] == [320, 58, 50]
    assert layout["buildings"]["operations"]["local"] == (-26, 0, 14)
    assert layout["nodes"]["console"] == layout["buildings"]["operations"]["local"]
    assert layout["nodes"]["hub-east"] == (-195, -10, -10)
    assert layout["nodes"]["outpost-west"] == (-64, 0, 0)


def test_incompatible_shared_axes_are_rejected(tmp_path):
    data = json.loads((ROOT / "jarvis/society/mars/definition.json").read_text())
    data["axes"]["up"] = "+Z"
    path = tmp_path / "definition.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="metre/Y-up"):
        recipe.canonical_layout(path)


def test_terrain_is_repeatable_closed_and_outward_with_real_depth():
    vertices, faces = recipe.plateau_mesh(6901)
    assert (vertices, faces) == recipe.plateau_mesh(6901)
    assert vertices != recipe.plateau_mesh(6902)[0]
    assert recipe.closed_edges(faces)
    assert signed_volume(vertices, faces) > 100_000
    assert min(point[1] for point in vertices) < -60
    assert max(point[1] for point in vertices) == pytest.approx(-0.12)
    assert all(math.isfinite(component) for point in vertices for component in point)


@pytest.mark.parametrize("radius,depth", [(10.1, 2.5), (12.0, 3.1)])
def test_parabolic_dishes_have_front_back_and_closed_rim(radius, depth):
    vertices, faces = recipe.dish_mesh(radius, depth)
    assert recipe.closed_edges(faces)
    assert signed_volume(vertices, faces) > 0
    assert min(point[1] for point in vertices) == pytest.approx(-0.1)
    assert max(point[1] for point in vertices) == pytest.approx(depth)


def test_graded_bridge_top_matches_both_navigation_endpoints():
    start, end = (-195, -10, -10), (-64, 0, 0)
    vertices, faces = recipe.deck_mesh(start, end)
    assert recipe.closed_edges(faces)
    assert signed_volume(vertices, faces) > 0
    assert recipe.lerp(vertices[0], vertices[3], 0.5) == start
    assert recipe.lerp(vertices[1], vertices[2], 0.5) == end
    assert recipe.length(
        tuple(vertices[0][axis] - vertices[3][axis] for axis in range(3))
    ) == pytest.approx(10)
    normal = recipe.cross(
        tuple(vertices[1][axis] - vertices[0][axis] for axis in range(3)),
        tuple(vertices[2][axis] - vertices[0][axis] for axis in range(3)),
    )
    assert normal[1] > 0


def test_operations_door_has_true_clear_width_and_headroom():
    walls = recipe.operations_walls()
    # This journey crosses the advertised south doorway into the station room.
    for x in (-27.19, -26, -24.81):
        for y in (0.01, 1.75, 2.99):
            for z in (21.25, 21, 20.75, 19, 16, 14):
                assert not any(inside_box((x, y, z), center, size) for _, center, size in walls)
    assert any(inside_box((-27.35, 1.5, 21), center, size) for _, center, size in walls)
    assert any(inside_box((-26, 3.01, 20.81), center, size) for _, center, size in walls)


def test_private_concepts_are_not_authored_geometry_dependencies():
    source = (ROOT / "scripts/art/build_outpost_reference.py").read_text(encoding="utf-8")
    assert "references/private" not in source
    assert "Downloads" not in source
    assert "image.open" not in source.lower()


def test_shared_route_endpoints_export_one_anchor_and_reject_identity_conflicts():
    author = recipe.Author.__new__(recipe.Author)
    author.anchors = []
    author.anchor("outpost-arrival", (-52, 0, 18), kind="navigation")
    author.anchor("outpost-arrival", (-52, 0, 18), kind="navigation")
    assert len(author.anchors) == 1
    with pytest.raises(ValueError, match="conflicting definitions"):
        author.anchor("outpost-arrival", (-53, 0, 18), kind="navigation")


def test_revised_tower_uses_separate_equipment_sections_inside_collision_envelope():
    sections = recipe.tower_sections()
    assert sections[0]["bottom"] == 5.8
    assert all(section["width"] < 14 and section["depth"] < 14 for section in sections)
    assert all(section["top"] > section["bottom"] for section in sections)
    assert all(a["top"] == b["bottom"] for a, b in zip(sections, sections[1:], strict=False))
    assert sections[-1]["top"] < 62


@pytest.mark.parametrize("family", ["paint", "metal", "mineral", "road"])
def test_authored_surface_maps_are_repeatable_and_have_valid_tangent_normals(family):
    heights, normals = recipe.surface_grain(family, 16)
    assert (heights, normals) == recipe.surface_grain(family, 16)
    assert len(heights) == 256 and len(normals) == 1024
    assert max(heights) - min(heights) > 0.1
    assert all(0 <= value <= 1 for value in normals)
    for offset in range(0, len(normals), 4):
        vector = [normals[offset + axis] * 2 - 1 for axis in range(3)]
        assert sum(value * value for value in vector) == pytest.approx(1)
        assert vector[2] > 0


def test_export_preserves_absolute_surface_tints_and_embeds_material_images():
    path = ROOT / "art/studies/mars-outpost-reference/exports/communications-outpost.glb"
    raw = path.read_bytes()
    json_bytes = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20 : 20 + json_bytes])
    mapped = 0
    for mesh in document["meshes"]:
        for primitive in mesh["primitives"]:
            material = document["materials"][primitive["material"]]
            if "COLOR_0" in primitive["attributes"]:
                # Surface tint contains absolute albedo, not a second darkening factor.
                factor = material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [1] * 4)
                assert factor == pytest.approx([1] * 4)
            if "normalTexture" in material:
                mapped += 1
                assert "TEXCOORD_0" in primitive["attributes"]
    assert mapped > 0
    assert document["images"]
    assert all("bufferView" in image and "uri" not in image for image in document["images"])
    actual = recipe.validate_glb_material_attributes(raw)
    assert actual["colored_primitives"] > 0
    assert 0 <= actual["linear_color_range"][0] <= actual["linear_color_range"][1] <= 1


def material_buffer_fixture(colors, *, byte_colors=False, normalized=False):
    """Padded, offset data makes incorrect accessor decoding fail visibly."""
    binary = b"PAD!" + struct.pack("<9f", *([0.0] * 9))
    encoded = b"PAD!"
    for color in colors:
        encoded += (
            struct.pack("<3B", *color) + b"\xff"
            if byte_colors
            else struct.pack("<4f", *color, 1234)
        )
    binary += encoded
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 4, "byteLength": 36},
            {
                "buffer": 0,
                "byteOffset": 40,
                "byteLength": len(encoded),
                "byteStride": 4 if byte_colors else 16,
            },
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {
                "bufferView": 1,
                "byteOffset": 4,
                "componentType": 5121 if byte_colors else 5126,
                "normalized": normalized,
                "count": 3,
                "type": "VEC3",
            },
        ],
        "meshes": [
            {"name": "regression", "primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1}}]}
        ],
    }
    payload = json.dumps(document).encode()
    payload += b" " * (-len(payload) % 4)
    return (
        struct.pack("<III", 0x46546C67, 2, 28 + len(payload) + len(binary))
        + struct.pack("<II", len(payload), 0x4E4F534A)
        + payload
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def test_numeric_validator_rejects_uv_coordinates_written_into_color_memory():
    # Reproduced from the three-vertex Blender 5.0.1 stale-UV-handle failure.
    raw = material_buffer_fixture([(0.2, 0.3, 21), (22, 32, 0.4), (0.2, 0.3, 0.4)])
    with pytest.raises(ValueError, match="COLOR_0 outside"):
        recipe.validate_glb_material_attributes(raw)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -244.272, 72.88])
def test_numeric_validator_rejects_nonfinite_and_out_of_range_color_components(bad):
    raw = material_buffer_fixture([(0.2, 0.3, bad), (0.2, 0.3, 0.4), (0.2, 0.3, 0.4)])
    with pytest.raises(ValueError, match="COLOR_0"):
        recipe.validate_glb_material_attributes(raw)


def test_numeric_validator_reads_offsets_strides_and_normalized_byte_colors():
    float_report = recipe.validate_glb_material_attributes(
        material_buffer_fixture([(0.2, 0.3, 0.4)] * 3)
    )
    assert float_report["linear_color_range"] == pytest.approx([0.2, 0.4])
    byte_report = recipe.validate_glb_material_attributes(
        material_buffer_fixture([(0, 128, 255)] * 3, byte_colors=True, normalized=True)
    )
    assert byte_report["linear_color_range"] == [0, 1]


def test_public_source_validation_checks_packed_snapshot_separately_from_image_path():
    records = [
        ("Image.filepath", "//materials/paint-normal.png"),
        ("ImagePackedFile.filepath", "C:/Users/private-fixture/paint-normal.png"),
    ]
    with pytest.raises(ValueError, match=r"ImagePackedFile.filepath is not a portable") as failure:
        recipe.validate_source_path_records(records)
    assert "private-fixture" not in str(failure.value)


@pytest.mark.parametrize("value", [b"C:/Users/private-fixture/", b"/home/private-fixture/", b"../"])
def test_public_source_validation_rejects_file_browser_profile_defaults(value):
    with pytest.raises(ValueError, match="FileSelectParams.dir"):
        recipe.validate_source_path_records([("FileSelectParams.dir", value)])


def test_public_source_validation_accepts_only_neutral_relative_field_contracts():
    assert recipe.validate_source_path_records(
        [
            ("Image.filepath", "//materials/paint-normal.png"),
            ("ImagePackedFile.filepath", "//materials/paint-normal.png"),
            ("FileSelectParams.dir", b"//"),
            ("RenderData.pic", "//renders/"),
        ]
    ) == {"validated_fields": 4}


def test_public_source_validation_handles_blender_native_relative_separators():
    assert recipe.validate_source_path_records(
        [
            ("Image.filepath", "//materials\\paint-normal.png"),
            ("RenderData.pic", "//renders\\"),
        ]
    ) == {"validated_fields": 2}
    with pytest.raises(ValueError, match="portable"):
        recipe.validate_source_path_records([("Image.filepath", "\\\\materials\\paint-normal.png")])
