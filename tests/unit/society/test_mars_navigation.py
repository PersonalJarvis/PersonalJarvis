"""Real SQLite fixtures for bounded, renderer-independent logical travel."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.society.mars.models import StationError
from jarvis.society.mars.navigation import NavigationGraph
from jarvis.society.mars.navigation_models import MoveCommand, NavigationState, TravelMode
from jarvis.society.mars.navigation_service import MarsNavigationService
from jarvis.society.mars.navigation_store import MarsNavigationStore


class Clock:
    now = 100_000

    def __call__(self):
        return self.now


class Authority:
    def __init__(self):
        self.calls = []
        self.allowed = True

    async def __call__(self, agent_id, station_id, mode):
        self.calls.append((agent_id, station_id, mode))
        return self.allowed


def definition(*, alternate=False):
    nodes = [
        {"id": "a", "position": [0, 0, 0]},
        {"id": "b", "position": [4, 0, 0]},
        {"id": "c", "position": [8, 0, 0]},
        {"id": "d", "position": [4, 0, 4]},
    ]
    edges = [
        {"id": "ab", "from": "a", "to": "b", "width": 7, "modes": ["pedestrian", "rover"]},
        {"id": "bc", "from": "b", "to": "c", "width": 2.4, "modes": ["pedestrian"]},
        {"id": "cd", "from": "c", "to": "d", "width": 2.4, "modes": ["pedestrian"]},
    ]
    if alternate:
        edges.extend(
            [
                {"id": "bd", "from": "b", "to": "d", "width": 2.4, "modes": ["pedestrian"]},
            ]
        )
    return {
        "world_id": "mars:ordinary",
        "layout_version": 1,
        "spawn": {"position": [0, 0, 0]},
        "navigation": {"version": 1, "nodes": nodes, "edges": edges},
        "stations": [
            {"id": "destination", "anchor": "c", "capacity": 1},
            {"id": "middle", "anchor": "b", "capacity": 1},
            {"id": "origin", "anchor": "a", "capacity": 1},
            {"id": "parking", "anchor": "d", "capacity": 1},
        ],
    }


def request(identity="visit", station="destination", mode=TravelMode.PEDESTRIAN):
    return MoveCommand(request_id=identity, station_id=station, mode=mode)


@pytest.fixture
async def runtime(tmp_path):
    clock, authority = Clock(), Authority()
    store = MarsNavigationStore(tmp_path / "navigation.db", clock_ms=clock)
    service = MarsNavigationService(store, definition(), authorize=authority)
    await service.start()
    try:
        yield service, clock, authority
    finally:
        await service.close()


async def tick(service, clock, count=1, *, interval=1000, **kwargs):
    for _ in range(count):
        clock.now += interval
        await service.advance(**kwargs)


async def test_duplicate_requests_authorize_once_and_keep_stable_identity(runtime):
    service, _, authority = runtime
    records = await asyncio.gather(*(service.submit("agent", request()) for _ in range(8)))
    assert len({record.command_id for record in records}) == 1
    assert len(authority.calls) == 1
    assert records[0].placement == "canonical_spawn"
    assert records[0].position == (0, 0, 0)
    with pytest.raises(StationError, match="idempotency_payload_mismatch"):
        await service.submit("agent", request(station="middle"))


async def test_authorization_denial_and_nonboolean_fail_closed_without_persistence(runtime):
    service, _, authority = runtime
    for value in [False, None, {"allowed": True}]:
        authority.allowed = value
        with pytest.raises(StationError, match="navigation_not_authorized"):
            await service.submit("agent", request())
    assert (await service.snapshot()).commands == ()


async def test_authorization_timeout_is_bounded_and_public(runtime):
    service, _, _ = runtime

    async def wait_forever(*args):
        await asyncio.Event().wait()

    service.authorize = wait_forever
    service.authorization_timeout_s = 0.001
    with pytest.raises(StationError, match="navigation_authorization_unavailable"):
        await service.submit("agent", request())
    assert (await service.snapshot()).commands == ()


async def test_arrived_body_retains_station_after_lease_expiry_and_cancel(runtime):
    service, clock, _ = runtime
    first = await service.submit("first", request())
    second = await service.submit("second", request())
    await tick(service, clock, 5)
    assert (await service.store.get(first.command_id)).state is NavigationState.ARRIVED
    waiting = await service.store.get(second.command_id)
    assert waiting.state is NavigationState.QUEUEING
    assert waiting.position == (0, 0, 0)
    await tick(service, clock, 16)
    assert (await service.store.get(second.command_id)).state is NavigationState.QUEUEING
    assert all(lease.command_id != first.command_id for lease in (await service.snapshot()).leases)
    await service.cancel("first", first.command_id)
    await tick(service, clock, 3)
    assert (await service.store.get(second.command_id)).position == (0, 0, 0)
    occupancy = (await service.snapshot()).occupancies
    assert (
        next(body for body in occupancy if body.resource_id == "station:destination").agent_id
        == "first"
    )
    departure = await service.submit("first", request("depart", station="parking"))
    # Accepting departure does not itself free a physical station slot.
    assert (await service.snapshot()).occupancies[0].position == (8, 0, 0)
    await tick(service, clock, 9)
    assert (await service.store.get(departure.command_id)).state is NavigationState.ARRIVED
    assert (await service.store.get(second.command_id)).state is NavigationState.ARRIVED
    latest = {row.agent_id: row for row in (await service.snapshot()).commands}
    assert latest["first"].position != latest["second"].position


async def test_cancel_releases_resources_and_preserves_mid_edge_for_new_request(runtime):
    service, clock, _ = runtime
    first = await service.submit("first", request())
    await tick(service, clock)
    moving = await service.store.get(first.command_id)
    assert 0 < moving.edge_progress < 1
    canceled = await service.cancel("first", first.command_id)
    assert canceled.position == moving.position
    assert (await service.snapshot()).leases == ()
    occupancy = (await service.snapshot()).occupancies
    assert len(occupancy) == 1 and occupancy[0].resource_id == "edge:ab"
    resumed = await service.submit("first", request("next"))
    assert resumed.placement == "persisted"
    assert resumed.position == canceled.position
    assert resumed.edge_progress == canceled.edge_progress
    await tick(service, clock)
    assert (await service.store.get(resumed.command_id)).position[0] > canceled.position[0]


async def test_cancel_cannot_stop_another_agent_or_reset_its_position(runtime):
    service, clock, _ = runtime
    record = await service.submit("first", request())
    await tick(service, clock)
    before = await service.store.get(record.command_id)
    with pytest.raises(StationError, match="navigation_command_not_found"):
        await service.cancel("second", record.command_id)
    assert await service.store.get(record.command_id) == before


async def test_agent_has_one_active_journey_and_queue_is_bounded(runtime):
    service, _, _ = runtime
    service.store.queue_limit = 2
    await service.submit("first", request())
    with pytest.raises(StationError, match="agent_already_moving"):
        await service.submit("first", request("second"))
    await service.submit("second", request())
    with pytest.raises(StationError, match="navigation_queue_full"):
        await service.submit("third", request())


async def test_mid_edge_obstruction_stops_in_place_and_exhausts_retry_budget(runtime):
    service, clock, _ = runtime
    record = await service.submit("first", request())
    await tick(service, clock)
    moving = await service.store.get(record.command_id)
    await tick(service, clock, blocked_edges=["ab"])
    blocked = await service.store.get(record.command_id)
    assert blocked.state is NavigationState.TEMPORARILY_BLOCKED
    assert blocked.position == moving.position
    await tick(service, clock, 10, blocked_edges=["ab"])
    failed = await service.store.get(record.command_id)
    assert failed.state is NavigationState.UNREACHABLE
    assert failed.reason == "navigation_retry_exhausted"
    assert failed.position == moving.position
    assert (await service.snapshot()).leases == ()


async def test_route_replans_at_safe_node_around_new_block(tmp_path):
    clock = Clock()
    service = MarsNavigationService(
        MarsNavigationStore(tmp_path / "alternate.db", clock_ms=clock),
        definition(alternate=True),
        authorize=Authority(),
    )
    await service.start()
    try:
        record = await service.submit("agent", request())
        await tick(service, clock)
        await tick(service, clock, blocked_edges=["bc"])
        rerouting = await service.store.get(record.command_id)
        assert rerouting.state is NavigationState.REROUTING
        assert "bc" not in rerouting.path
        await tick(service, clock, 10, blocked_edges=["bc"])
        assert (await service.store.get(record.command_id)).state is NavigationState.ARRIVED
    finally:
        await service.close()


async def test_rover_cannot_reach_pedestrian_only_console(runtime):
    service, clock, _ = runtime
    record = await service.submit("first", request(mode=TravelMode.ROVER))
    await tick(service, clock)
    failed = await service.store.get(record.command_id)
    assert failed.state is NavigationState.UNREACHABLE
    assert failed.reason == "navigation_route_unreachable"
    assert failed.position == (0, 0, 0)


async def test_deadline_bounds_station_queue_and_no_progress(runtime):
    service, clock, _ = runtime
    service.deadline_ms = 1000
    record = await service.submit("first", request())
    await tick(service, clock)
    failed = await service.store.get(record.command_id)
    assert failed.state is NavigationState.UNREACHABLE
    assert failed.reason == "navigation_deadline_exceeded"


async def test_unready_logical_destination_waits_and_recovers(runtime):
    service, clock, _ = runtime
    record = await service.submit("first", request())
    await tick(service, clock, 2, unavailable_stations=["destination"])
    assert (await service.store.get(record.command_id)).position == (0, 0, 0)
    await tick(service, clock, 5)
    assert (await service.store.get(record.command_id)).state is NavigationState.ARRIVED


async def test_restart_releases_old_leases_resumes_exact_position_and_preserves_replay(runtime):
    service, clock, authority = runtime
    record = await service.submit("agent", request())
    await tick(service, clock)
    moving = await service.store.get(record.command_id)
    # Close the store only: emulate process loss without a service cleanup transaction.
    await service.store.close()
    recovered = MarsNavigationService(
        MarsNavigationStore(service.store.path, clock_ms=clock), definition(), authorize=authority
    )
    clock.now += 50_000
    await recovered.start()
    try:
        assert (await recovered.snapshot()).leases == ()
        replay = await recovered.submit("agent", request())
        assert replay.command_id == record.command_id
        assert replay.position == moving.position
        await recovered.advance()
        assert (await recovered.store.get(record.command_id)).position == moving.position
        await tick(recovered, clock)
        assert (await recovered.store.get(record.command_id)).position[0] > moving.position[0]
        assert len(authority.calls) >= 3
    finally:
        await recovered.close()
        service._started = False


async def test_only_one_process_can_own_navigation_database(runtime):
    service, clock, _ = runtime
    second = MarsNavigationStore(service.store.path, clock_ms=clock)
    with pytest.raises(StationError, match="navigation_owned_by_another_process"):
        await second.open()


async def test_graph_content_change_refuses_teleport_or_silent_reinterpretation(runtime):
    service, clock, authority = runtime
    record = await service.submit("agent", request())
    await tick(service, clock)
    await service.close()
    changed = definition()
    changed["navigation"]["nodes"][1]["position"][0] = 5
    recovered = MarsNavigationService(service.store, changed, authorize=authority)
    await recovered.start()
    try:
        await recovered.advance()
        assert (await recovered.store.get(record.command_id)).reason == "navigation_graph_changed"
        with pytest.raises(StationError, match="navigation_location_graph_changed"):
            await recovered.submit("agent", request("new"))
    finally:
        await recovered.close()


async def test_checked_catchup_is_bounded_and_never_crosses_a_block(runtime):
    service, clock, _ = runtime
    record = await service.submit("agent", request())
    await tick(service, clock, interval=50_000, blocked_edges=["bc"])
    current = await service.store.get(record.command_id)
    assert current.state is NavigationState.TEMPORARILY_BLOCKED
    assert current.position == (0, 0, 0)
    await service.cancel("agent", record.command_id)
    long_graph = definition()
    long_graph["navigation"]["nodes"][1]["position"][0] = 400
    graph = NavigationGraph(long_graph)
    assert graph.path("a", "middle", TravelMode.ROVER) == ("ab",)


async def test_narrow_route_is_exclusive_between_independent_destinations(runtime):
    service, clock, _ = runtime
    first = await service.submit("first", request(station="middle"))
    second = await service.submit("second", request())
    await tick(service, clock)
    waiting = await service.store.get(second.command_id)
    assert waiting.state is NavigationState.QUEUEING
    assert waiting.reason == "physical_route_occupied"
    assert waiting.position == (0, 0, 0)
    leases = (await service.snapshot()).leases
    assert sum(lease.resource_id == "edge:ab" for lease in leases) == 1
    assert (
        next(lease for lease in leases if lease.resource_id == "edge:ab").command_id
        == first.command_id
    )
    await tick(service, clock, 8)
    assert (await service.store.get(second.command_id)).state is NavigationState.QUEUEING
    assert (await service.store.get(second.command_id)).position == (0, 0, 0)
    await service.submit("first", request("departure", station="parking"))
    await tick(service, clock, 10)
    assert (await service.store.get(second.command_id)).state is NavigationState.ARRIVED


@pytest.mark.parametrize("interruption", ["cancel", "deadline", "restart"])
async def test_mid_edge_body_precedes_older_waiter_after_interruption(runtime, interruption):
    service, clock, _ = runtime
    older = await service.submit("older", request())
    if interruption == "deadline":
        service.deadline_ms = 2000
    younger = await service.submit("younger", request(station="middle"))
    service.deadline_ms = 600_000
    await tick(service, clock, unavailable_stations=["destination"])
    before = await service.store.get(younger.command_id)
    assert before.position == (1.8, 0, 0)
    if interruption == "cancel":
        await service.cancel("younger", younger.command_id)
    elif interruption == "deadline":
        await tick(service, clock, unavailable_stations=["destination"])
        assert (await service.store.get(younger.command_id)).state is NavigationState.UNREACHABLE
    else:
        await service.close()
        await service.start()
    # Intent leases may expire or disappear; the body must still win over FIFO admission.
    await tick(service, clock, interval=30_000)
    older_after = await service.store.get(older.command_id)
    younger_after = await service.store.get(younger.command_id)
    assert older_after.position == (0, 0, 0)
    assert older_after.state is NavigationState.QUEUEING
    assert younger_after.position != older_after.position
    if interruption != "restart":
        assert younger_after.position == before.position
        assert any(
            body.resource_id == "edge:ab" and body.agent_id == "younger"
            for body in (await service.snapshot()).occupancies
        )


async def test_queued_request_reauthorizes_before_first_physical_movement(runtime):
    service, clock, authority = runtime
    record = await service.submit("agent", request())
    authority.allowed = False
    await tick(service, clock)
    denied = await service.store.get(record.command_id)
    assert denied.state is NavigationState.CANCELED
    assert denied.reason == "navigation_not_authorized"
    assert denied.position == record.position
    assert denied.presence == "spawn_queue"
    assert len(authority.calls) == 2
    assert (await service.snapshot()).occupancies == ()


@pytest.mark.parametrize("restart", [False, True])
async def test_revoked_moving_actor_stops_but_remains_a_physical_obstacle(runtime, restart):
    service, clock, authority = runtime
    record = await service.submit("agent", request())
    await tick(service, clock)
    before = await service.store.get(record.command_id)
    if restart:
        await service.close()
        await service.start()
    authority.allowed = False
    await tick(service, clock)
    denied = await service.store.get(record.command_id)
    assert denied.state is NavigationState.CANCELED
    assert denied.reason == "navigation_not_authorized"
    assert denied.position == before.position
    assert (await service.snapshot()).leases == ()
    occupancy = (await service.snapshot()).occupancies
    assert len(occupancy) == 1 and occupancy[0].resource_id == "edge:ab"
    authority.allowed = True
    next_request = await service.submit("agent", request("resume"))
    await tick(service, clock)
    assert (await service.store.get(next_request.command_id)).position[0] > denied.position[0]


async def test_physical_body_survives_snapshot_history_window(runtime):
    service, clock, _ = runtime
    parked = await service.submit("parked", request(station="middle"))
    await tick(service, clock, 3)
    assert (await service.store.get(parked.command_id)).state is NavigationState.ARRIVED
    for index in range(70):
        await service.submit("other", request(str(index), station="origin"))
        await tick(service, clock)
    snapshot = await service.snapshot()
    assert parked.command_id in {row.command_id for row in snapshot.commands}
    assert any(
        body.agent_id == "parked" and body.resource_id == "node:b" for body in snapshot.occupancies
    )
    departure = await service.submit("other", request("depart"))
    await tick(service, clock)
    assert (await service.store.get(departure.command_id)).position == (0, 0, 0)


async def test_spawn_queue_is_explicit_and_actor_capacity_never_discards_a_body(runtime):
    service, clock, _ = runtime
    service.store.actor_limit = 2
    first = await service.submit("first", request(station="origin"))
    await tick(service, clock)
    second = await service.submit("second", request())
    await tick(service, clock)
    waiting = await service.store.get(second.command_id)
    assert waiting.reason == "physical_spawn_occupied"
    assert waiting.presence == "spawn_queue"
    assert (await service.store.get(first.command_id)).presence == "placed"
    with pytest.raises(StationError, match="navigation_actor_limit_reached"):
        await service.submit("third", request())
    assert {body.agent_id for body in (await service.snapshot()).occupancies} == {"first"}


async def test_canceled_waiter_can_change_mode_before_entering_reserved_edge(runtime):
    service, clock, _ = runtime
    await service.submit("first", request(station="middle"))
    waiter = await service.submit("waiter", request())
    await tick(service, clock)
    waiting = await service.store.get(waiter.command_id)
    assert waiting.presence == "spawn_queue"
    assert waiting.edge_id == "ab" and waiting.edge_progress == 0
    await service.cancel("waiter", waiter.command_id)
    changed = await service.submit(
        "waiter", request("rover", station="middle", mode=TravelMode.ROVER)
    )
    assert changed.mode is TravelMode.ROVER
    assert changed.position == waiting.position
    assert changed.current_node == waiting.current_node
    assert changed.edge_id is None and changed.next_node is None
    assert changed.edge_progress == 0


async def test_canceled_waiter_targeting_current_node_does_not_traverse_unentered_edge(runtime):
    service, clock, _ = runtime
    await service.submit("first", request(station="middle"))
    waiter = await service.submit("waiter", request())
    await tick(service, clock)
    waiting = await service.store.get(waiter.command_id)
    assert waiting.edge_id == "ab" and waiting.edge_progress == 0
    await service.cancel("waiter", waiter.command_id)
    at_origin = await service.submit("waiter", request("stay", station="origin"))
    await tick(service, clock)
    arrived = await service.store.get(at_origin.command_id)
    assert arrived.state is NavigationState.ARRIVED
    assert arrived.position == waiting.position == (0, 0, 0)
    assert arrived.edge_id is None and arrived.next_node is None
    assert not any(
        body.agent_id == "waiter" and body.resource_id == "edge:ab"
        for body in (await service.snapshot()).occupancies
    )
