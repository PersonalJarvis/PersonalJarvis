"""No-client logical travel controller; never dispatches or completes real work."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from .models import StationError
from .navigation import NavigationGraph
from .navigation_models import (
    NAVIGATION_TERMINAL,
    MoveCommand,
    NavigationLease,
    NavigationRecord,
    NavigationSnapshot,
    NavigationState,
    TravelMode,
)
from .navigation_store import MarsNavigationStore

_LOG = logging.getLogger(__name__)
NavigationAuthorizer = Callable[[str, str, TravelMode], Awaitable[bool]]


class MarsNavigationService:
    """Host calls advance independently of renderers, sockets, and task workers.

    Missing ticks catch up at most five seconds, checking each crossed edge.
    Process downtime is paused travel, with exact saved placement retained and
    reservations reacquired on restart. A render chunk has no effect on travel;
    player collision/asset readiness remains a separate frontend obligation.
    Expiring leases reserve intended resources; they never erase a body. Latest
    persisted actor placements continue occupying nodes, station slots and edges
    after arrival, cancellation, process loss, and lease expiry. New actors wait
    in an explicit logical spawn queue until they can enter a clear route.
    These reservations never obstruct remote communication-draft tasks.
    """

    def __init__(
        self,
        store: MarsNavigationStore,
        definition: dict[str, Any],
        *,
        authorize: NavigationAuthorizer,
        lease_ms: int = 10_000,
        deadline_ms: int = 600_000,
        retry_interval_ms: int = 2_000,
        max_retries: int = 5,
        max_advance_ms: int = 5_000,
        authorization_timeout_s: float = 10,
    ) -> None:
        if not (
            1_000 <= lease_ms <= 60_000
            and 1_000 <= deadline_ms <= 1_800_000
            and 100 <= retry_interval_ms <= 60_000
            and 1 <= max_retries <= 20
            and 1 <= max_advance_ms <= 5_000
            and 0 < authorization_timeout_s <= 30
        ):
            raise ValueError("invalid bounded navigation configuration")
        self.store = store
        self.graph = NavigationGraph(definition)
        self.authorize = authorize
        self.lease_ms = lease_ms
        self.deadline_ms = deadline_ms
        self.retry_interval_ms = retry_interval_ms
        self.max_retries = max_retries
        self.max_advance_ms = max_advance_ms
        self.authorization_timeout_s = authorization_timeout_s
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            await self.store.open()

            def recover(records, leases, now):
                leases.clear()
                for key, record in records.items():
                    if record.state not in NAVIGATION_TERMINAL:
                        records[key] = record.model_copy(
                            update={
                                "state": NavigationState.REROUTING,
                                "reason": "process_restarted",
                                "updated_ms": now,
                                "retry_at_ms": 0,
                            }
                        )

            try:
                await self.store.mutate(recover)
            except BaseException:
                await self.store.close()
                raise
            self._started = True

    async def close(self) -> None:
        async with self._lock:
            try:
                if self._started:
                    await self.store.mutate(lambda records, leases, now: leases.clear())
            finally:
                await self.store.close()
                self._started = False

    async def submit(self, agent_id: str, request: MoveCommand) -> NavigationRecord:
        async with self._lock:
            existing = await self.store.duplicate(agent_id, request)
            if existing is not None:
                return existing
            if (
                request.graph_version != self.graph.version
                or request.layout_version != self.graph.layout_version
            ):
                raise StationError("navigation_graph_version_mismatch", 409)
            if request.station_id not in self.graph.stations:
                raise StationError("unknown_navigation_station", 404)
            await self._authorize(agent_id, request.station_id, request.mode)
            return await self.store.accept(
                agent_id, request, self.graph, deadline_ms=self.deadline_ms
            )

    async def _authorize(self, agent_id: str, station_id: str, mode: TravelMode) -> None:
        try:
            allowed = await asyncio.wait_for(
                self.authorize(agent_id, station_id, mode),
                timeout=self.authorization_timeout_s,
            )
        except StationError:
            raise
        except Exception:
            # Do not expose callback/provider errors or caller identity in logs.
            _LOG.warning("Navigation authorization did not complete")
            raise StationError("navigation_authorization_unavailable", 503) from None
        if allowed is not True:
            raise StationError("navigation_not_authorized", 403)

    async def cancel(self, agent_id: str, command_id: str) -> NavigationRecord:
        async with self._lock:
            record = await self.store.get(command_id)
            if record.agent_id != agent_id:
                raise StationError("navigation_command_not_found", 404)

            def cancel(records, leases, now):
                current = records[command_id]
                if current.state not in {NavigationState.CANCELED, NavigationState.UNREACHABLE}:
                    records[command_id] = current.model_copy(
                        update={
                            "state": NavigationState.CANCELED,
                            "reason": "user_canceled",
                            "updated_ms": now,
                        }
                    )
                self._release(leases, command_id)

            await self.store.mutate(cancel, include_id=command_id)
            return await self.store.get(command_id)

    async def snapshot(self) -> NavigationSnapshot:
        return await self.store.snapshot(self.graph)

    @staticmethod
    def _release(leases: dict[str, NavigationLease], command_id: str) -> None:
        for key in [key for key, lease in leases.items() if lease.command_id == command_id]:
            del leases[key]

    @staticmethod
    def _available(leases, occupancy, resource_id: str, record: NavigationRecord) -> bool:
        if any(
            body.resource_id == resource_id and body.agent_id != record.agent_id
            for body in occupancy
        ):
            return False
        lease = leases.get(resource_id)
        return lease is None or lease.agent_id == record.agent_id

    def _claim(
        self, leases, occupancy, resource_id: str, record: NavigationRecord, now: int
    ) -> bool:
        if not self._available(leases, occupancy, resource_id, record):
            return False
        leases[resource_id] = NavigationLease(
            resource_id=resource_id,
            command_id=record.command_id,
            agent_id=record.agent_id,
            expires_ms=now + self.lease_ms,
        )
        return True

    def _blocked(self, record: NavigationRecord, now: int, reason: str) -> NavigationRecord:
        retries = record.retries + (now >= record.retry_at_ms)
        state = NavigationState.TEMPORARILY_BLOCKED
        if retries >= self.max_retries:
            state, reason = NavigationState.UNREACHABLE, "navigation_retry_exhausted"
        return record.model_copy(
            update={
                "state": state,
                "reason": reason,
                "updated_ms": now,
                "retries": retries,
                "retry_at_ms": now + self.retry_interval_ms
                if now >= record.retry_at_ms
                else record.retry_at_ms,
            }
        )

    async def advance(
        self, *, blocked_edges: Iterable[str] = (), unavailable_stations: Iterable[str] = ()
    ) -> None:
        """Advance authoritative state in FIFO order against host-known obstructions.

        Inputs describe logical obstructions, never client visibility or asset
        loading. The host retains these inputs while an obstruction is present.
        """
        blocked, unavailable = frozenset(blocked_edges), frozenset(unavailable_stations)
        if not blocked <= self.graph.edges.keys() or not unavailable <= self.graph.stations.keys():
            raise StationError("unknown_navigation_obstruction", 422)
        async with self._lock:
            candidates = [
                record
                for record in (await self.store.snapshot(self.graph)).commands
                if record.state not in NAVIGATION_TERMINAL
            ]

            async def reauthorize(record):
                try:
                    await self._authorize(record.agent_id, record.station_id, record.mode)
                except StationError as exc:
                    return record.command_id, (
                        "denied" if exc.status_code in {401, 403, 404} else "unavailable"
                    )
                return record.command_id, "allowed"

            # At most 64 candidates; one unavailable authority cannot make the
            # controller await 64 sequential ten-second timeouts.
            authorization = dict(await asyncio.gather(*(reauthorize(row) for row in candidates)))

            def step(records, leases, now):
                for key in [key for key, lease in leases.items() if lease.expires_ms <= now]:
                    del leases[key]
                # Store supplies insertion order, so station/segment contention is FIFO.
                for key, record in records.items():
                    if record.state in NAVIGATION_TERMINAL:
                        continue
                    decision = authorization.get(key, "unavailable")
                    if decision == "denied":
                        result = record.model_copy(
                            update={
                                "state": NavigationState.CANCELED,
                                "reason": "navigation_not_authorized",
                                "updated_ms": now,
                            }
                        )
                        self._release(leases, key)
                    elif decision == "unavailable":
                        result = self._blocked(record, now, "navigation_authorization_unavailable")
                        self._release(leases, key)
                    else:
                        occupancy = self.graph.occupancies(list(records.values()))
                        result = self._advance_one(
                            record, leases, occupancy, now, blocked, unavailable
                        )
                    records[key] = result
                    if result.state in {NavigationState.UNREACHABLE, NavigationState.CANCELED}:
                        self._release(leases, key)

            await self.store.mutate(step)

    def _advance_one(
        self, record, leases, occupancy, now, blocked, unavailable
    ) -> NavigationRecord:
        if record.graph_signature != self.graph.signature:
            return record.model_copy(
                update={
                    "state": NavigationState.UNREACHABLE,
                    "reason": "navigation_graph_changed",
                    "updated_ms": now,
                }
            )
        if now >= record.deadline_ms:
            return record.model_copy(
                update={
                    "state": NavigationState.UNREACHABLE,
                    "reason": "navigation_deadline_exceeded",
                    "updated_ms": now,
                }
            )
        if record.station_id in unavailable:
            self._release(leases, record.command_id)
            return self._blocked(record, now, "destination_temporarily_unavailable")
        if not self._claim(leases, occupancy, "station:" + record.station_id, record, now):
            return record.model_copy(
                update={
                    "state": NavigationState.QUEUEING,
                    "reason": "physical_station_occupied",
                    "updated_ms": now,
                }
            )
        if record.edge_id and record.edge_id in blocked:
            return self._blocked(record, now, "route_temporarily_blocked")
        if record.presence == "spawn_queue" and not self._available(
            leases, occupancy, "node:" + record.current_node, record
        ):
            return record.model_copy(
                update={
                    "state": NavigationState.QUEUEING,
                    "reason": "physical_spawn_occupied",
                    "updated_ms": now,
                }
            )
        start = record.next_node if record.edge_id else record.current_node
        path = self.graph.path(start, record.station_id, record.mode, blocked)
        if path is None:
            self._release(leases, record.command_id)
            if self.graph.path(start, record.station_id, record.mode) is None:
                return record.model_copy(
                    update={
                        "state": NavigationState.UNREACHABLE,
                        "reason": "navigation_route_unreachable",
                        "updated_ms": now,
                    }
                )
            return self._blocked(record, now, "route_temporarily_blocked")
        if (
            record.path
            and any(edge in blocked for edge in record.path)
            and record.state is not NavigationState.REROUTING
        ):
            return record.model_copy(
                update={
                    "state": NavigationState.REROUTING,
                    "reason": "route_changed",
                    "path": path,
                    "updated_ms": now,
                }
            )
        seconds = min(max(now - record.updated_ms, 0), self.max_advance_ms) / 1000
        distance = seconds * (1.8 if record.mode is TravelMode.PEDESTRIAN else 4.0)
        # A gap cannot turn a parked/queued body into unchecked motion.
        if (
            record.state is NavigationState.QUEUEING
            and record.reason == "physical_station_occupied"
        ):
            distance = 0
        current = record.model_copy(
            update={"path": path, "updated_ms": now, "reason": "", "state": NavigationState.MOVING}
        )
        for _ in range(len(self.graph.edges) + 1):
            if current.edge_id is None:
                if current.current_node == self.graph.stations[current.station_id]:
                    if not self._claim(
                        leases, occupancy, "node:" + current.current_node, current, now
                    ):
                        return current.model_copy(
                            update={
                                "state": NavigationState.QUEUEING,
                                "reason": "physical_destination_occupied",
                            }
                        )
                    return current.model_copy(
                        update={
                            "state": NavigationState.ARRIVED,
                            "path": (),
                            "reason": "physical_destination_reached",
                            "presence": "placed",
                        }
                    )
                if not current.path:
                    return self._blocked(current, now, "route_temporarily_blocked")
                edge_id = current.path[0]
                current = current.model_copy(
                    update={
                        "edge_id": edge_id,
                        "next_node": self.graph.destination(edge_id, current.current_node),
                        "edge_progress": 0,
                        "path": current.path[1:],
                    }
                )
            resource_id = "edge:" + current.edge_id
            destination_id = "node:" + current.next_node
            if not all(
                self._available(leases, occupancy, resource, current)
                for resource in (
                    resource_id,
                    destination_id,
                )
            ):
                # A waiter outside an edge must not reserve it against a body
                # that needs that edge to leave the occupied endpoint.
                if current.edge_progress == 0:
                    lease = leases.get(resource_id)
                    if lease is not None and lease.agent_id == current.agent_id:
                        del leases[resource_id]
                return current.model_copy(
                    update={"state": NavigationState.QUEUEING, "reason": "physical_route_occupied"}
                )
            self._claim(leases, occupancy, resource_id, current, now)
            self._claim(leases, occupancy, destination_id, current, now)
            edge = self.graph.edges[current.edge_id]
            remaining = edge.length * (1 - current.edge_progress)
            moved = min(distance, remaining)
            fraction = min(1.0, current.edge_progress + moved / edge.length)
            current = current.model_copy(
                update={
                    "edge_progress": fraction,
                    "position": self.graph.position(
                        current.current_node, current.next_node, fraction
                    ),
                    "last_progress_ms": now if moved > 0 else current.last_progress_ms,
                    "retries": 0 if moved > 0 else current.retries,
                    "retry_at_ms": 0 if moved > 0 else current.retry_at_ms,
                    "presence": "placed" if moved > 0 else current.presence,
                }
            )
            if moved > 0:
                departed_node = "node:" + current.current_node
                lease = leases.get(departed_node)
                if lease is not None and lease.agent_id == current.agent_id:
                    del leases[departed_node]
            if fraction < 1:
                return current
            del leases[resource_id]
            distance -= moved
            current = current.model_copy(
                update={
                    "current_node": current.next_node,
                    "edge_id": None,
                    "next_node": None,
                    "edge_progress": 0,
                }
            )
        raise StationError("navigation_step_limit_exceeded", 503)
