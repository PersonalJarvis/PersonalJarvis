"""The new canonical world has stable, portable and navigable placement data."""

from __future__ import annotations

import json
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
