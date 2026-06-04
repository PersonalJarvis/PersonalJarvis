"""SQLiteMemoryStore — schema-isolated persistence for skillbook rules, traces, and KG.

All tables are namespaced with the ``skb_`` prefix (DoD-4). Concurrent access is
serialized via an asyncio lock around a single connection (single-instance use).
JSON columns use SQLite's json1 extension (built into stdlib sqlite3 since
Python 3.10) for indexed-key lookup without table reshape.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .models import Entity, Relation, Rule, TraceStep

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skb_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skb_rules (
    id TEXT PRIMARY KEY,
    trigger_json TEXT NOT NULL,
    strategy_json TEXT NOT NULL,
    source_peer TEXT NOT NULL,
    created_at_ns INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    evidence TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS skb_rules_actor_idx
    ON skb_rules (json_extract(trigger_json, '$.actor'));
CREATE INDEX IF NOT EXISTS skb_rules_deleted_idx
    ON skb_rules (deleted);

CREATE TABLE IF NOT EXISTS skb_traces (
    task_id TEXT NOT NULL,
    step_idx INTEGER NOT NULL,
    actor TEXT NOT NULL,
    params_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    status TEXT NOT NULL,
    ts_ns INTEGER NOT NULL,
    PRIMARY KEY (task_id, step_idx)
);
CREATE INDEX IF NOT EXISTS skb_traces_task_idx ON skb_traces (task_id);

CREATE TABLE IF NOT EXISTS skb_entities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    attrs_json TEXT NOT NULL,
    valid_from_ns INTEGER NOT NULL,
    valid_to_ns INTEGER
);
CREATE INDEX IF NOT EXISTS skb_entities_kind_idx ON skb_entities (kind);

CREATE TABLE IF NOT EXISTS skb_relations (
    id TEXT PRIMARY KEY,
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    attrs_json TEXT NOT NULL,
    valid_from_ns INTEGER NOT NULL,
    valid_to_ns INTEGER
);
CREATE INDEX IF NOT EXISTS skb_relations_src_idx ON skb_relations (src_id);
"""


@runtime_checkable
class MemoryStore(Protocol):
    """Public interface used by ace_core, guardrails, p2p_sync."""

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def put_rule(self, rule: Rule) -> None: ...
    async def query_rules(
        self,
        *,
        actor: str | None = None,
        include_tombstones: bool = False,
    ) -> list[Rule]: ...
    async def tombstone_rule(self, rule_id: str) -> None: ...
    async def put_trace_step(self, step: TraceStep) -> None: ...
    async def query_trace_steps(self, *, task_id: str) -> list[TraceStep]: ...


