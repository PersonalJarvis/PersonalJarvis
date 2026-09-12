"""Single-owner durable navigation journal and separate physical reservations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import aiosqlite
from filelock import FileLock, Timeout
from pydantic import TypeAdapter

from .models import Identity, StationError
from .navigation import NavigationGraph
from .navigation_models import (
    NAVIGATION_TERMINAL,
    MoveCommand,
    NavigationLease,
    NavigationRecord,
    NavigationSnapshot,
    NavigationState,
)

_IDENTITY = TypeAdapter(Identity)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS navigation_meta (
    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, seq INTEGER NOT NULL
);
INSERT OR IGNORE INTO navigation_meta VALUES(1,1,0);
CREATE TABLE IF NOT EXISTS navigation_commands (
    ordinal INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT UNIQUE NOT NULL,
    agent_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN
        ('queueing','moving','temporarily_blocked','rerouting','arrived','unreachable','canceled')),
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS navigation_agent ON navigation_commands(agent_id, ordinal);
CREATE TABLE IF NOT EXISTS navigation_leases (
    resource_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES navigation_commands(command_id),
    record TEXT NOT NULL
);
"""


class MarsNavigationStore:
    """Construction does no I/O; history preserves replay identity across restarts.

    The working set and snapshots are bounded, while durable command receipts
    remain on disk so an old retry cannot start a second journey.
    """

    def __init__(
        self,
        path: Path,
        *,
        clock_ms: Callable[[], int] | None = None,
        queue_limit: int = 64,
        actor_limit: int = 256,
    ) -> None:
        if not 1 <= queue_limit <= 64 or not 1 <= actor_limit <= 256:
            raise ValueError("invalid navigation queue limit")
        self.path = Path(path).resolve()
        self.clock = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.queue_limit = queue_limit
        self.actor_limit = actor_limit
        self._lock = asyncio.Lock()
        self._conn: aiosqlite.Connection | None = None
        self._owner: FileLock | None = None

    async def open(self) -> None:
        async with self._lock:
            if self._conn is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            owner = FileLock(str(self.path) + ".owner.lock", thread_local=False)
            try:
                owner.acquire(timeout=0)
            except Timeout as exc:
                raise StationError("navigation_owned_by_another_process", 503) from exc
            conn = None
            try:
                conn = await aiosqlite.connect(self.path, isolation_level=None)
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA synchronous=FULL")
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.executescript(_SCHEMA)
                row = await self._one(conn, "SELECT version FROM navigation_meta WHERE id=1")
                if row[0] != 1:
                    raise StationError("unsupported_navigation_storage_version", 503)
                self._conn, self._owner = conn, owner
            except BaseException:
                if conn is not None:
                    await conn.close()
                owner.release()
                raise

    async def close(self) -> None:
        async with self._lock:
            try:
                if self._conn is not None:
                    await self._conn.close()
            finally:
                self._conn = None
                if self._owner is not None:
                    self._owner.release()
                    self._owner = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            if self._conn is None:
                raise StationError("navigation_not_started", 503)
            conn = self._conn
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    @staticmethod
    async def _one(conn: aiosqlite.Connection, sql: str, args: tuple = ()):
        async with conn.execute(sql, args) as cursor:
            return await cursor.fetchone()

    @staticmethod
    def identity(agent_id: str, request: MoveCommand) -> tuple[str, str]:
        _IDENTITY.validate_python(agent_id)
        command_id = "mars-move:" + str(
            uuid5(NAMESPACE_URL, json.dumps([agent_id, request.request_id]))
        )
        fingerprint = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        return command_id, fingerprint

    async def duplicate(self, agent_id: str, request: MoveCommand) -> NavigationRecord | None:
        command_id, fingerprint = self.identity(agent_id, request)
        async with self.transaction() as conn:
            row = await self._one(
                conn, "SELECT * FROM navigation_commands WHERE command_id=?", (command_id,)
            )
            if row is None:
                return None
            if row["fingerprint"] != fingerprint:
                raise StationError("idempotency_payload_mismatch", 409)
            return NavigationRecord.model_validate_json(row["record"])

    async def accept(
        self, agent_id: str, request: MoveCommand, graph: NavigationGraph, *, deadline_ms: int
    ) -> NavigationRecord:
        command_id, fingerprint = self.identity(agent_id, request)
        async with self.transaction() as conn:
            duplicate = await self._one(
                conn, "SELECT * FROM navigation_commands WHERE command_id=?", (command_id,)
            )
            if duplicate is not None:
                if duplicate["fingerprint"] != fingerprint:
                    raise StationError("idempotency_payload_mismatch", 409)
                return NavigationRecord.model_validate_json(duplicate["record"])
            active = await self._one(
                conn,
                "SELECT COUNT(*) FROM navigation_commands "
                "WHERE state NOT IN ('arrived','unreachable','canceled')",
            )
            if active[0] >= self.queue_limit:
                raise StationError("navigation_queue_full", 429)
            previous = await self._one(
                conn,
                "SELECT record FROM navigation_commands WHERE agent_id=? "
                "ORDER BY ordinal DESC LIMIT 1",
                (agent_id,),
            )
            prior = (
                NavigationRecord.model_validate_json(previous[0]) if previous is not None else None
            )
            if prior is None:
                actors = await self._one(
                    conn, "SELECT COUNT(DISTINCT agent_id) FROM navigation_commands"
                )
                if actors[0] >= self.actor_limit:
                    raise StationError("navigation_actor_limit_reached", 429)
            if prior is not None and prior.state not in NAVIGATION_TERMINAL:
                raise StationError("agent_already_moving", 409)
            if prior is not None and prior.graph_signature != graph.signature:
                raise StationError("navigation_location_graph_changed", 409)
            # A queued edge is only intent. Only an actually entered segment
            # carries physical progress into a new request after cancellation.
            transit = (
                prior
                if prior is not None
                and prior.presence == "placed"
                and prior.edge_id is not None
                and 0 < prior.edge_progress < 1
                else None
            )
            if transit is not None and transit.mode is not request.mode:
                raise StationError("navigation_mode_change_in_transit", 409)
            now = self.clock()
            record = NavigationRecord(
                command_id=command_id,
                request_id=request.request_id,
                agent_id=agent_id,
                trace_id=str(uuid4()),
                layout_version=graph.layout_version,
                graph_version=graph.version,
                graph_signature=graph.signature,
                station_id=request.station_id,
                mode=request.mode,
                state=NavigationState.QUEUEING,
                position=prior.position if prior else graph.nodes[graph.spawn_node],
                current_node=prior.current_node if prior else graph.spawn_node,
                edge_id=transit.edge_id if transit else None,
                next_node=transit.next_node if transit else None,
                edge_progress=transit.edge_progress if transit else 0,
                placement="persisted" if prior else "canonical_spawn",
                presence=prior.presence if prior else "spawn_queue",
                created_ms=now,
                updated_ms=now,
                last_progress_ms=now,
                deadline_ms=now + deadline_ms,
            )
            if prior is not None:
                await conn.execute(
                    "DELETE FROM navigation_leases WHERE command_id=?", (prior.command_id,)
                )
            await conn.execute(
                "INSERT INTO navigation_commands(command_id,agent_id,fingerprint,state,record) "
                "VALUES(?,?,?,?,?)",
                (command_id, agent_id, fingerprint, record.state.value, record.model_dump_json()),
            )
            await conn.execute("UPDATE navigation_meta SET seq=seq+1 WHERE id=1")
            return record

    async def get(self, command_id: str) -> NavigationRecord:
        async with self.transaction() as conn:
            row = await self._one(
                conn, "SELECT record FROM navigation_commands WHERE command_id=?", (command_id,)
            )
            if row is None:
                raise StationError("navigation_command_not_found", 404)
            return NavigationRecord.model_validate_json(row[0])

    async def mutate(
        self,
        operation: Callable[[dict[str, NavigationRecord], dict[str, NavigationLease], int], None],
        *,
        include_id: str | None = None,
    ) -> None:
        """Commit bounded travel state and all its physical leases atomically."""
        async with self.transaction() as conn:
            async with conn.execute(
                "SELECT command_id,record FROM navigation_commands "
                "WHERE state NOT IN ('arrived','unreachable','canceled') "
                "OR command_id IN (SELECT command_id FROM navigation_leases) "
                "OR ordinal IN (SELECT MAX(ordinal) FROM navigation_commands GROUP BY agent_id) "
                "OR command_id=? ORDER BY ordinal",
                (include_id,),
            ) as cursor:
                records = {
                    row[0]: NavigationRecord.model_validate_json(row[1])
                    for row in await cursor.fetchall()
                }
            async with conn.execute("SELECT resource_id,record FROM navigation_leases") as cursor:
                leases = {
                    row[0]: NavigationLease.model_validate_json(row[1])
                    for row in await cursor.fetchall()
                }
            before = (records.copy(), leases.copy())
            operation(records, leases, self.clock())
            if before == (records, leases):
                return
            for record in records.values():
                await conn.execute(
                    "UPDATE navigation_commands SET state=?,record=? WHERE command_id=?",
                    (record.state.value, record.model_dump_json(), record.command_id),
                )
            await conn.execute("DELETE FROM navigation_leases")
            await conn.executemany(
                "INSERT INTO navigation_leases(resource_id,command_id,record) VALUES(?,?,?)",
                [
                    (lease.resource_id, lease.command_id, lease.model_dump_json())
                    for lease in leases.values()
                ],
            )
            await conn.execute("UPDATE navigation_meta SET seq=seq+1 WHERE id=1")

    async def snapshot(self, graph: NavigationGraph) -> NavigationSnapshot:
        async with self.transaction() as conn:
            seq = await self._one(conn, "SELECT seq FROM navigation_meta WHERE id=1")
            async with conn.execute(
                "SELECT record FROM navigation_commands "
                "WHERE state NOT IN ('arrived','unreachable','canceled') "
                "OR command_id IN (SELECT command_id FROM navigation_leases) OR ordinal IN "
                "(SELECT ordinal FROM navigation_commands ORDER BY ordinal DESC LIMIT 64) "
                "OR ordinal IN (SELECT MAX(ordinal) FROM navigation_commands GROUP BY agent_id) "
                "ORDER BY ordinal"
            ) as cursor:
                records = tuple(
                    NavigationRecord.model_validate_json(row[0]) for row in await cursor.fetchall()
                )
            async with conn.execute(
                "SELECT record FROM navigation_leases ORDER BY resource_id"
            ) as cursor:
                leases = tuple(
                    NavigationLease.model_validate_json(row[0]) for row in await cursor.fetchall()
                )
            return NavigationSnapshot(
                graph_version=graph.version,
                graph_signature=graph.signature,
                seq=seq[0],
                commands=records,
                leases=leases,
                occupancies=graph.occupancies(list(records)),
            )
