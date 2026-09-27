"""The new canonical world has stable, portable and navigable placement data."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from jarvis.society.mars.definition import load_definition, validate_definition

ROOT = Path(__file__).resolve().parents[2]


def test_packaged_definition_matches_frontend_and_has_no_private_runtime_paths():
    definition = load_definition()
    projection = ROOT / "jarvis/ui/web/frontend/src/components/society/mars/worldDefinition.json"
    assert json.loads(projection.read_text(encoding="utf-8")) == definition
    encoded = json.dumps(definition)
    assert "Downloads" not in encoded and ".blend" not in encoded
    assert definition["stations"][0]["checkpoint"] == "hub:comms"
    assert definition["status"] == "blockout"
    assert all(row["stage"] == "blockout" for row in definition["districts"])


def test_reloads_do_not_share_mutable_state():
    original = load_definition()
    original["districts"].clear()
    assert len(load_definition()["districts"]) == 10


@pytest.mark.parametrize("kind", ["unknown", "disconnected", "narrow", "version", "infinite"])
def test_invalid_navigation_is_rejected(kind):
    definition = load_definition()
    routes = definition["navigation"]["edges"]
    if kind == "unknown":
        routes[0]["to"] = "missing"
    elif kind == "disconnected":
        definition["navigation"]["nodes"].append({"id": "orphan", "position": [0, 48, 0]})
    elif kind == "narrow":
        routes[0]["width"] = 2
    elif kind == "version":
        definition["navigation"]["version"] = 2
    else:
        definition["navigation"]["nodes"][0]["position"][0] = float("inf")
    with pytest.raises(ValueError):
        validate_definition(definition)


def test_bounds_contain_tallest_landmark_and_station_rejects_unknown_anchor():
    definition = load_definition()
    foundry = next(d for d in definition["districts"] if d["id"] == "foundry")
    assert definition["bounds"]["max"][1] >= foundry["center"][1] + foundry["landmark_height"]
    definition["stations"][0]["anchor"] = "unreachable-console"
    with pytest.raises(ValueError):
        validate_definition(definition)


def test_visit_targets_reuse_existing_nodes_and_have_no_task_authority():
    data = load_definition()
    destinations = data["navigation"]["destinations"]
    assert {
        row["id"]: row["anchor"] for row in destinations if not row["id"].startswith("rover-")
    } == {
        "outpost-approach": "console-approach",
        "outpost-bridge-staging": "outpost-west",
    }
    assert all(set(row) == {"id", "name", "anchor", "capacity"} for row in destinations)
    assert all(row["capacity"] == 1 for row in destinations)
    assert len(data["stations"]) == 1


def test_rover_docks_and_access_share_authored_collision_and_separate_bodies():
    from jarvis.society.mars.mobility_geometry import MobilityGeometry

    data = load_definition()
    nav = data["navigation"]
    geometry = MobilityGeometry(data)
    nodes = {row["id"]: row for row in nav["nodes"]}
    stations = {row["id"]: row["anchor"] for row in nav["destinations"]}
    a, b = nav["rover_docks"]
    assert geometry.path_clear(nodes[a["node_id"]]["position"], nodes[b["node_id"]]["position"])
    for dock in nav["rover_docks"]:
        assert dock["node_id"] not in stations.values()
        for station in [dock["boarding_station_id"], *dock["exit_station_ids"]]:
            assert geometry.capsule_clear(nodes[stations[station]]["position"])
    edges = {row["id"]: row for row in nav["edges"]}
    for label, end in (("a", "lead"), ("b", "tail")):
        crossing = f"corridor:route05-{end}"
        assert crossing in edges[f"route-05-{end}"]["resource_ids"]
        assert crossing in edges[f"rover-{label}-access-1"]["resource_ids"]
        assert crossing in edges[f"rover-{label}-access-4"]["resource_ids"]
        assert f"clearance:rover-{label}" in nodes[f"rover-{label}-dock"]["resource_ids"]
    assert "corridor:route05-lead" in edges["route-03"]["resource_ids"]
    assert "corridor:route05-lead" in edges["rover-a-access-5"]["resource_ids"]
    assert "corridor:route05-lead" in nodes["rover-a-alternate"]["resource_ids"]
    assert "corridor:route05-lead" in nodes["rover-a-alternate-shoulder"]["resource_ids"]
    assert edges["rover-b-access-2"]["to"] == "rover-b-exit"


def test_parked_rover_does_not_clip_the_existing_console_approach():
    from jarvis.society.mars.mobility_geometry import _rectangle, _segment_distance

    data = load_definition()
    nav = data["navigation"]
    nodes = {node["id"]: node["position"] for node in nav["nodes"]}
    dock = nav["rover_docks"][0]
    center = nodes[dock["node_id"]]
    footprint = _rectangle((center[0], center[2]), dock["yaw"], 3.4, 4.8)
    start, end = nodes["outpost-arrival"], nodes["console-approach"]
    for step in range(101):
        t = step / 100
        point = (start[0] + (end[0] - start[0]) * t, start[2] + (end[2] - start[2]) * t)
        assert (
            min(
                _segment_distance(point, p, q)
                for p, q in zip(footprint, footprint[1:] + footprint[:1], strict=True)
            )
            >= 0.6
        )


def test_rover_collision_is_reproducible_from_authored_source_and_roads_stay_unchanged():
    from scripts.art.project_mars_collision import project_collision

    data = load_definition()
    source = json.loads(
        (ROOT / "art/studies/mars-outpost-reference/source/geometry-contract.json").read_text(
            "utf-8"
        )
    )
    assert data["navigation"]["collision"] == project_collision(data, source)
    old = data["navigation"]["graph_migrations"][0]["source_descriptor"]["navigation"]
    assert data["navigation"]["surface_edges"] == old["edges"]
    assert len(data["navigation"]["surface_edges"]) == 25
    assert len(data["navigation"]["edges"]) == 37


@pytest.mark.parametrize(
    "failure",
    [
        "wall",
        "no_support",
        "same_exit",
        "same_position",
        "bumper",
        "missing",
        "capacity",
        "nan",
        "far",
    ],
)
def test_invalid_rover_dock_is_not_published(failure):
    data = load_definition()
    nav = data["navigation"]
    dock = nav["rover_docks"][0]
    node = next(row for row in nav["nodes"] if row["id"] == "rover-a-board")
    if failure == "wall":
        point = node["position"]
        nav["collision"]["solid_boxes"].append(
            {
                "id": "blocked",
                "min": [point[0] - 1, 58, point[2] - 1],
                "max": [point[0] + 1, 61, point[2] + 1],
            }
        )
    elif failure == "no_support":
        for surface in nav["collision"]["support_surfaces"]:
            surface["plane"][2] -= 2
    elif failure == "same_exit":
        dock["exit_station_ids"][1] = dock["exit_station_ids"][0]
    elif failure == "same_position":
        primary = next(row for row in nav["nodes"] if row["id"] == "rover-a-exit")
        alternate = next(row for row in nav["nodes"] if row["id"] == "rover-a-alternate")
        alternate["position"] = primary["position"].copy()
    elif failure == "bumper":
        center = next(row for row in nav["nodes"] if row["id"] == dock["node_id"])["position"]
        node["position"] = [
            center[0] + 2.4 * math.sin(dock["yaw"]),
            center[1],
            center[2] + 2.4 * math.cos(dock["yaw"]),
        ]
    elif failure == "missing":
        dock["boarding_station_id"] = "missing"
    elif failure == "capacity":
        nav["rovers"][0]["seat_capacity"] = True
    elif failure == "nan":
        nav["rovers"][0]["width_m"] = float("nan")
    else:
        node["position"][0] -= 5
    with pytest.raises(ValueError):
        validate_definition(data)


@pytest.mark.parametrize(
    "patch",
    [
        {"capacity": 2},
        {"capacity": True},
        {"anchor": "missing"},
        {"capability": "send-email"},
        {"name": ""},
        {"id": "communications-console"},
    ],
)
def test_invalid_visit_target_cannot_create_a_task_station(patch):
    data = load_definition()
    data["navigation"]["destinations"][0].update(patch)
    with pytest.raises(ValueError):
        validate_definition(data)
