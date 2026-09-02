"""``society.db`` — the roster, the board, rooms, knowledge staging, approvals.

Same conventions as ``jarvis/missions/event_store.py``: aiosqlite in
autocommit with WAL as the lock manager, ``busy_timeout=5000``, idempotent
schema via ``executescript``, ``pragma_table_info`` migrations.

Persist-before-publish: :meth:`SocietyStore.append_and_publish` INSERTs with
``RETURNING seq`` and only then hands the envelope to the bus. A crash in
between loses the fan-out, never the row; startup replays ``events_since``.

Single writer: only the server process opens this file for writing. The CLI
and every other client go through REST.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

from .bus import SocietyBus
from .events import MsgType, SocietyEnvelope, now_ms

log = logging.getLogger(__name__)

__all__ = ["SocietyStore", "SCHEMA_VERSION"]

_SCHEMA_PATH = Path(__file__).parent / "society_schema.sql"
SCHEMA_VERSION = 1

_KEY_KILL_SWITCH = "kill_switch"
_KEY_SCHEMA_VERSION = "schema_version"

_EVENT_COLUMNS = (
    "seq, event_id, msg_type, from_agent, to_agent, trace_id, parent_event_id, "
    "ts_ms, cost_usd, payload_json"
)


def _row_to_envelope(row: Any) -> SocietyEnvelope:
    return SocietyEnvelope(
        seq=int(row[0]),
        event_id=str(row[1]),
        msg_type=MsgType(str(row[2])),
        from_agent=str(row[3]),
        to_agent=row[4],
        trace_id=str(row[5]),
        parent_event_id=row[6],
        ts_ms=int(row[7]),
        cost_usd=float(row[8]),
        payload=json.loads(row[9]) if row[9] else {},
    )


class SocietyStore:
    """SQLite WAL store with append-and-publish atomicity."""

    def __init__(self, db_path: Path, bus: SocietyBus | None = None) -> None:
        self._db_path = db_path
        self._bus = bus or SocietyBus()
        self._conn: aiosqlite.Connection | None = None

    @property
    def bus(self) -> SocietyBus:
        return self._bus

    @property
    def path(self) -> Path:
        return self._db_path

    # ------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._conn = conn
        await self._apply_migrations()
        await self._ensure_fts()
        await self.set_meta(_KEY_SCHEMA_VERSION, str(SCHEMA_VERSION))

    async def _apply_migrations(self) -> None:
        """Idempotent column migrations for databases created by older builds."""
        cur = await self.conn.execute("PRAGMA table_info(society_agents)")
        rows = await cur.fetchall()
        await cur.close()
        existing = {str(r[1]) for r in rows}
        if not existing:  # pragma: no cover — schema always creates the table
            raise RuntimeError("society_agents table missing after schema load")
        if "account_id" not in existing:
            await self.conn.execute(
                "ALTER TABLE society_agents ADD COLUMN account_id TEXT NOT NULL DEFAULT ''"
            )
            log.info("society store: migration applied — added account_id")
        if "browser_mode" not in existing:
            await self.conn.execute(
                "ALTER TABLE society_agents ADD COLUMN browser_mode TEXT NOT NULL "
                "DEFAULT 'own' CHECK (browser_mode IN ('own', 'attach'))"
            )
            log.info("society store: migration applied — added browser_mode")
        if "browser_allowed_domains_json" not in existing:
            await self.conn.execute(
                "ALTER TABLE society_agents ADD COLUMN browser_allowed_domains_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
            log.info("society store: migration applied — added browser_allowed_domains")

    async def _ensure_fts(self) -> None:
        """FTS5 over knowledge summaries; optional — a sqlite without FTS5 still
        runs the society, search then falls back to LIKE in the read path."""
        try:
            await self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5("
                "summary, wiki_path, content='knowledge', content_rowid='id')"
            )
        except Exception as exc:  # noqa: BLE001 — FTS5 is a build option of sqlite
            log.info("society store: FTS5 unavailable, knowledge search degrades (%s)", exc)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("SocietyStore: call open() before use")
        return self._conn

    # ---------------------------------------------------------------- meta

    async def get_meta(self, key: str, default: str = "") -> str:
        cur = await self.conn.execute("SELECT value FROM society_meta WHERE key = ?", (key,))
        row = await cur.fetchone()
        await cur.close()
        return str(row[0]) if row else default

    async def set_meta(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO society_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def kill_switch(self) -> bool:
        return (await self.get_meta(_KEY_KILL_SWITCH, "0")) == "1"

    async def set_kill_switch(self, engaged: bool) -> None:
        await self.set_meta(_KEY_KILL_SWITCH, "1" if engaged else "0")

    # -------------------------------------------------------------- events

    async def append_and_publish(self, envelope: SocietyEnvelope) -> SocietyEnvelope:
        """Persist, then publish. Returns the envelope with its ``seq``."""
        if envelope.seq is not None:
            raise ValueError("append_and_publish: envelope.seq must be None (server-assigned)")
        cur = await self.conn.execute(
            """
            INSERT INTO society_events
                (event_id, msg_type, from_agent, to_agent, trace_id, parent_event_id,
                 ts_ms, cost_usd, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING seq
            """,
            (
                envelope.event_id,
                str(envelope.msg_type),
                envelope.from_agent,
                envelope.to_agent,
                envelope.trace_id,
                envelope.parent_event_id,
                envelope.ts_ms,
                float(envelope.cost_usd),
                json.dumps(envelope.payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:  # pragma: no cover — sqlite always returns the seq
            raise RuntimeError("INSERT ... RETURNING seq returned no value")
        stored = envelope.model_copy(update={"seq": int(row[0])})
        await self._bus.publish(stored)
        return stored

    async def events_since(self, after_seq: int = 0, *, limit: int = 1000) -> list[SocietyEnvelope]:
        cur = await self.conn.execute(
            f"SELECT {_EVENT_COLUMNS} FROM society_events WHERE seq > ? "  # noqa: S608 — identifiers from a fixed allowlist, values bound
            "ORDER BY seq ASC LIMIT ?",
            (int(after_seq), int(limit)),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_envelope(r) for r in rows]

    async def events_for_trace(self, trace_id: str) -> list[SocietyEnvelope]:
        cur = await self.conn.execute(
            f"SELECT {_EVENT_COLUMNS} FROM society_events WHERE trace_id = ? ORDER BY seq ASC",  # noqa: S608 — identifiers from a fixed allowlist, values bound
            (trace_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_envelope(r) for r in rows]

    async def events_for_agent(
        self, agent_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[SocietyEnvelope]:
        """Everything sent by or to ``agent_id`` (broadcasts included)."""
        cur = await self.conn.execute(
            f"SELECT {_EVENT_COLUMNS} FROM society_events "  # noqa: S608 — identifiers from a fixed allowlist, values bound
            "WHERE seq > ? AND (from_agent = ? OR to_agent = ? OR to_agent IS NULL) "
            "ORDER BY seq DESC LIMIT ?",
            (int(after_seq), agent_id, agent_id, int(limit)),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_envelope(r) for r in reversed(list(rows))]

    async def inbox_for(
        self, agent_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[SocietyEnvelope]:
        """Envelopes addressed to ``agent_id`` (direct only), oldest first."""
        cur = await self.conn.execute(
            f"SELECT {_EVENT_COLUMNS} FROM society_events WHERE to_agent = ? AND seq > ? "  # noqa: S608 — identifiers from a fixed allowlist, values bound
            "ORDER BY seq ASC LIMIT ?",
            (agent_id, int(after_seq), int(limit)),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_envelope(r) for r in rows]

    async def last_seq(self) -> int:
        cur = await self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM society_events")
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row else 0

    async def count_in_trace(self, trace_id: str) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM society_events WHERE trace_id = ?", (trace_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row else 0

    async def cost_since(self, agent_id: str, since_ms: int) -> float:
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM society_events "
            "WHERE from_agent = ? AND ts_ms >= ?",
            (agent_id, int(since_ms)),
        )
        row = await cur.fetchone()
        await cur.close()
        return float(row[0]) if row else 0.0

    async def agent_stats(self, agent_id: str) -> dict[str, Any]:
        """Derived lifetime numbers: runs (RESULTs sent), cost, last activity."""
        cur = await self.conn.execute(
            "SELECT "
            "  SUM(CASE WHEN msg_type = 'RESULT' THEN 1 ELSE 0 END), "
            "  COALESCE(SUM(cost_usd), 0), MAX(ts_ms) "
            "FROM society_events WHERE from_agent = ?",
            (agent_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        runs, cost, last = row or (0, 0.0, None)
        return {
            "runs": int(runs or 0),
            "total_cost_usd": float(cost or 0.0),
            "last_active_ms": int(last) if last is not None else None,
        }

    # -------------------------------------------------------------- agents
    # Raw row access. Validation and the record type live in roster.py.

    async def insert_agent(self, row: dict[str, Any]) -> None:
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        await self.conn.execute(
            f"INSERT INTO society_agents ({cols}) VALUES ({marks})",  # noqa: S608 — identifiers from a fixed allowlist
            tuple(row.values()),
        )

    async def update_agent(self, agent_id: str, fields: dict[str, Any]) -> bool:
        if not fields:
            return False
        fields = dict(fields)
        fields["updated_ms"] = now_ms()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        cur = await self.conn.execute(
            f"UPDATE society_agents SET {assignments} WHERE agent_id = ?",  # noqa: S608 — identifiers from a fixed allowlist, values bound
            (*fields.values(), agent_id),
        )
        changed = cur.rowcount > 0
        await cur.close()
        return changed

    async def get_agent_row(self, agent_id: str) -> dict[str, Any] | None:
        self.conn.row_factory = aiosqlite.Row
        cur = await self.conn.execute(
            "SELECT * FROM society_agents WHERE agent_id = ?", (agent_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    async def get_agent_row_by_name(self, name: str) -> dict[str, Any] | None:
        self.conn.row_factory = aiosqlite.Row
        cur = await self.conn.execute(
            "SELECT * FROM society_agents WHERE lower(name) = lower(?)", (name,)
        )
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    async def list_agent_rows(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        self.conn.row_factory = aiosqlite.Row
        sql = "SELECT * FROM society_agents"
        if not include_archived:
            sql += " WHERE state != 'archived'"
        sql += " ORDER BY CASE tier WHEN 'lead' THEN 0 WHEN 'orchestrator' THEN 1 ELSE 2 END, name"
        cur = await self.conn.execute(sql)
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- rooms

    async def insert_room(self, row: dict[str, Any]) -> None:
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        await self.conn.execute(
            f"INSERT INTO society_rooms ({cols}) VALUES ({marks})",  # noqa: S608 — identifiers from a fixed allowlist
            tuple(row.values()),
        )

    async def update_room(self, room_id: str, fields: dict[str, Any]) -> None:
        fields = dict(fields)
        fields["updated_ms"] = now_ms()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE society_rooms SET {assignments} WHERE room_id = ?",  # noqa: S608 — identifiers from a fixed allowlist, values bound
            (*fields.values(), room_id),
        )

    async def get_room_row(self, room_id: str) -> dict[str, Any] | None:
        self.conn.row_factory = aiosqlite.Row
        cur = await self.conn.execute("SELECT * FROM society_rooms WHERE room_id = ?", (room_id,))
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    async def list_room_rows(self, *, state: str | None = None) -> list[dict[str, Any]]:
        self.conn.row_factory = aiosqlite.Row
        if state is None:
            cur = await self.conn.execute("SELECT * FROM society_rooms ORDER BY created_ms DESC")
        else:
            cur = await self.conn.execute(
                "SELECT * FROM society_rooms WHERE state = ? ORDER BY created_ms DESC", (state,)
            )
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------- approvals

    async def insert_approval(self, row: dict[str, Any]) -> None:
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        await self.conn.execute(
            f"INSERT INTO approvals ({cols}) VALUES ({marks})",  # noqa: S608 — identifiers from a fixed allowlist
            tuple(row.values()),
        )

    async def update_approval(self, approval_id: str, fields: dict[str, Any]) -> bool:
        if not fields:
            return False
        assignments = ", ".join(f"{k} = ?" for k in fields)
        cur = await self.conn.execute(
            f"UPDATE approvals SET {assignments} WHERE id = ?",  # noqa: S608 — identifiers from a fixed allowlist
            (*fields.values(), approval_id),
        )
        changed = cur.rowcount > 0
        await cur.close()
        return changed

    async def get_approval_row(self, approval_id: str) -> dict[str, Any] | None:
        self.conn.row_factory = aiosqlite.Row
        cur = await self.conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    async def list_approval_rows(
        self, *, state: str | None = None, agent_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        self.conn.row_factory = aiosqlite.Row
        clauses: list[str] = []
        params: list[Any] = []
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = await self.conn.execute(
            f"SELECT * FROM approvals{where} ORDER BY created_ms DESC LIMIT ?",  # noqa: S608 — identifiers from a fixed allowlist, values bound
            (*params, int(limit)),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------- knowledge

    # -------------------------------------------------------------- quests

    async def insert_quest(self, row: dict[str, Any]) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        await self.conn.execute(
            f"INSERT INTO society_quests ({cols}) VALUES ({marks})",  # noqa: S608 — identifiers from the caller's fixed dict, values bound
            tuple(row.values()),
        )

    async def update_quest(self, quest_id: str, fields: dict[str, Any]) -> bool:
        if not fields:
            return False
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur = await self.conn.execute(
            f"UPDATE society_quests SET {sets} WHERE quest_id = ?",  # noqa: S608 — identifiers from a fixed allowlist, values bound
            (*fields.values(), quest_id),
        )
        await cur.close()
        return bool(cur.rowcount)

    async def get_quest_row(self, quest_id: str) -> dict[str, Any] | None:
        self.conn.row_factory = aiosqlite.Row
        cur = await self.conn.execute(
            "SELECT * FROM society_quests WHERE quest_id = ?", (quest_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    async def get_quest_row_by_trace(self, trace_id: str) -> dict[str, Any] | None:
        self.conn.row_factory = aiosqlite.Row
        cur = await self.conn.execute(
            "SELECT * FROM society_quests WHERE trace_id = ?", (trace_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    async def list_quest_rows(
        self, *, state: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        self.conn.row_factory = aiosqlite.Row
        if state is not None:
            cur = await self.conn.execute(
                "SELECT * FROM society_quests WHERE state = ? ORDER BY created_ms DESC LIMIT ?",
                (state, int(limit)),
            )
        else:
            cur = await self.conn.execute(
                "SELECT * FROM society_quests ORDER BY created_ms DESC LIMIT ?", (int(limit),)
            )
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def insert_knowledge(self, row: dict[str, Any]) -> int:
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        cur = await self.conn.execute(
            f"INSERT INTO knowledge ({cols}) VALUES ({marks}) RETURNING id",  # noqa: S608 — identifiers from a fixed allowlist
            tuple(row.values()),
        )
        got = await cur.fetchone()
        await cur.close()
        new_id = int(got[0]) if got else 0
        try:
            await self.conn.execute(
                "INSERT INTO knowledge_fts (rowid, summary, wiki_path) VALUES (?, ?, ?)",
                (new_id, row.get("summary", ""), row.get("wiki_path", "")),
            )
        except Exception:  # noqa: BLE001 — FTS5 optional (see _ensure_fts)
            log.debug("society store: knowledge_fts insert skipped (no FTS5)")
        return new_id

    async def list_knowledge_rows(
        self, *, agent_id: str | None = None, reviewed: bool | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        self.conn.row_factory = aiosqlite.Row
        clauses: list[str] = []
        params: list[Any] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if reviewed is not None:
            clauses.append("reviewed = ?")
            params.append(1 if reviewed else 0)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = await self.conn.execute(
            f"SELECT * FROM knowledge{where} ORDER BY created_ms DESC LIMIT ?",  # noqa: S608 — identifiers from a fixed allowlist, values bound
            (*params, int(limit)),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def mark_knowledge_reviewed(self, knowledge_id: int) -> bool:
        cur = await self.conn.execute(
            "UPDATE knowledge SET reviewed = 1 WHERE id = ?", (int(knowledge_id),)
        )
        changed = cur.rowcount > 0
        await cur.close()
        return changed
