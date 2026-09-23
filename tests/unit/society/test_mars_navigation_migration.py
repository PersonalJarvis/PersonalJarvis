"""Persisted graph updates prove exact geometry before remapping a placed body."""

from __future__ import annotations

import copy

import pytest

from jarvis.society.mars.models import StationError
from jarvis.society.mars.navigation import NavigationGraph
from jarvis.society.mars.navigation_models import MoveCommand, NavigationState
from jarvis.society.mars.navigation_service import MarsNavigationService
from jarvis.society.mars.navigation_store import MarsNavigationStore


def source():
    return {
        "world_id": "mars:ordinary",
        "layout_version": 1,
        "spawn": {"position": [0, 0, 0]},
        "stations": [
            {"id": "finish", "anchor": "end", "capacity": 1},
            {"id": "origin", "anchor": "start", "capacity": 1},
        ],
        "navigation": {
            "version": 1,
            "nodes": [
                {"id": "start", "position": [0, 0, 0]},
                {"id": "end", "position": [20, 0, 0]},
            ],
            "edges": [
                {
                    "id": "road",
                    "from": "start",
                    "to": "end",
                    "width": 7,
                    "modes": ["pedestrian", "rover"],
                }
            ],
        },
    }


def replacement(old):
    data = copy.deepcopy(old)
    data["navigation"]["nodes"].extend(
        [{"id": "split-a", "position": [6, 0, 0]}, {"id": "split-b", "position": [14, 0, 0]}]
    )
    data["navigation"]["edges"] = [
        {"id": edge, "from": start, "to": end, "width": 7, "modes": ["pedestrian", "rover"]}
        for edge, start, end in [
            ("lead", "start", "split-a"),
            ("ride", "split-a", "split-b"),
            ("tail", "split-b", "end"),
        ]
    ]
    data["navigation"]["graph_migrations"] = [
        {
            "source_signature": NavigationGraph(old).signature,
            "source_descriptor": {
                key: copy.deepcopy(old[key]) for key in ("navigation", "stations", "spawn")
            },
            "edge_splits": {"road": ["lead", "ride", "tail"]},
        }
    ]
    return data


async def allow(*args):
    return True


@pytest.mark.parametrize("reverse", [False, True])
async def test_subdivision_recovers_saved_forward_and_reverse_pose_without_teleport(
    tmp_path, reverse
):
    now = [100_000]
    old = source()
    if reverse:
        old["spawn"]["position"] = [20, 0, 0]
    path = tmp_path / "navigation.db"
    service = MarsNavigationService(
        MarsNavigationStore(path, clock_ms=lambda: now[0]), old, authorize=allow
    )
    await service.start()
    command = await service.submit(
        "real-agent", MoveCommand(request_id="move", station_id="origin" if reverse else "finish")
    )
    now[0] += 5000
    await service.advance()
    before = await service.store.get(command.command_id)
    assert 0 < before.edge_progress < 1
    await service.close()
    now[0] += 30000
    updated = MarsNavigationService(
        MarsNavigationStore(path, clock_ms=lambda: now[0]), replacement(old), authorize=allow
    )
    await updated.start()
    try:
        recovered = await updated.store.get(command.command_id)
        assert recovered.position == before.position
        assert recovered.edge_id == "ride"
        assert recovered.current_node == ("split-b" if reverse else "split-a")
        assert recovered.graph_signature == updated.graph.signature
        await updated.advance()
        assert (await updated.store.get(command.command_id)).position == before.position
        now[0] += 1000
        await updated.advance()
        after = await updated.store.get(command.command_id)
        assert after.position[0] == pytest.approx(before.position[0] + (-1.8 if reverse else 1.8))
    finally:
        await updated.close()


@pytest.mark.parametrize("tamper", ["descriptor", "curve", "width", "station"])
async def test_unproven_geometry_changes_freeze_exact_physical_body(tmp_path, tamper):
    now = [100_000]
    old = source()
    path = tmp_path / "navigation.db"
    service = MarsNavigationService(
        MarsNavigationStore(path, clock_ms=lambda: now[0]), old, authorize=allow
    )
    await service.start()
    record = await service.submit("agent", MoveCommand(request_id="move", station_id="finish"))
    now[0] += 5000
    await service.advance()
    before = await service.store.get(record.command_id)
    await service.close()
    new = replacement(old)
    if tamper == "descriptor":
        new["navigation"]["graph_migrations"][0]["source_descriptor"]["navigation"]["edges"][0][
            "width"
        ] = 8
    elif tamper == "curve":
        new["navigation"]["nodes"][-1]["position"][2] = 1
    elif tamper == "width":
        new["navigation"]["edges"][1]["width"] = 8
    else:
        new["stations"][0]["anchor"] = "split-b"
    updated = MarsNavigationService(
        MarsNavigationStore(path, clock_ms=lambda: now[0]), new, authorize=allow
    )
    await updated.start()
    try:
        now[0] += 1000
        await updated.advance()
        after = await updated.store.get(record.command_id)
        assert after.position == before.position and after.graph_signature == before.graph_signature
        assert after.state is NavigationState.UNREACHABLE
        assert after.reason == "navigation_location_graph_changed"
        assert (await updated.snapshot()).occupancies[0].position == before.position
        # A new edge ID must not let a second body drive through the unmapped
        # placement just because its old resource name no longer exists.
        with pytest.raises(StationError, match="navigation_location_graph_changed"):
            await updated.submit(
                "other-agent", MoveCommand(request_id="new-move", station_id="finish")
            )
        now[0] += 5000
        await updated.advance()
        snapshot = await updated.snapshot()
        assert {command.agent_id for command in snapshot.commands} == {"agent"}
        assert snapshot.occupancies[0].position == before.position
    finally:
        await updated.close()