class SQLiteMemoryStore:
    """Single-file SQLite-backed implementation of MemoryStore.

    Connection is opened lazily by :meth:`open` and serialized by a single
    asyncio lock; SQLite's own serialization guarantees row-level consistency
    on top.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    async def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        def _connect() -> sqlite3.Connection:
            # check_same_thread=False is safe here: every method acquires
            # self._lock before touching the connection, so SQLite never sees
            # concurrent use even though asyncio.to_thread dispatches across
            # the default ThreadPoolExecutor.
            conn = sqlite3.connect(
                self._db_path, isolation_level=None, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            return conn

        self._conn = await asyncio.to_thread(_connect)

    async def close(self) -> None:
        if self._conn is None:
            return
        conn = self._conn
        self._conn = None
        await asyncio.to_thread(conn.close)

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteMemoryStore.open() must be awaited before use")
        return self._conn

    async def list_tables(self) -> list[str]:
        conn = self._require_conn()

        def _q() -> list[str]:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            return [r["name"] for r in rows]

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def put_rule(self, rule: Rule) -> None:
        conn = self._require_conn()

        def _w() -> None:
            conn.execute(
                """INSERT INTO skb_rules
                   (id, trigger_json, strategy_json, source_peer, created_at_ns,
                    priority, deleted, evidence)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     trigger_json=excluded.trigger_json,
                     strategy_json=excluded.strategy_json,
                     source_peer=excluded.source_peer,
                     created_at_ns=excluded.created_at_ns,
                     priority=excluded.priority,
                     deleted=excluded.deleted,
                     evidence=excluded.evidence""",
                (
                    rule.id,
                    json.dumps(rule.trigger),
                    json.dumps(rule.strategy),
                    rule.source_peer,
                    rule.created_at_ns,
                    rule.priority,
                    1 if rule.deleted else 0,
                    rule.evidence,
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_w)

    async def query_rules(
        self,
        *,
        actor: str | None = None,
        include_tombstones: bool = False,
    ) -> list[Rule]:
        conn = self._require_conn()

        sql = "SELECT * FROM skb_rules"
        params: list[Any] = []
        wheres: list[str] = []
        if actor is not None:
            wheres.append("json_extract(trigger_json, '$.actor') = ?")
            params.append(actor)
        if not include_tombstones:
            wheres.append("deleted = 0")
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY priority DESC, created_at_ns ASC"

        def _q() -> list[Rule]:
            rows = conn.execute(sql, params).fetchall()
            return [
                Rule(
                    id=r["id"],
                    trigger=json.loads(r["trigger_json"]),
                    strategy=json.loads(r["strategy_json"]),
                    source_peer=r["source_peer"],
                    created_at_ns=r["created_at_ns"],
                    priority=r["priority"],
                    deleted=bool(r["deleted"]),
                    evidence=r["evidence"],
                )
                for r in rows
            ]

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def tombstone_rule(self, rule_id: str) -> None:
        conn = self._require_conn()

        def _w() -> None:
            conn.execute("UPDATE skb_rules SET deleted = 1 WHERE id = ?", (rule_id,))

        async with self._lock:
            await asyncio.to_thread(_w)

    async def put_trace_step(self, step: TraceStep) -> None:
        conn = self._require_conn()

        def _w() -> None:
            conn.execute(
                """INSERT INTO skb_traces
                   (task_id, step_idx, actor, params_json, result_json, status, ts_ns)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(task_id, step_idx) DO UPDATE SET
                     actor=excluded.actor,
                     params_json=excluded.params_json,
                     result_json=excluded.result_json,
                     status=excluded.status,
                     ts_ns=excluded.ts_ns""",
                (
                    step.task_id,
                    step.step_idx,
                    step.actor,
                    json.dumps(step.params),
                    json.dumps(step.result),
                    step.status,
                    step.ts_ns,
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_w)

    async def query_trace_steps(self, *, task_id: str) -> list[TraceStep]:
        conn = self._require_conn()

        def _q() -> list[TraceStep]:
            rows = conn.execute(
                "SELECT * FROM skb_traces WHERE task_id = ? ORDER BY step_idx ASC",
                (task_id,),
            ).fetchall()
            return [
                TraceStep(
                    task_id=r["task_id"],
                    step_idx=r["step_idx"],
                    actor=r["actor"],
                    params=json.loads(r["params_json"]),
                    result=json.loads(r["result_json"]),
                    status=r["status"],
                    ts_ns=r["ts_ns"],
                )
                for r in rows
            ]

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def put_entity(self, ent: Entity) -> None:
        conn = self._require_conn()

        def _w() -> None:
            conn.execute(
                """INSERT INTO skb_entities
                   (id, kind, attrs_json, valid_from_ns, valid_to_ns)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     kind=excluded.kind,
                     attrs_json=excluded.attrs_json,
                     valid_from_ns=excluded.valid_from_ns,
                     valid_to_ns=excluded.valid_to_ns""",
                (
                    ent.id,
                    ent.kind,
                    json.dumps(ent.attrs),
                    ent.valid_from_ns,
                    ent.valid_to_ns,
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_w)

    async def query_entities(self, *, kind: str | None = None) -> list[Entity]:
        conn = self._require_conn()
        sql = "SELECT * FROM skb_entities"
        params: list[Any] = []
        if kind is not None:
            sql += " WHERE kind = ?"
            params.append(kind)

        def _q() -> list[Entity]:
            rows = conn.execute(sql, params).fetchall()
            return [
                Entity(
                    id=r["id"],
                    kind=r["kind"],
                    attrs=json.loads(r["attrs_json"]),
                    valid_from_ns=r["valid_from_ns"],
                    valid_to_ns=r["valid_to_ns"],
                )
                for r in rows
            ]

        async with self._lock:
            return await asyncio.to_thread(_q)

    async def put_relation(self, rel: Relation) -> None:
        conn = self._require_conn()

        def _w() -> None:
            conn.execute(
                """INSERT INTO skb_relations
                   (id, src_id, dst_id, kind, attrs_json, valid_from_ns, valid_to_ns)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     src_id=excluded.src_id,
                     dst_id=excluded.dst_id,
                     kind=excluded.kind,
                     attrs_json=excluded.attrs_json,
                     valid_from_ns=excluded.valid_from_ns,
                     valid_to_ns=excluded.valid_to_ns""",
                (
                    rel.id,
                    rel.src_id,
                    rel.dst_id,
                    rel.kind,
                    json.dumps(rel.attrs),
                    rel.valid_from_ns,
                    rel.valid_to_ns,
                ),
            )

        async with self._lock:
            await asyncio.to_thread(_w)

    async def query_relations(
        self,
        *,
        src_id: str | None = None,
        kind: str | None = None,
    ) -> list[Relation]:
        conn = self._require_conn()
        sql = "SELECT * FROM skb_relations"
        clauses: list[str] = []
        params: list[Any] = []
        if src_id is not None:
            clauses.append("src_id = ?")
            params.append(src_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY valid_from_ns ASC"

        def _q() -> list[Relation]:
            rows = conn.execute(sql, params).fetchall()
            return [
                Relation(
                    id=r["id"],
                    src_id=r["src_id"],
                    dst_id=r["dst_id"],
                    kind=r["kind"],
                    attrs=json.loads(r["attrs_json"]),
                    valid_from_ns=r["valid_from_ns"],
                    valid_to_ns=r["valid_to_ns"],
                )
                for r in rows
            ]

        async with self._lock:
            return await asyncio.to_thread(_q)
