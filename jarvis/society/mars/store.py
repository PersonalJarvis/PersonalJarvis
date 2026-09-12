"""Single-owner SQLite station journal with atomic state/event commits and fencing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import aiosqlite
from filelock import FileLock, Timeout
from pydantic import TypeAdapter

from .models import (
    SCHEMA_VERSION,
    STATION_ID,
    TERMINAL_STATES,
    WORLD_ID,
    CommandRecord,
    CommandState,
    ExecutionReceipt,
    Identity,
    StationCommand,
    StationError,
    StationEvent,
    StationEventBatch,
    StationLease,
    StationSnapshot,
    reject_draft_credentials,
)

_IDENTITY = TypeAdapter(Identity)


class MarsStore:
    """Lazy store: constructing it opens neither database nor process lock.

    The lock lives as long as the connection. Other processes must use HTTP;
    they must not take over a live owner's lease or start another controller.
    """

    def __init__(
        self,
        path: Path,
        *,
        clock_ms: Callable[[], int] | None = None,
        lease_ms: int = 30_000,
        queue_limit: int = 64,
        retain_events: int = 2000,
    ) -> None:
        if not 1 <= queue_limit <= 64 or lease_ms < 100 or retain_events < 1:
            raise ValueError("invalid bounded station store configuration")
        self.path = Path(path).resolve()
        self._clock = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._lease_ms = lease_ms
        self._queue_limit = queue_limit
        self._retain_events = retain_events
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
                raise StationError("world_owned_by_another_process", 503) from exc
            conn: aiosqlite.Connection | None = None
            try:
                conn = await aiosqlite.connect(self.path, isolation_level=None)
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA synchronous=FULL")
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.executescript(Path(__file__).with_name("schema.sql").read_text("utf-8"))
                row = await self._one(
                    conn, "SELECT value FROM mars_meta WHERE key='schema_version'"
                )
                if row is not None and row[0] != SCHEMA_VERSION:
                    raise StationError("unsupported_storage_version", 503)
                await conn.execute(
                    "INSERT OR IGNORE INTO mars_meta(key,value) VALUES ('schema_version',?)",
                    (SCHEMA_VERSION,),
                )
                await conn.execute(
                    "INSERT OR IGNORE INTO mars_station(station_id) VALUES (?)", (STATION_ID,)
                )
                # Taking ownership fences old callbacks, but never blindly re-dispatches work.
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    await self._interrupt_owned(conn, "process_interrupted")
                    await conn.commit()
                except BaseException:
                    await conn.rollback()
                    raise
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
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            conn = self._conn
            if conn is None:
                raise StationError("world_not_started", 503)
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
    def _record(row: aiosqlite.Row) -> CommandRecord:
        fields = dict(row)
        fields.pop("fingerprint")
        fields["state"] = CommandState(fields["state"])
        fields["cancel_requested"] = bool(fields["cancel_requested"])
        fields["cancel_dispatched"] = bool(fields["cancel_dispatched"])
        return CommandRecord(**fields)

    async def _command(self, conn: aiosqlite.Connection, command_id: str) -> CommandRecord:
        row = await self._one(conn, "SELECT * FROM mars_commands WHERE command_id=?", (command_id,))
        if row is None:
            raise StationError("command_not_found", 404)
        return self._record(row)

    async def _event(self, conn: aiosqlite.Connection, command_id: str) -> None:
        await conn.execute(
            "INSERT INTO mars_events(command_id,trace_id,agent_id,state,fence,task_ref,"
            "result_ref,reason,cancel_requested,ts_ms) SELECT command_id,trace_id,agent_id,"
            "state,fence,task_ref,result_ref,reason,cancel_requested,updated_ms "
            "FROM mars_commands WHERE command_id=?",
            (command_id,),
        )
        await conn.execute(
            "DELETE FROM mars_events WHERE seq <= (SELECT COALESCE(MAX(seq),0)-? FROM mars_events)",
            (self._retain_events,),
        )

    async def _interrupt_owned(self, conn: aiosqlite.Connection, reason: str) -> None:
        station = await self._one(
            conn, "SELECT * FROM mars_station WHERE station_id=?", (STATION_ID,)
        )
        if station is None or station["command_id"] is None:
            return
        command_id, generation = station["command_id"], station["generation"] + 1
        now = self._clock()
        await conn.execute(
            "UPDATE mars_station SET generation=?, expires_ms=? WHERE station_id=?",
            (generation, now + self._lease_ms, STATION_ID),
        )
        await conn.execute(
            "UPDATE mars_commands SET state='interrupted',fence=?,reason=?,updated_ms=? "
            "WHERE command_id=?",
            (generation, reason, now, command_id),
        )
        await self._event(conn, command_id)

    async def submit(self, agent_id: str, command: StationCommand) -> CommandRecord:
        reject_draft_credentials(command.draft)
        _IDENTITY.validate_python(agent_id, strict=True)
        # Immutable validated request; request_id is identity, not part of its content.
        content = command.model_dump(exclude={"request_id"})
        fingerprint = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        async with self._transaction() as conn:
            old = await self._one(
                conn,
                "SELECT * FROM mars_commands WHERE world_id=? AND agent_id=? AND request_id=?",
                (WORLD_ID, agent_id, command.request_id),
            )
            if old is not None:
                if old["fingerprint"] != fingerprint:
                    raise StationError("idempotency_payload_mismatch")
                return self._record(old)
            count = await self._one(
                conn,
                "SELECT COUNT(*) FROM mars_commands WHERE state NOT IN "
                "('completed','failed','canceled')",
            )
            if count is not None and count[0] >= self._queue_limit:
                raise StationError("station_queue_full", 429)
            command_id = f"mars-command:{uuid4()}"
            now = self._clock()
            await conn.execute(
                "INSERT INTO mars_commands(command_id,world_id,station_id,capability_id,"
                "agent_id,request_id,fingerprint,trace_id,draft,state,created_ms,updated_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?,'queued',?,?)",
                (
                    command_id,
                    WORLD_ID,
                    STATION_ID,
                    command.capability_id,
                    agent_id,
                    command.request_id,
                    fingerprint,
                    command_id,
                    command.draft,
                    now,
                    now,
                ),
            )
            await self._event(conn, command_id)
            return await self._command(conn, command_id)

    async def get(self, command_id: str, *, agent_id: str | None = None) -> CommandRecord:
        async with self._transaction() as conn:
            result = await self._command(conn, command_id)
            if agent_id is not None and result.agent_id != agent_id:
                raise StationError("command_not_found", 404)
            return result

    async def claim_next(self) -> CommandRecord | None:
        """Reserve the first queued command and commit dispatch intent before any call."""
        async with self._transaction() as conn:
            station = await self._one(
                conn, "SELECT * FROM mars_station WHERE station_id=?", (STATION_ID,)
            )
            if station["command_id"] is not None:
                return None
            row = await self._one(
                conn,
                "SELECT command_id FROM mars_commands WHERE state='queued' ORDER BY rowid LIMIT 1",
            )
            if row is None:
                return None
            command_id, fence, now = row[0], station["generation"] + 1, self._clock()
            await conn.execute(
                "UPDATE mars_station SET generation=?,command_id=?,expires_ms=? WHERE station_id=?",
                (fence, command_id, now + self._lease_ms, STATION_ID),
            )
            await conn.execute(
                "UPDATE mars_commands SET state='active',fence=?,updated_ms=?,"
                "reason='dispatch_started' "
                "WHERE command_id=?",
                (fence, now, command_id),
            )
            await self._event(conn, command_id)
            return await self._command(conn, command_id)

    async def current(self) -> CommandRecord | None:
        async with self._transaction() as conn:
            station = await self._one(
                conn, "SELECT * FROM mars_station WHERE station_id=?", (STATION_ID,)
            )
            if station["command_id"] is None:
                return None
            if station["expires_ms"] <= self._clock():
                await self._interrupt_owned(conn, "lease_expired")
            return await self._command(conn, station["command_id"])

    async def apply(
        self, command_id: str, fence: int, receipt: ExecutionReceipt, *, reason: str = ""
    ) -> CommandRecord:
        """Only the current server-side fence may acknowledge an execution outcome."""
        async with self._transaction() as conn:
            command = await self._command(conn, command_id)
            station = await self._one(
                conn, "SELECT * FROM mars_station WHERE station_id=?", (STATION_ID,)
            )
            if command.state in TERMINAL_STATES:
                if command.fence == fence and command.state is receipt.state:
                    return command
                raise StationError("stale_station_lease")
            if command.task_ref and receipt.task_ref and command.task_ref != receipt.task_ref:
                raise StationError("task_reference_mismatch")
            if station["command_id"] != command_id or station["generation"] != fence:
                raise StationError("stale_station_lease")
            if station["expires_ms"] <= self._clock():
                # Do not accept a late worker result. The next reconcile fences it.
                raise StationError("stale_station_lease")
            now = self._clock()
            task_ref, result_ref = (
                receipt.task_ref or command.task_ref,
                receipt.result_ref or command.result_ref,
            )
            cancel_dispatched = command.cancel_dispatched
            if receipt.cancel_attempt == "not_attempted":
                cancel_dispatched = False
            changed = (
                command.state,
                command.task_ref,
                command.result_ref,
                command.reason,
                command.cancel_dispatched,
            ) != (
                receipt.state,
                task_ref,
                result_ref,
                reason,
                cancel_dispatched,
            )
            await conn.execute(
                "UPDATE mars_commands SET state=?,task_ref=?,result_ref=?,reason=?,updated_ms=?,"
                "cancel_dispatched=? "
                "WHERE command_id=?",
                (
                    str(receipt.state),
                    task_ref,
                    result_ref,
                    reason,
                    now,
                    int(cancel_dispatched),
                    command_id,
                ),
            )
            if receipt.state in TERMINAL_STATES:
                await conn.execute(
                    "UPDATE mars_station SET command_id=NULL,expires_ms=0 WHERE station_id=?",
                    (STATION_ID,),
                )
            else:
                # An unknown external outcome keeps the exclusive resource until reconciled.
                await conn.execute(
                    "UPDATE mars_station SET expires_ms=? WHERE station_id=?",
                    (now + self._lease_ms, STATION_ID),
                )
            if changed:
                await self._event(conn, command_id)
            return await self._command(conn, command_id)

    async def request_cancel(self, agent_id: str, command_id: str) -> CommandRecord:
        async with self._transaction() as conn:
            command = await self._command(conn, command_id)
            if command.agent_id != agent_id:
                raise StationError("command_not_found", 404)
            if command.state in TERMINAL_STATES or command.cancel_requested:
                return command
            state = CommandState.CANCELED if command.state is CommandState.QUEUED else command.state
            await conn.execute(
                "UPDATE mars_commands SET state=?,cancel_requested=1,"
                "reason='cancel_requested',updated_ms=? "
                "WHERE command_id=?",
                (str(state), self._clock(), command_id),
            )
            await self._event(conn, command_id)
            return await self._command(conn, command_id)

    async def begin_cancel(self, command_id: str, fence: int) -> CommandRecord:
        """Persist cancellation intent once; an uncertain response is inspected, not repeated."""
        async with self._transaction() as conn:
            command = await self._command(conn, command_id)
            station = await self._one(
                conn, "SELECT * FROM mars_station WHERE station_id=?", (STATION_ID,)
            )
            if station["command_id"] != command_id or station["generation"] != fence:
                raise StationError("stale_station_lease")
            if not command.cancel_requested:
                raise StationError("cancellation_not_requested")
            if command.cancel_dispatched:
                return command
            await conn.execute(
                "UPDATE mars_commands SET cancel_dispatched=1,reason='cancel_pending',updated_ms=? "
                "WHERE command_id=?",
                (self._clock(), command_id),
            )
            await self._event(conn, command_id)
            return await self._command(conn, command_id)

    async def snapshot(self) -> StationSnapshot:
        async with self._transaction() as conn:
            seq = await self._one(conn, "SELECT COALESCE(MAX(seq),0) FROM mars_events")
            # All pending records (at most 64), plus bounded newest terminal history.
            async with conn.execute(
                "SELECT * FROM mars_commands ORDER BY "
                "(state IN ('completed','failed','canceled')) ASC, rowid DESC LIMIT 100"
            ) as cursor:
                commands = tuple(self._record(row) for row in await cursor.fetchall())
            station = await self._one(
                conn, "SELECT * FROM mars_station WHERE station_id=?", (STATION_ID,)
            )
            lease = None
            if station["command_id"] is not None:
                owner = await self._command(conn, station["command_id"])
                lease = StationLease(
                    command_id=owner.command_id,
                    agent_id=owner.agent_id,
                    fence=station["generation"],
                    expires_ms=station["expires_ms"],
                )
            return StationSnapshot(seq=seq[0], commands=commands, lease=lease)

    async def events(self, after_seq: int, *, limit: int = 100) -> StationEventBatch:
        if type(after_seq) is not int or after_seq < 0 or type(limit) is not int or limit < 1:
            raise StationError("invalid_event_cursor", 422)
        limit = min(limit, 250)
        async with self._transaction() as conn:
            bounds = await self._one(
                conn, "SELECT COALESCE(MIN(seq),0),COALESCE(MAX(seq),0) FROM mars_events"
            )
            oldest, latest = bounds
            resync = after_seq > latest or (oldest > 0 and after_seq < oldest - 1)
            records = []
            if not resync:
                async with conn.execute(
                    "SELECT * FROM mars_events WHERE seq>? ORDER BY seq LIMIT ?", (after_seq, limit)
                ) as cursor:
                    for row in await cursor.fetchall():
                        fields = dict(row)
                        fields["state"] = CommandState(fields["state"])
                        fields["cancel_requested"] = bool(fields["cancel_requested"])
                        records.append(StationEvent(**fields))
            delivered = records[-1].seq if records else after_seq
            return StationEventBatch(
                events=tuple(records),
                next_cursor=delivered,
                latest_seq=latest,
                has_more=not resync and delivered < latest,
                resync_required=resync,
            )
