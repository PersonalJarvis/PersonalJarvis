"""SQLite WAL event store for mission events.

Reuses the Phase-5 pattern from `jarvis/tasks/store.py:48-60`:
aiosqlite with `isolation_level=None`, `PRAGMA journal_mode=WAL`,
`synchronous=NORMAL`, `busy_timeout=5000`, `executescript()` migrations.

Dedicated DB file `data/missions.db` (see ADR-0009 §"SQLite strategy",
subagent report 3 recommendation) — separate lifecycle from `data/jarvis.db`.

Persist-before-publish atomicity (ADR-0009 Decision §4 + Risk #9/#10):
`append_and_publish()` first INSERTs with RETURNING seq, then publishes to the bus.
Crash in between = INSERT is persisted; recovery finds the event on startup.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

from .event_bus import MissionBus
from .events import EventEnvelope

log = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "missions_schema.sql"


class MissionEventStore:
    """SQLite WAL event store with append-and-publish atomicity."""

    def __init__(self, db_path: Path, bus: MissionBus) -> None:
        self._db_path = db_path
        self._bus = bus
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open the DB, set PRAGMAs, load schema (idempotent) + run migrations."""
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(
            self._db_path,
            isolation_level=None,  # autocommit — WAL ist Lock-Manager
        )
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        await conn.executescript(schema_sql)
        self._conn = conn
        await self._apply_migrations()

    async def _apply_migrations(self) -> None:
        """Idempotent column migrations for existing databases.

        SQLite has no `ADD COLUMN IF NOT EXISTS` — we read pragma_table_info
        and append only missing columns. Phase-3 columns (`iteration`,
        `cost_usd`) are already present in the CREATE statement; this is
        the upgrade path for pre-Phase-3 DBs from blocks 1/2.
        """
        # Read existing column names.
        cur = await self.conn.execute("PRAGMA table_info(missions)")
        rows = await cur.fetchall()
        await cur.close()
        existing_cols = {str(row[1]) for row in rows}

        if "iteration" not in existing_cols:
            await self.conn.execute(
                "ALTER TABLE missions ADD COLUMN iteration INTEGER NOT NULL DEFAULT 0"
            )
            log.info("MissionEventStore: migration applied — added 'iteration'")
        if "cost_usd" not in existing_cols:
            await self.conn.execute(
                "ALTER TABLE missions ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0.0"
            )
            log.info("MissionEventStore: migration applied — added 'cost_usd'")
        if "last_heartbeat_ms" not in existing_cols:
            await self.conn.execute(
                "ALTER TABLE missions ADD COLUMN last_heartbeat_ms INTEGER NOT NULL DEFAULT 0"
            )
            log.info("MissionEventStore: migration applied — added 'last_heartbeat_ms'")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("MissionEventStore: open() vor Verwendung aufrufen")
        return self._conn

    # --- Append & Publish (atomar im WAL-Sinne) ---

    async def append_and_publish(self, envelope: EventEnvelope) -> int:
        """Persist event, then publish to bus. Returns the assigned seq.

        Crash behaviour: WAL + synchronous=NORMAL flushes the INSERT to disk;
        bus publish is lost. Recovery on startup reads
        `events_since(last_known_seq)` and re-broadcasts.

        Strict: `envelope.seq` MUST be None — seq is server-assigned.
        """
        if envelope.seq is not None:
            raise ValueError(
                "append_and_publish: envelope.seq muss None sein (server-assigned)"
            )

        payload_json = envelope.payload.model_dump_json()
        cur = await self.conn.execute(
            """
            INSERT INTO mission_events
                (event_id, mission_id, event_type, parent_event_id,
                 worker_id, source_actor, ts_ms, schema_version, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING seq
            """,
            (
                envelope.event_id,
                envelope.mission_id,
                envelope.payload.event_type,
                envelope.parent_event_id,
                envelope.worker_id,
                envelope.source_actor,
                envelope.ts_ms,
                envelope.schema_version,
                payload_json,
            ),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise RuntimeError("INSERT ... RETURNING seq lieferte keinen Wert")
        seq = int(row[0])

        envelope_with_seq = envelope.model_copy(update={"seq": seq})
        await self._bus.publish(envelope_with_seq)
        return seq

    # --- Read-API ---

    async def events_since(self, after_seq: int = 0) -> list[EventEnvelope]:
        """All events with `seq > after_seq`, sorted ascending by seq."""
        cur = await self.conn.execute(
            """
            SELECT seq, event_id, mission_id, event_type, parent_event_id,
                   worker_id, source_actor, ts_ms, schema_version, payload_json
            FROM mission_events
            WHERE seq > ?
            ORDER BY seq ASC
            """,
            (after_seq,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [self._row_to_envelope(row) for row in rows]

    async def events_for_mission(self, mission_id: str) -> list[EventEnvelope]:
        """All events for a mission, sorted ascending by seq."""
        cur = await self.conn.execute(
            """
            SELECT seq, event_id, mission_id, event_type, parent_event_id,
                   worker_id, source_actor, ts_ms, schema_version, payload_json
            FROM mission_events
            WHERE mission_id = ?
            ORDER BY seq ASC
            """,
            (mission_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [self._row_to_envelope(row) for row in rows]

    # --- Mission header (separate, for recovery + UI snapshots) ---

    async def upsert_mission(
        self,
        *,
        mission_id: str,
        prompt: str,
        state: str,
        language: str,
        ts_ms: int,
        iteration: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Create a mission header or update state + iteration + cost.

        On insert: all fields are written.
        On update (ON CONFLICT): state/updated_ms always; iteration/cost_usd
        only when the new value > current — we do not want to overwrite with
        smaller values (cost accumulates monotonically, iteration only upward).
        """
        await self.conn.execute(
            """
            INSERT INTO missions (
                id, prompt, state, language, created_ms, updated_ms,
                iteration, cost_usd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state = excluded.state,
                updated_ms = excluded.updated_ms,
                iteration = MAX(missions.iteration, excluded.iteration),
                cost_usd  = MAX(missions.cost_usd, excluded.cost_usd)
            """,
            (
                mission_id,
                prompt,
                state,
                language,
                ts_ms,
                ts_ms,
                iteration,
                cost_usd,
            ),
        )

    async def get_mission_view(
        self, mission_id: str
    ) -> tuple[str, str, str, int, float] | None:
        """Returns `(prompt, state, language, iteration, cost_usd)` or None.

        Convenience API for the Kontrollierer orchestrator (needs everything at once).
        The existing `get_mission_state()` remains unchanged — no breaking change.
        """
        cur = await self.conn.execute(
            "SELECT prompt, state, language, iteration, cost_usd FROM missions WHERE id = ?",
            (mission_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        return (str(row[0]), str(row[1]), str(row[2]), int(row[3]), float(row[4]))

    async def list_missions(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List of mission headers. The Phase-4 UI endpoint uses this for
        ``GET /api/missions``. Sorted by created_ms desc (newest first).

        Args:
            state: Optional filter (e.g. ``"RUNNING"``).
            limit: Maximum number of entries (default 100).
        """
        sql = (
            "SELECT id, prompt, state, language, created_ms, updated_ms,"
            " iteration, cost_usd FROM missions"
        )
        params: tuple[Any, ...]
        if state is not None:
            sql += " WHERE state = ?"
            params = (state,)
        else:
            params = ()
        sql += " ORDER BY created_ms DESC LIMIT ?"
        params = params + (int(limit),)
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return [
            {
                "id": str(row[0]),
                "prompt": str(row[1]),
                "state": str(row[2]),
                "language": str(row[3]),
                "created_ms": int(row[4]),
                "updated_ms": int(row[5]),
                "iteration": int(row[6]),
                "cost_usd": float(row[7]),
            }
            for row in rows
        ]

    async def list_non_terminal_missions(self) -> list[tuple[str, str, str]]:
        """Returns `(mission_id, prompt, state)` for non-terminal missions.

        Recovery on startup uses this to find stale RUNNING missions.
        """
        cur = await self.conn.execute(
            """
            SELECT id, prompt, state FROM missions
            WHERE state NOT IN ('APPROVED', 'FAILED', 'CANCELLED', 'TIMED_OUT')
            ORDER BY created_ms ASC
            """
        )
        rows = await cur.fetchall()
        await cur.close()
        return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]

    async def get_mission_state(self, mission_id: str) -> str | None:
        cur = await self.conn.execute(
            "SELECT state FROM missions WHERE id = ?",
            (mission_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        return str(row[0]) if row is not None else None

    async def touch_heartbeat(self, mission_id: str, ts_ms: int) -> None:
        """Bump a mission's liveness heartbeat (header-only, NO event).

        A live orchestrator calls this periodically while a worker is draining
        so startup recovery can distinguish a busy-but-silent worker from a
        genuinely orphaned mission. Deliberately not an event: it must not bloat
        the event log or wake the flight-recorder wildcard subscriber.
        """
        await self.conn.execute(
            "UPDATE missions SET last_heartbeat_ms = ? WHERE id = ?",
            (ts_ms, mission_id),
        )

    async def get_heartbeat(self, mission_id: str) -> int:
        """Last heartbeat ms for a mission, or 0 if none/unknown."""
        cur = await self.conn.execute(
            "SELECT last_heartbeat_ms FROM missions WHERE id = ?", (mission_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row is not None and row[0] is not None else 0

    async def wal_checkpoint(self) -> None:
        """Manual WAL checkpoint (TRUNCATE mode)."""
        await self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # --- Helpers ---

    @staticmethod
    def _row_to_envelope(row: Any) -> EventEnvelope:
        (
            seq,
            event_id,
            mission_id,
            event_type,
            parent_event_id,
            worker_id,
            source_actor,
            ts_ms,
            schema_version,
            payload_json,
        ) = row
        payload_dict = json.loads(payload_json)
        # Redundantly ensure the discriminator field (model_dump_json already sets it)
        payload_dict.setdefault("event_type", event_type)
        return EventEnvelope.model_validate(
            {
                "event_id": event_id,
                "seq": seq,
                "mission_id": mission_id,
                "parent_event_id": parent_event_id,
                "worker_id": worker_id,
                "source_actor": source_actor,
                "ts_ms": ts_ms,
                "schema_version": schema_version,
                "payload": payload_dict,
            }
        )
