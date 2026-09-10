"""Terrain blockout checks, separate from runtime or visual acceptance."""

from __future__ import annotations

import copy
import importlib.util
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("mars_map", ROOT / "scripts/art/mars_map.py")
assert SPEC and SPEC.loader
mars_map = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mars_map)


@pytest.fixture
def layout() -> dict:
    nodes = [
        ("command", [-72, 12, -20], 22),
        ("junction", [-15, 10, 8], 4),
        ("terminal", [20, 8, 70], 24),
        ("habitat", [-100, 6, 100], 32),
        ("research", [-175, 18, -70], 22),
        ("bridge-west", [20, 12, -50], 5),
        ("bridge-east", [100, 12, -50], 5),
        ("workshop", [145, 6, 95], 24),
        ("ridge-turn-a", [170, 16, -80], 4),
        ("ridge-turn-b", [110, 20, -140], 4),
        ("comms", [190, 26, -180], 18),
        ("landing", [255, 2, 185], 34),
    ]
    links = [
        ("command", "junction"),
        ("junction", "terminal"),
        ("junction", "habitat"),
        ("command", "research"),
        ("junction", "bridge-west"),
        ("bridge-west", "bridge-east"),
        ("bridge-east", "workshop"),
        ("bridge-east", "ridge-turn-a"),
        ("ridge-turn-a", "ridge-turn-b"),
        ("ridge-turn-b", "comms"),
        ("workshop", "landing"),
    ]
    return {
        "schema": 1,
        "units": "metres",
        "bounds": {"minX": -320, "maxX": 330, "minZ": -245, "maxZ": 245},
        "nodes": [{"id": name, "position": p, "radius": radius} for name, p, radius in nodes],
        "links": [
            {
                "id": f"{a}/{b}",
                "a": a,
                "b": b,
                "kind": "bridge" if a == "bridge-west" else "ground",
                "width": 6,
            }
            for a, b in links
        ],
    }


def test_compact_base_graph_is_connected_with_valid_pad_edge_ramps(layout: dict) -> None:
    report = mars_map.validate_layout(layout)
    assert report["connected_nodes"] == report["node_count"] == 12
    assert report["link_count"] == 11
    assert 0.08 < report["max_grade"] <= 0.1
    assert report["span"] == {"x": 430, "y": 24, "z": 365}
    assert report["bridge_length"] == 80
    assert report["terrain_issues"] == []
    assert "unverified" in report["scope"]


def test_json_loader_accepts_bom_and_preserves_extra_metadata(tmp_path: Path, layout: dict) -> None:
    layout["authoring_note"] = "Blockout only"
    path = tmp_path / "layout.json"
    path.write_text(json.dumps(layout), encoding="utf-8-sig")
    assert mars_map.load_layout(path) == layout


def test_disconnected_graph_is_rejected(layout: dict) -> None:
    layout["links"] = [link for link in layout["links"] if link["kind"] != "bridge"]
    with pytest.raises(ValueError, match="disconnected"):
        mars_map.validate_layout(layout)


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan, True, "12"])
def test_invalid_coordinates_are_rejected(layout: dict, bad: object) -> None:
    layout["nodes"][0]["position"][1] = bad
    with pytest.raises(ValueError, match="finite number"):
        mars_map.validate_layout(layout)


def test_duplicate_node_and_link_ids_are_rejected(layout: dict) -> None:
    invalid = copy.deepcopy(layout)
    invalid["nodes"].append(copy.deepcopy(invalid["nodes"][0]))
    with pytest.raises(ValueError, match="duplicate node"):
        mars_map.validate_layout(invalid)
    layout["links"].append(copy.deepcopy(layout["links"][0]))
    with pytest.raises(ValueError, match="duplicate link"):
        mars_map.validate_layout(layout)


def test_missing_endpoint_is_rejected(layout: dict) -> None:
    layout["links"][0]["a"] = "missing"
    with pytest.raises(ValueError, match="unknown endpoint"):
        mars_map.validate_layout(layout)


