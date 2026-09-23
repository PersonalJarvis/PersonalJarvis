"""Real SQLite rover contracts: exclusive seats, authoritative motion and safe exits."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.society.mars.models import StationError
from jarvis.society.mars.navigation_models import (
    MoveCommand,
    NavigationState,
    RideState,
    RoverActionCommand,
    RoverReserveCommand,
    RoverTravelCommand,
)
from jarvis.society.mars.navigation_service import MarsNavigationService
from jarvis.society.mars.navigation_store import MarsNavigationStore


class Clock:
    now = 100_000

    def __call__(self):
        return self.now


class Authority:
    allowed = True

    async def __call__(self, agent_id, destination, mode):
        return self.allowed


def definition():
    positions = {
        "spawn": [-5, 0, 2.8],
        "a": [5, 0, 0],
        "b": [15, 0, 0],
        "a-board": [5, 0, 2.8],
        "b-board": [15, 0, 2.8],
        "a-exit": [5, 0, -2.8],
        "b-exit": [15, 0, -2.8],
        "a-alt": [5, 0, 3.4],
        "b-alt": [15, 0, 3.4],
    }
    edges = [{"id": "road", "from": "a", "to": "b", "width": 7, "modes": ["pedestrian", "rover"]}]
    for edge_id, start, end in [
        ("approach", "spawn", "a-board"),
        ("shoulder", "a-board", "b-board"),
        ("a-exit-path", "a-exit", "spawn"),
        ("b-exit-path", "b-exit", "spawn"),
        ("a-alt-path", "a-alt", "spawn"),
        ("b-alt-path", "b-alt", "spawn"),
    ]:
        edges.append(
            {"id": edge_id, "from": start, "to": end, "width": 2.4, "modes": ["pedestrian"]}
        )
    stations = [
        {"id": key, "anchor": key, "capacity": 1} for key in positions if key not in {"a", "b"}
    ]
    return {
        "world_id": "mars:ordinary",
        "layout_version": 1,
        "spawn": {"position": positions["spawn"]},
        "stations": stations,
        "navigation": {
            "version": 1,
            "nodes": [{"id": key, "position": value} for key, value in positions.items()],
            "edges": edges,
            "rover_docks": [
                {
                    "id": dock,
                    "node_id": dock,
                    "boarding_station_id": dock + "-board",
                    "exit_station_ids": [dock + "-exit", dock + "-alt"],
                    "yaw": 1.5707963267948966,
                }
                for dock in ("a", "b")
            ],
            "rovers": [
                {
                    "id": "rover",
                    "home_dock_id": "a",
                    "seat_capacity": 1,
                    "width_m": 3.4,
                    "length_m": 4.8,
                    "height_m": 2.6,
                    "speed_m_s": 4,
                }
            ],
            "collision": {
                "version": 1,
                "source_sha256": "0" * 64,
                "solid_boxes": [{"id": "far-solid", "min": [45, 0, 45], "max": [46, 10, 46]}],
                "support_surfaces": [
                    {
                        "id": "floor",
                        "polygon": [[-50, -50], [50, -50], [50, 50], [-50, 50]],
                        "plane": [0, 0, 0],
                    }
                ],
            },
        },
    }


@pytest.fixture
async def runtime(tmp_path):
    clock, authority = Clock(), Authority()
    service = MarsNavigationService(
        MarsNavigationStore(tmp_path / "navigation.db", clock_ms=clock),
        definition(),
        authorize=authority,
        authorize_rover=authority,
    )
    await service.start()
    try:
        yield service, clock, authority
    finally:
        await service.close()


async def tick(service, clock, count=1, **kwargs):
    for _ in range(count):
        clock.now += 1000
        await service.advance(**kwargs)


async def ready(service, clock, agent="agent", request="reserve"):
    ride = await service.reserve_ride(
        agent, RoverReserveCommand(request_id=request, vehicle_id="rover")
    )
    await tick(service, clock, 7)
    ride = await service.store.get_ride(ride.ride_id)
    assert ride.state is RideState.READY_TO_BOARD
    return ride


async def boarded(service, clock):
    ride = await ready(service, clock)
    return await service.board_ride("agent", ride.ride_id, RoverActionCommand(request_id="board"))


async def test_exclusive_reservation_is_atomic_and_idempotent(runtime):
    service, _, _ = runtime
    command = RoverReserveCommand(request_id="reserve", vehicle_id="rover")
    results = await asyncio.gather(*(service.reserve_ride("agent", command) for _ in range(8)))
    assert len({ride.ride_id for ride in results}) == 1
    with pytest.raises(StationError, match="rover_seat_reserved"):
        await service.reserve_ride("other", command)
    with pytest.raises(StationError, match="idempotency_payload_mismatch"):
        await service.board_ride(
            "agent", results[0].ride_id, RoverActionCommand(request_id="reserve")
        )
    snapshot = await service.snapshot()
    assert len(snapshot.rides) == len(snapshot.commands) == 1
    assert snapshot.commands[0].agent_id == "agent"
    assert snapshot.vehicles[0].vehicle_id == "rover"


async def test_pedestrian_authority_does_not_implicitly_authorize_a_ride(runtime):
    service, _, _ = runtime
    service.authorize_rover = None
    with pytest.raises(StationError, match="navigation_not_authorized"):
        await service.reserve_ride(
            "agent", RoverReserveCommand(request_id="reserve", vehicle_id="rover")
        )
    assert (await service.snapshot()).rides == ()


async def test_explicit_boarding_and_complete_route_do_not_duplicate_rider_body(runtime):
    service, clock, _ = runtime
    ride = await service.reserve_ride(
        "agent", RoverReserveCommand(request_id="reserve", vehicle_id="rover")
    )
    with pytest.raises(StationError, match="rover_rider_not_ready"):
        await service.board_ride(
            "agent", ride.ride_id, RoverActionCommand(request_id="board-too-soon")
        )
    await tick(service, clock, 7)
    assert not (await service.store.get_ride(ride.ride_id)).attached
    ride = await service.board_ride("agent", ride.ride_id, RoverActionCommand(request_id="board"))
    assert ride.attached
    assert all(body.owner.kind == "vehicle" for body in (await service.snapshot()).occupancies)
    await service.travel_ride(
        "agent", ride.ride_id, RoverTravelCommand(request_id="travel", destination_dock_id="b")
    )
    await tick(service, clock)
    vehicle = (await service.snapshot()).vehicles[0]
    assert vehicle.position == (9, 0, 0)
    await tick(service, clock, 3)
    assert (await service.store.get_ride(ride.ride_id)).state is RideState.ARRIVED
    ride = await service.exit_ride("agent", ride.ride_id, RoverActionCommand(request_id="exit"))
    assert ride.state is RideState.COMPLETED and not ride.attached
    snapshot = await service.snapshot()
    assert {body.owner.kind for body in snapshot.occupancies} == {"agent", "vehicle"}
    assert (await service.store.get(ride.approach_command_id)).position == (15, 0, -2.8)
    assert snapshot.vehicles[0].position == (15, 0, 0)


async def test_revoked_boarding_cannot_acquire_seat_and_tick_releases_reservation(runtime):
    service, clock, authority = runtime
    ride = await ready(service, clock)
    authority.allowed = False
    with pytest.raises(StationError, match="navigation_not_authorized"):
        await service.board_ride("agent", ride.ride_id, RoverActionCommand(request_id="board"))
    await tick(service, clock)
    assert (await service.store.get_ride(ride.ride_id)).state is RideState.CANCELED
    assert (await service.snapshot()).vehicles[0].ride_id is None


async def test_cancel_midroad_retains_seat_and_pose_until_safe_dock_exit(runtime):
    service, clock, _ = runtime
    ride = await boarded(service, clock)
    await service.travel_ride(
        "agent", ride.ride_id, RoverTravelCommand(request_id="travel", destination_dock_id="b")
    )
    await tick(service, clock)
    before = (await service.snapshot()).vehicles[0]
    cancel = RoverActionCommand(request_id="cancel")
    stopped = await service.cancel_ride("agent", ride.ride_id, cancel)
    assert await service.cancel_ride("agent", ride.ride_id, cancel) == stopped
    await tick(service, clock, 10)
    after = (await service.snapshot()).vehicles[0]
    assert after.position == before.position and after.edge_progress == before.edge_progress
    blocked = await service.exit_ride(
        "agent", ride.ride_id, RoverActionCommand(request_id="unsafe-exit")
    )
    assert blocked.state is RideState.EXIT_BLOCKED and blocked.attached
    with pytest.raises(StationError, match="rover_seat_reserved"):
        await service.reserve_ride(
            "other", RoverReserveCommand(request_id="reserve", vehicle_id="rover")
        )
    await service.travel_ride(
        "agent", ride.ride_id, RoverTravelCommand(request_id="resume", destination_dock_id="b")
    )
    assert (await service.snapshot()).vehicles[0].position == before.position
    await tick(service, clock, 3)
    exited = await service.exit_ride(
        "agent", ride.ride_id, RoverActionCommand(request_id="safe-exit")
    )
    assert exited.state is RideState.COMPLETED


async def test_blocked_primary_exit_uses_alternate_and_both_blocked_retain_seat(runtime):
    service, clock, _ = runtime
    first = await service.submit("blocker", MoveCommand(request_id="block", station_id="a-exit"))
    await tick(service, clock, 8)
    assert (await service.store.get(first.command_id)).state is NavigationState.ARRIVED
    await service.submit("other", MoveCommand(request_id="block", station_id="a-alt"))
    await tick(service, clock, 8)
    ride = await boarded(service, clock)
    blocked = await service.exit_ride(
        "agent", ride.ride_id, RoverActionCommand(request_id="exit-both-blocked")
    )
    assert blocked.state is RideState.EXIT_BLOCKED and blocked.attached
    await service.submit("other", MoveCommand(request_id="leave", station_id="b-alt"))
    await tick(service, clock, 25)
    exited = await service.exit_ride(
        "agent", ride.ride_id, RoverActionCommand(request_id="exit-alternate")
    )
    assert exited.state is RideState.COMPLETED
    assert (await service.store.get(ride.approach_command_id)).current_node == "a-alt"


async def test_obstruction_retry_budget_stops_without_releasing_rider(runtime):
    service, clock, _ = runtime
    ride = await boarded(service, clock)
    await service.travel_ride(
        "agent", ride.ride_id, RoverTravelCommand(request_id="travel", destination_dock_id="b")
    )
    await tick(service, clock)
    pose = (await service.snapshot()).vehicles[0].position
    await tick(service, clock, 15, blocked_edges=["road"])
    ride = await service.store.get_ride(ride.ride_id)
    assert ride.state is RideState.STOPPED and ride.attached
    assert (await service.snapshot()).vehicles[0].position == pose


async def test_restart_pauses_distance_but_wall_deadline_retains_attached_seat(runtime):
    service, clock, _ = runtime
    ride = await boarded(service, clock)
    await service.travel_ride(
        "agent", ride.ride_id, RoverTravelCommand(request_id="travel", destination_dock_id="b")
    )
    await tick(service, clock)
    before = (await service.snapshot()).vehicles[0]
    await service.close()
    clock.now += 60_000
    await service.start()
    await service.advance()
    after = (await service.snapshot()).vehicles[0]
    assert after.position == before.position
    clock.now += 700_000
    await service.advance()
    ride = await service.store.get_ride(ride.ride_id)
    assert ride.state is RideState.STOPPED and ride.attached
    assert (await service.snapshot()).vehicles[0].position == before.position


async def test_failed_mutation_rolls_back_seat_body_and_receipt(runtime):
    service, _, _ = runtime
    before = await service.snapshot()

    def fail(world, now):
        world.vehicles.clear()
        world.leases.clear()
        raise RuntimeError("fixture rollback")

    with pytest.raises(RuntimeError, match="fixture rollback"):
        await service.store.mutate_world(fail)
    assert await service.snapshot() == before


async def test_unsafe_collision_data_prevents_vehicle_initialization(tmp_path):
    data = definition()
    data["navigation"]["collision"]["solid_boxes"].append(
        {"id": "wall", "min": [3, 0, -1], "max": [7, 3, 1]}
    )
    with pytest.raises(ValueError, match="unsafe rover dock"):
        MarsNavigationService(
            MarsNavigationStore(tmp_path / "navigation.db"), data, authorize=Authority()
        )
    assert not (tmp_path / "navigation.db").exists()


async def test_static_road_obstruction_refuses_travel_before_displacement(runtime):
    service, clock, _ = runtime
    ride = await boarded(service, clock)
    service.graph.geometry.boxes.append(((9, 0, -1), (10, 3, 1)))
    before = (await service.snapshot()).vehicles[0]
    with pytest.raises(StationError, match="rover_route_clearance_failed"):
        await service.travel_ride(
            "agent", ride.ride_id, RoverTravelCommand(request_id="travel", destination_dock_id="b")
        )
    assert (await service.snapshot()).vehicles[0] == before


def test_public_pedestrian_request_still_rejects_rover():
    from pydantic import ValidationError

    from jarvis.society.mars.navigation_models import PedestrianMoveCommand

    with pytest.raises(ValidationError):
        PedestrianMoveCommand.model_validate_json(
            '{"request_id":"x","station_id":"a","mode":"rover"}'
        )
