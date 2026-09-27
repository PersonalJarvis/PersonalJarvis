"""Portable navigation contract: real graph, strict messages, independent leases."""

from __future__ import annotations

import json

import aiosqlite
import pytest
from pydantic import ValidationError

from jarvis.society.mars.definition import load_definition
from jarvis.society.mars.models import StationCommand, StationError
from jarvis.society.mars.navigation import NavigationGraph
from jarvis.society.mars.navigation_models import MoveCommand, NavigationState, TravelMode
from jarvis.society.mars.navigation_service import MarsNavigationService
from jarvis.society.mars.navigation_store import MarsNavigationStore
from jarvis.society.mars.store import MarsStore


def test_canonical_routes_support_pedestrian_outpost_but_rover_stops_before_interior():
    graph = NavigationGraph(load_definition())
    assert graph.spawn_node == "outpost-arrival"
    assert graph.path(graph.spawn_node, "communications-console", TravelMode.PEDESTRIAN) == (
        "route-03",
        "route-04",
    )
    assert graph.path(graph.spawn_node, "communications-console", TravelMode.ROVER) is None


@pytest.mark.parametrize(
    "patch",
    [
        {"world_id": "other"},
        {"schema_version": True},
        {"graph_version": True},
        {"layout_version": 0},
        {"position": [1, 2, 3]},
        {"draft": "private input"},
    ],
)
def test_move_contract_rejects_cross_world_or_client_position_and_private_payload(patch):
    data = {"request_id": "request", "station_id": "communications-console", **patch}
    with pytest.raises(ValidationError):
        MoveCommand.model_validate_json(json.dumps(data))


async def test_navigation_does_not_acquire_task_lease_or_change_actual_task_state(tmp_path):
    calls = []

    async def authorize(agent_id, station_id, mode):
        calls.append((agent_id, station_id, mode))
        return True

    station = MarsStore(tmp_path / "work.db")
    navigation = MarsNavigationService(
        MarsNavigationStore(tmp_path / "navigation.db"), load_definition(), authorize=authorize
    )
    await station.open()
    await navigation.start()
    try:
        task = await station.submit(
            "ordinary-agent", StationCommand(request_id="work", draft="Draft a short handover.")
        )
        before = await station.snapshot()
        journey = await navigation.submit(
            "ordinary-agent", MoveCommand(request_id="visit", station_id="communications-console")
        )
        await navigation.advance(unavailable_stations=["communications-console"])
        await navigation.cancel("ordinary-agent", journey.command_id)
        assert await station.snapshot() == before
        assert (await station.get(task.command_id)).state.value == "queued"
        assert len(calls) == 2
        payload = (await navigation.snapshot()).model_dump_json()
        assert "Draft a short handover" not in payload
        assert not {"draft", "task_ref", "result_ref"} & json.loads(payload)["commands"][0].keys()
    finally:
        await navigation.close()
        await station.close()


async def test_sql_and_public_state_values_match_and_unknown_graph_is_rejected(tmp_path):
    async def authorize(*args):
        return True

    store = MarsNavigationStore(tmp_path / "navigation.db")
    service = MarsNavigationService(store, load_definition(), authorize=authorize)
    await service.start()
    try:
        with pytest.raises(StationError, match="navigation_graph_version_mismatch"):
            await service.submit(
                "agent",
                MoveCommand(
                    request_id="visit", station_id="communications-console", graph_version=2
                ),
            )
        async with aiosqlite.connect(store.path) as conn:
            async with conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='navigation_commands'"
            ) as cursor:
                schema = (await cursor.fetchone())[0]
        for state in NavigationState:
            assert "'" + state.value + "'" in schema
        assert (await service.snapshot()).commands == ()
    finally:
        await service.close()