def test_out_of_bounds_node_is_rejected(layout: dict) -> None:
    layout["nodes"][0]["position"][0] = -321
    with pytest.raises(ValueError, match="outside bounds"):
        mars_map.validate_layout(layout)


@pytest.mark.parametrize("bad", [0, -1, math.inf])
def test_invalid_route_width_is_rejected(layout: dict, bad: float) -> None:
    layout["links"][0]["width"] = bad
    with pytest.raises(ValueError, match="width"):
        mars_map.validate_layout(layout)


def test_zero_length_route_is_rejected(layout: dict) -> None:
    layout["links"][0]["b"] = layout["links"][0]["a"]
    with pytest.raises(ValueError, match="positive horizontal length"):
        mars_map.validate_layout(layout)


def test_grade_is_measured_between_flat_pad_edges(layout: dict) -> None:
    research = next(node for node in layout["nodes"] if node["id"] == "research")
    research["position"][1] = 20
    # Centre-to-centre grade is only 7%; the remaining ramp exceeds 10%.
    with pytest.raises(ValueError, match="maximum grade"):
        mars_map.validate_layout(layout)


def test_pad_centres_and_interiors_keep_their_exact_design_elevation(layout: dict) -> None:
    for node in layout["nodes"]:
        x, y, z = node["position"]
        assert mars_map.height_at(layout, x, z) == y
        for dx, dz in [(0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3)]:
            assert mars_map.height_at(layout, x + dx * node["radius"], z + dz * node["radius"]) == y


def test_ground_routes_stay_level_on_pads_and_ramp_between_their_edges(layout: dict) -> None:
    nodes = {node["id"]: node for node in layout["nodes"]}
    for link in layout["links"]:
        if link["kind"] != "ground":
            continue
        a, b = nodes[link["a"]], nodes[link["b"]]
        ax, ay, az = a["position"]
        bx, by, bz = b["position"]
        length = math.hypot(bx - ax, bz - az)
        for fraction in [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]:
            progress = max(
                0, min(1, (fraction * length - a["radius"]) / (length - a["radius"] - b["radius"]))
            )
            expected = ay + (by - ay) * progress
            actual = mars_map.height_at(
                layout, ax + (bx - ax) * fraction, az + (bz - az) * fraction
            )
            assert actual == pytest.approx(expected, abs=1e-8), link["id"]


def test_bridge_keeps_the_ravine_open_instead_of_creating_an_earth_dam(layout: dict) -> None:
    assert mars_map.height_at(layout, 60, -50) < -10
    filled = copy.deepcopy(layout)
    bridge = next(link for link in filled["links"] if link["kind"] == "bridge")
    bridge["kind"] = "ground"
    assert mars_map.height_at(filled, 60, -50) == 12


def test_terrain_is_deterministic_and_has_relief_outside_the_base(layout: dict) -> None:
    points = [(-310, -220), (310, -220), (-300, 220), (310, 220), (60, 0)]
    heights = [mars_map.height_at(layout, x, z) for x, z in points]
    reverse_heights = [mars_map.height_at(layout, x, z) for x, z in reversed(points)]
    assert heights == list(reversed(reverse_heights))
    assert max(heights) - min(heights) > 40


def test_pad_conflicts_are_reported_without_claiming_the_path_is_covered(layout: dict) -> None:
    layout["nodes"] = [
        {"id": "a", "position": [-60, 0, 0], "radius": 2},
        {"id": "b", "position": [60, 0, 0], "radius": 2},
        {"id": "raised-pad", "position": [0, 5, 0], "radius": 3},
    ]
    layout["links"] = [
        {"id": "through-pad", "a": "a", "b": "b", "kind": "ground", "width": 6},
        {"id": "pad-approach", "a": "b", "b": "raised-pad", "kind": "ground", "width": 6},
    ]
    report = mars_map.validate_layout(layout)
    assert mars_map.height_at(layout, 0, 0) == 5
    issue = next(issue for issue in report["terrain_issues"] if issue["link"] == "through-pad")
    assert issue["kind"] == "uncovered_ground_path"
    assert issue["max_height_error"] == 5
