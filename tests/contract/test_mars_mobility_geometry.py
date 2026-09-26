"""Authored support and solid geometry, with no renderer or native dependency."""

from __future__ import annotations

import copy
import math

import pytest

from jarvis.society.mars.mobility_geometry import MobilityGeometry


def definition():
    return {
        "navigation": {
            "collision": {
                "version": 1,
                "source_sha256": "a" * 64,
                "solid_boxes": [{"id": "wall", "min": [4, 0, -2], "max": [5, 4, 2]}],
                "support_surfaces": [
                    {
                        "id": "deck",
                        "polygon": [[-20, -20], [20, -20], [20, 20], [-20, 20]],
                        "plane": [0, 0, 0],
                    }
                ],
            }
        }
    }


def test_capsule_cannot_exit_into_wall_or_over_edge():
    geometry = MobilityGeometry(definition())
    assert geometry.capsule_clear([0, 0, 0])
    assert geometry.capsule_clear([3.3, 0, 0])
    assert not geometry.capsule_clear([3.5, 0, 0])
    assert not geometry.capsule_clear([4.5, 0, 0])
    assert not geometry.capsule_clear([19.5, 0, 0])
    assert not geometry.capsule_clear([0, 3, 0])
    assert not geometry.capsule_clear([0, -3, 0])


def test_low_ceiling_blocks_capsule_but_overhead_structure_does_not():
    data = definition()
    box = data["navigation"]["collision"]["solid_boxes"][0]
    box.update(min=[-2, 1.7, -2], max=[2, 4, 2])
    assert not MobilityGeometry(data).capsule_clear([0, 0, 0])
    box["min"][1] = 2
    assert MobilityGeometry(data).capsule_clear([0, 0, 0])


def test_vehicle_checks_rotated_full_body_and_swept_path_not_only_endpoints():
    geometry = MobilityGeometry(definition())
    assert geometry.vehicle_clear([0, 0, 0], 0)
    assert not geometry.vehicle_clear([2, 0, 0], math.pi / 2)
    assert geometry.vehicle_clear([10, 0, 0], math.pi / 2)
    assert not geometry.path_clear([0, 0, 0], [10, 0, 0])
    assert geometry.path_clear([-10, 0, -10], [10, 0, -10])
    assert not geometry.path_clear([-19, 0, -10], [10, 0, -10])


def test_bridge_support_preserves_grade_and_rejects_gap():
    data = definition()
    surfaces = data["navigation"]["collision"]["support_surfaces"]
    surfaces[0]["plane"] = [0.05, 0, 0]
    geometry = MobilityGeometry(data)
    assert geometry.path_clear([-10, -0.5, -10], [10, 0.5, -10])
    assert not geometry.path_clear([-10, 0, -10], [10, 0, -10])
    surfaces[0]["polygon"] = [[-20, -20], [-1, -20], [-1, 20], [-20, 20]]
    right = copy.deepcopy(surfaces[0])
    right.update(id="right", polygon=[[1, -20], [20, -20], [20, 20], [1, 20]])
    surfaces.append(right)
    assert not MobilityGeometry(data).path_clear([-10, -0.5, -10], [10, 0.5, -10])


def test_concave_shore_cannot_cut_through_vehicle_between_supported_corners():
    data = definition()
    data["navigation"]["collision"]["support_surfaces"][0]["polygon"] = [
        [-20, -20],
        [20, -20],
        [20, 20],
        [0.2, 20],
        [0.2, 0],
        [-0.2, 0],
        [-0.2, 20],
        [-20, 20],
    ]
    geometry = MobilityGeometry(data)
    assert not geometry.vehicle_clear([0, 0, 0], 0)
    assert not geometry.supported([0, 0, -0.1], 0.6)


def test_corner_to_corner_notch_does_not_grant_support_under_the_vehicle():
    data = definition()
    data["navigation"]["collision"]["support_surfaces"][0]["polygon"] = [
        [-20, -20],
        [20, -20],
        [20, 20],
        [1.7, 20],
        [1.7, -2.4],
        [-1.7, 2.4],
        [-1.7, 20],
        [-20, 20],
    ]
    geometry = MobilityGeometry(data)
    assert not geometry.supported([0, 0, 1])
    assert not geometry.vehicle_clear([0, 0, 0], 0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_invalid_positions_fail_closed(bad):
    geometry = MobilityGeometry(definition())
    assert not geometry.capsule_clear([bad, 0, 0])
    assert not geometry.vehicle_clear([0, 0, 0], bad)
    assert not geometry.path_clear([0, 0, 0], [bad, 0, 0])


@pytest.mark.parametrize("change", ["missing", "nan", "duplicate", "degenerate", "bounds"])
def test_invalid_packaged_collision_rejected(change):
    data = definition()
    collision = data["navigation"]["collision"]
    if change == "missing":
        data["navigation"].pop("collision")
    elif change == "nan":
        collision["support_surfaces"][0]["plane"][0] = float("nan")
    elif change == "duplicate":
        collision["solid_boxes"].append(collision["solid_boxes"][0])
    elif change == "degenerate":
        collision["support_surfaces"][0]["polygon"] = [[0, 0], [1, 1], [2, 2]]
    else:
        collision["solid_boxes"][0]["min"][0] = 10
    with pytest.raises(ValueError):
        MobilityGeometry(data)
