"""Server-owned, one-seat route-driven rover actions inside navigation.db."""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import TypeAdapter

from .models import Identity, StationError
from .navigation_migration import migrate_record
from .navigation_models import (
    NAVIGATION_TERMINAL,
    RIDE_TERMINAL,
    MoveCommand,
    NavigationRecord,
    NavigationState,
    RideState,
    RoverActionCommand,
    RoverReserveCommand,
    RoverRideRecord,
    RoverTravelCommand,
    TravelMode,
    VehicleRecord,
)

_IDENTITY = TypeAdapter(Identity)


class RoverServiceMixin:
    """Uses the navigation service lock, owner, store and physical allocator.

    Reservations expire after ten wall-clock minutes including process downtime.
    Expiry revokes an unboarded reservation; boarded rides stop exactly in place
    and retain their seat until an explicit safe exit. A fresh travel action gets
    a fresh bounded deadline. Neither client absence nor wall time advances a body.
    """

    def _world_occupancy(self, world):
        return self.graph.occupancies(
            list(world.records.values()), list(world.vehicles.values()), list(world.rides.values())
        )

    def _recover_rovers(self, world, now):
        for key, record in world.records.items():
            world.records[key] = migrate_record(record, self.graph)
        for key, vehicle in world.vehicles.items():
            world.vehicles[key] = migrate_record(vehicle, self.graph).model_copy(
                update={"updated_ms": now}
            )
        # A changed graph cannot place a new vehicle over an unmigrated old body.
        if any(
            row.graph_signature != self.graph.signature
            for row in [*world.records.values(), *world.vehicles.values()]
        ):
            return
        for vehicle_id, spec in self.graph.rovers.items():
            if vehicle_id in world.vehicles:
                continue
            dock = self.graph.docks[spec["home_dock_id"]]
            node = dock["node_id"]
            command_id = "mars-rover:" + str(uuid5(NAMESPACE_URL, vehicle_id))
            world.vehicles[vehicle_id] = VehicleRecord(
                vehicle_id=vehicle_id,
                command_id=command_id,
                request_id=command_id,
                trace_id=str(uuid4()),
                graph_version=self.graph.version,
                graph_signature=self.graph.signature,
                layout_version=self.graph.layout_version,
                station_id="rover-dock:" + dock["id"],
                mode=TravelMode.ROVER,
                state=NavigationState.ARRIVED,
                position=self.graph.nodes[node],
                current_node=node,
                placement="canonical_spawn",
                presence="spawn_queue",
                created_ms=now,
                updated_ms=now,
                last_progress_ms=now,
                deadline_ms=now + self.deadline_ms,
            )
        self._admit_rovers(world, now)

    def _admit_rovers(self, world, now):
        if self._unmapped_bodies(world):
            return
        for key, vehicle in world.vehicles.items():
            if vehicle.presence != "spawn_queue" or vehicle.graph_signature != self.graph.signature:
                continue
            if self._available(
                world.leases, self._world_occupancy(world), "node:" + vehicle.current_node, vehicle
            ):
                world.vehicles[key] = vehicle.model_copy(
                    update={"presence": "placed", "updated_ms": now}
                )

    def _unmapped_bodies(self, world):
        return any(
            body.graph_signature != self.graph.signature for body in self._world_occupancy(world)
        )

    @staticmethod
    def _rover_identity(agent_id, action, ride_id, request):
        _IDENTITY.validate_python(agent_id)
        action_id = "mars-rover-action:" + str(
            uuid5(NAMESPACE_URL, json.dumps([agent_id, request.request_id]))
        )
        payload = json.dumps(
            [action, ride_id, request.model_dump(mode="json")],
            sort_keys=True,
            separators=(",", ":"),
        )
        return action_id, hashlib.sha256(payload.encode()).hexdigest()

    async def reserve_ride(self, agent_id: str, request: RoverReserveCommand) -> RoverRideRecord:
        async with self._lock:
            action_id, fingerprint = self._rover_identity(agent_id, "reserve", None, request)
            duplicate = await self.store.duplicate_action(action_id, fingerprint)
            if duplicate is not None:
                return duplicate
            if (
                request.graph_version != self.graph.version
                or request.layout_version != self.graph.layout_version
            ):
                raise StationError("navigation_graph_version_mismatch", 409)
            if request.vehicle_id not in self.graph.rovers:
                raise StationError("rover_not_found", 404)
            await self._authorize(agent_id, request.vehicle_id, TravelMode.ROVER, rover=True)
            ride_id = "mars-ride:" + str(uuid5(NAMESPACE_URL, action_id))

            def reserve(world, now):
                if self._unmapped_bodies(world):
                    raise StationError("navigation_location_graph_changed", 409)
                vehicle = world.vehicles.get(request.vehicle_id)
                if vehicle is None or vehicle.presence != "placed":
                    raise StationError("rover_not_ready", 409)
                if vehicle.graph_signature != self.graph.signature:
                    raise StationError("navigation_location_graph_changed", 409)
                if any(
                    ride.state not in RIDE_TERMINAL and ride.agent_id == agent_id
                    for ride in world.rides.values()
                ):
                    raise StationError("agent_has_rover_reservation", 409)
                if any(
                    ride.state not in RIDE_TERMINAL and ride.vehicle_id == request.vehicle_id
                    for ride in world.rides.values()
                ):
                    raise StationError("rover_seat_reserved", 409)
                dock_id = self._vehicle_dock(vehicle)
                if dock_id is None:
                    raise StationError("rover_not_at_dock", 409)
                dock = self.graph.docks[dock_id]
                move = MoveCommand(
                    request_id="rover-approach:" + str(uuid5(NAMESPACE_URL, ride_id)),
                    station_id=dock["boarding_station_id"],
                    graph_version=self.graph.version,
                    layout_version=self.graph.layout_version,
                )
                command_id, move_fingerprint = self.store.identity(agent_id, move)
                prior = next(
                    (
                        row
                        for row in reversed(list(world.records.values()))
                        if row.agent_id == agent_id
                    ),
                    None,
                )
                if prior and prior.state not in NAVIGATION_TERMINAL:
                    raise StationError("agent_already_moving", 409)
                if prior and prior.graph_signature != self.graph.signature:
                    raise StationError("navigation_location_graph_changed", 409)
                if (
                    sum(row.state not in NAVIGATION_TERMINAL for row in world.records.values())
                    >= self.store.queue_limit
                ):
                    raise StationError("navigation_queue_full", 429)
                if (
                    prior is None
                    and len({row.agent_id for row in world.records.values()})
                    >= self.store.actor_limit
                ):
                    raise StationError("navigation_actor_limit_reached", 429)
                transit = prior if prior and prior.edge_id and 0 < prior.edge_progress < 1 else None
                record = NavigationRecord(
                    command_id=command_id,
                    request_id=move.request_id,
                    agent_id=agent_id,
                    trace_id=str(uuid4()),
                    layout_version=self.graph.layout_version,
                    graph_version=self.graph.version,
                    graph_signature=self.graph.signature,
                    station_id=move.station_id,
                    mode=TravelMode.PEDESTRIAN,
                    state=NavigationState.QUEUEING,
                    position=prior.position if prior else self.graph.nodes[self.graph.spawn_node],
                    current_node=prior.current_node if prior else self.graph.spawn_node,
                    edge_id=transit.edge_id if transit else None,
                    next_node=transit.next_node if transit else None,
                    edge_progress=transit.edge_progress if transit else 0,
                    placement="persisted" if prior else "canonical_spawn",
                    presence=prior.presence if prior else "spawn_queue",
                    created_ms=now,
                    updated_ms=now,
                    last_progress_ms=now,
                    deadline_ms=now + self.deadline_ms,
                )
                if prior:
                    self._release(world.leases, prior.command_id)
                world.records[command_id] = record
                world.fingerprints[command_id] = move_fingerprint
                world.rides[ride_id] = RoverRideRecord(
                    ride_id=ride_id,
                    request_id=request.request_id,
                    agent_id=agent_id,
                    vehicle_id=request.vehicle_id,
                    trace_id=record.trace_id,
                    origin_dock_id=dock_id,
                    approach_command_id=command_id,
                    state=RideState.APPROACHING,
                    created_ms=now,
                    updated_ms=now,
                    deadline_ms=now + self.deadline_ms,
                )
                world.vehicles[vehicle.vehicle_id] = vehicle.model_copy(update={"ride_id": ride_id})
                world.actions[action_id] = (fingerprint, ride_id)

            await self.store.mutate_world(reserve)
            return await self.store.get_ride(ride_id)

    async def _ride_action(self, action, agent_id, ride_id, request):
        async with self._lock:
            action_id, fingerprint = self._rover_identity(agent_id, action, ride_id, request)
            duplicate = await self.store.duplicate_action(action_id, fingerprint)
            if duplicate is not None:
                return duplicate
            prior = await self.store.get_ride(ride_id)
            if prior.agent_id != agent_id:
                raise StationError("rover_ride_not_found", 404)
            await self._authorize(agent_id, prior.vehicle_id, TravelMode.ROVER, rover=True)

            def apply(world, now):
                ride = world.rides.get(ride_id)
                # A retained durable receipt outside the bounded working set is
                # terminal; include it solely to replay a no-op cancellation.
                if ride is None:
                    ride = prior
                    world.rides[ride_id] = ride
                vehicle = world.vehicles[ride.vehicle_id]
                if action == "cancel":
                    self._stop_ride(world, ride, now, "user_canceled")
                else:
                    if self._unmapped_bodies(world):
                        raise StationError("navigation_location_graph_changed", 409)
                    if ride.state in RIDE_TERMINAL:
                        raise StationError("rover_ride_finished", 409)
                    if vehicle.graph_signature != self.graph.signature:
                        raise StationError("navigation_location_graph_changed", 409)
                    if action == "board":
                        self._board_ride(world, ride, vehicle, now)
                    elif action == "travel":
                        self._travel_ride(world, ride, vehicle, request.destination_dock_id, now)
                    elif action == "exit":
                        self._exit_ride(world, ride, vehicle, now)
                world.actions[action_id] = (fingerprint, ride_id)

            await self.store.mutate_world(apply, include_id=prior.approach_command_id)
            return await self.store.get_ride(ride_id)

    async def board_ride(
        self, agent_id: str, ride_id: str, request: RoverActionCommand
    ) -> RoverRideRecord:
        return await self._ride_action("board", agent_id, ride_id, request)

    async def travel_ride(
        self, agent_id: str, ride_id: str, request: RoverTravelCommand
    ) -> RoverRideRecord:
        return await self._ride_action("travel", agent_id, ride_id, request)

    async def cancel_ride(
        self, agent_id: str, ride_id: str, request: RoverActionCommand
    ) -> RoverRideRecord:
        return await self._ride_action("cancel", agent_id, ride_id, request)

    async def exit_ride(
        self, agent_id: str, ride_id: str, request: RoverActionCommand
    ) -> RoverRideRecord:
        return await self._ride_action("exit", agent_id, ride_id, request)

    async def rover_snapshot(self):
        return await self.snapshot()

    def _vehicle_dock(self, vehicle):
        if vehicle.edge_id is not None and 0 < vehicle.edge_progress < 1:
            return None
        return next(
            (
                key
                for key, dock in self.graph.docks.items()
                if dock["node_id"] == vehicle.current_node
            ),
            None,
        )

    def _board_ride(self, world, ride, vehicle, now):
        if ride.state is not RideState.READY_TO_BOARD or ride.attached:
            raise StationError("rover_rider_not_ready", 409)
        if now >= ride.deadline_ms:
            raise StationError("rover_reservation_expired", 409)
        dock = self.graph.docks[ride.origin_dock_id]
        agent = world.records[ride.approach_command_id]
        if (
            agent.state is not NavigationState.ARRIVED
            or agent.presence != "placed"
            or agent.current_node != self.graph.stations[dock["boarding_station_id"]]
            or self._vehicle_dock(vehicle) != ride.origin_dock_id
            or vehicle.presence != "placed"
        ):
            raise StationError("rover_rider_not_at_boarding_anchor", 409)
        if not self.graph.geometry.capsule_clear(agent.position):
            raise StationError("rover_boarding_clearance_failed", 409)
        self._release(world.leases, agent.command_id)
        world.rides[ride.ride_id] = ride.model_copy(
            update={"state": RideState.BOARDED, "attached": True, "updated_ms": now}
        )

    def _travel_ride(self, world, ride, vehicle, destination, now):
        if not ride.attached or ride.state is RideState.TRAVELING:
            raise StationError("rover_rider_not_ready", 409)
        if destination not in self.graph.docks:
            raise StationError("rover_dock_not_found", 404)
        station = "rover-dock:" + destination
        start = (
            vehicle.next_node
            if vehicle.edge_id and 0 < vehicle.edge_progress < 1
            else vehicle.current_node
        )
        path = self.graph.path(start, station, TravelMode.ROVER)
        if path is None:
            raise StationError("rover_route_unreachable", 409)
        for edge_id in (
            *((vehicle.edge_id,) if vehicle.edge_id and 0 < vehicle.edge_progress < 1 else ()),
            *path,
        ):
            edge = self.graph.edges[edge_id]
            if not self.graph.geometry.path_clear(
                self.graph.nodes[edge.start],
                self.graph.nodes[edge.end],
                width=3.4,
                length=4.8,
                height=2.6,
            ):
                raise StationError("rover_route_clearance_failed", 409)
        self._release(world.leases, vehicle.command_id)
        updates = {
            "station_id": station,
            "state": NavigationState.QUEUEING,
            "path": (),
            "updated_ms": now,
            "deadline_ms": now + self.deadline_ms,
            "reason": "",
            "retries": 0,
            "retry_at_ms": 0,
        }
        if not (vehicle.edge_id and 0 < vehicle.edge_progress < 1):
            updates.update(edge_id=None, next_node=None, edge_progress=0)
        world.vehicles[vehicle.vehicle_id] = vehicle.model_copy(update=updates)
        world.rides[ride.ride_id] = ride.model_copy(
            update={
                "state": RideState.TRAVELING,
                "destination_dock_id": destination,
                "updated_ms": now,
                "deadline_ms": now + self.deadline_ms,
                "reason": "",
            }
        )

    def _stop_ride(self, world, ride, now, reason):
        if ride.state in RIDE_TERMINAL:
            return
        vehicle = world.vehicles[ride.vehicle_id]
        self._release(world.leases, vehicle.command_id)
        if ride.attached:
            world.vehicles[vehicle.vehicle_id] = vehicle.model_copy(
                update={"state": NavigationState.CANCELED, "updated_ms": now, "reason": reason}
            )
            world.rides[ride.ride_id] = ride.model_copy(
                update={"state": RideState.STOPPED, "updated_ms": now, "reason": reason}
            )
        else:
            agent = world.records.get(ride.approach_command_id)
            if agent is not None:
                self._release(world.leases, agent.command_id)
                world.records[agent.command_id] = agent.model_copy(
                    update={"state": NavigationState.CANCELED, "updated_ms": now, "reason": reason}
                )
            world.vehicles[vehicle.vehicle_id] = vehicle.model_copy(update={"ride_id": None})
            world.rides[ride.ride_id] = ride.model_copy(
                update={"state": RideState.CANCELED, "updated_ms": now, "reason": reason}
            )

    def _exit_ride(self, world, ride, vehicle, now):
        if not ride.attached:
            raise StationError("rover_rider_not_boarded", 409)
        dock_id = self._vehicle_dock(vehicle)
        if ride.state is RideState.TRAVELING or dock_id is None:
            world.rides[ride.ride_id] = ride.model_copy(
                update={
                    "state": RideState.EXIT_BLOCKED,
                    "updated_ms": now,
                    "reason": "rover_exit_requires_stopped_dock",
                }
            )
            # An exit request is a bounded safe stop, never mid-road detachment.
            world.vehicles[vehicle.vehicle_id] = vehicle.model_copy(
                update={"state": NavigationState.CANCELED, "updated_ms": now}
            )
            self._release(world.leases, vehicle.command_id)
            return
        agent = world.records[ride.approach_command_id]
        occupancy = self._world_occupancy(world)
        for station in self.graph.docks[dock_id]["exit_station_ids"]:
            node = self.graph.stations[station]
            position = self.graph.nodes[node]
            if (
                station in getattr(self, "_rover_unavailable", frozenset())
                or not self.graph.geometry.capsule_clear(position)
                or not self._available(world.leases, occupancy, "node:" + node, agent)
                or not self._available(world.leases, occupancy, "station:" + station, agent)
            ):
                continue
            world.records[agent.command_id] = agent.model_copy(
                update={
                    "state": NavigationState.ARRIVED,
                    "station_id": station,
                    "current_node": node,
                    "position": position,
                    "edge_id": None,
                    "next_node": None,
                    "edge_progress": 0,
                    "presence": "placed",
                    "placement": "persisted",
                    "updated_ms": now,
                    "graph_signature": self.graph.signature,
                    "graph_version": self.graph.version,
                    "path": (),
                    "reason": "rover_exit_completed",
                }
            )
            self._release(world.leases, vehicle.command_id)
            world.vehicles[vehicle.vehicle_id] = vehicle.model_copy(
                update={
                    "ride_id": None,
                    "state": NavigationState.ARRIVED,
                    "updated_ms": now,
                    "station_id": "rover-dock:" + dock_id,
                    "reason": "",
                }
            )
            world.rides[ride.ride_id] = ride.model_copy(
                update={
                    "state": RideState.COMPLETED,
                    "attached": False,
                    "updated_ms": now,
                    "reason": "rover_exit_completed",
                }
            )
            return
        world.rides[ride.ride_id] = ride.model_copy(
            update={
                "state": RideState.EXIT_BLOCKED,
                "updated_ms": now,
                "reason": "rover_exit_occupied",
            }
        )

    async def _ride_authorization(self):
        rides = (await self.store.snapshot(self.graph)).rides

        async def check(ride):
            try:
                await self._authorize(ride.agent_id, ride.vehicle_id, TravelMode.ROVER, rover=True)
            except StationError as exc:
                return ride.ride_id, "denied" if exc.status_code in {
                    401,
                    403,
                    404,
                } else "unavailable"
            return ride.ride_id, "allowed"

        return dict(
            await asyncio.gather(
                *(check(ride) for ride in rides if ride.state not in RIDE_TERMINAL)
            )
        )

    def _advance_rovers(self, world, now, blocked, unavailable, authorization):
        self._rover_unavailable = unavailable
        for ride_id, ride in world.rides.items():
            if ride.state in RIDE_TERMINAL:
                continue
            vehicle = world.vehicles[ride.vehicle_id]
            decision = authorization.get(ride_id, "unavailable")
            if decision == "denied" or now >= ride.deadline_ms:
                self._stop_ride(
                    world,
                    ride,
                    now,
                    "navigation_not_authorized"
                    if decision == "denied"
                    else "rover_deadline_exceeded",
                )
                continue
            if decision != "allowed":
                # No elapsed-time catch-up after authority returns.
                world.vehicles[vehicle.vehicle_id] = vehicle.model_copy(update={"updated_ms": now})
                continue
            if ride.state is RideState.APPROACHING:
                agent = world.records[ride.approach_command_id]
                if agent.state is NavigationState.ARRIVED:
                    world.rides[ride_id] = ride.model_copy(
                        update={"state": RideState.READY_TO_BOARD, "updated_ms": now}
                    )
                elif agent.state in {NavigationState.CANCELED, NavigationState.UNREACHABLE}:
                    self._stop_ride(world, ride, now, "rover_approach_failed")
            elif ride.state is RideState.TRAVELING:
                result = self._advance_one(
                    vehicle, world.leases, self._world_occupancy(world), now, blocked, unavailable
                )
                world.vehicles[vehicle.vehicle_id] = result
                if result.state is NavigationState.ARRIVED:
                    world.rides[ride_id] = ride.model_copy(
                        update={"state": RideState.ARRIVED, "updated_ms": now}
                    )
                elif result.state in {NavigationState.UNREACHABLE, NavigationState.CANCELED}:
                    self._release(world.leases, vehicle.command_id)
                    world.rides[ride_id] = ride.model_copy(
                        update={
                            "state": RideState.STOPPED,
                            "updated_ms": now,
                            "reason": result.reason,
                        }
                    )
