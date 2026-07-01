"""TaskStore — aiosqlite-based persistence for the task queue (ADR-0003).

The store is deliberately dumb: no scheduling, no event dispatch. Just
CRUD + transactional state transitions + startup cleanup.

Two tables:
- ``tasks``      — main row per scheduled task (TaskSpec as a JSON blob).
- ``task_steps`` — append-only step log (the runner writes observation/action/
  verify/log lines here, the UI renders them as a timeline).

Pattern: identical to ``jarvis/memory/recall.py`` (``aiosqlite`` + ``ensure_open``
lazy-init so the store instance can be constructed synchronously).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .schema import TaskSpec, TaskState

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

# Rebuild target for the legacy trigger_type-CHECK migration. Mirrors the
# `tasks` table in schema.sql but as `tasks_new` and with `every` in the
# CHECK. Kept here (not in schema.sql) because it only runs during migration.
_TASKS_REBUILD_SQL = """
CREATE TABLE tasks_new (
    id              TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    spec_json       TEXT NOT NULL,
    state           TEXT NOT NULL CHECK(state IN (
                        'pending','scheduled','running','completed',
                        'failed','cancelled','interrupted')),
    trigger_type    TEXT NOT NULL CHECK(trigger_type IN (
                        'after_delay','at_time','on_event','every')),
    due_at_ns       INTEGER,
    event_selector  TEXT,
    title           TEXT NOT NULL DEFAULT '',
    created_at_ns   INTEGER NOT NULL,
    started_at_ns   INTEGER,
    finished_at_ns  INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    result_json     TEXT
)
"""


class TaskStore:
    """CRUD store for tasks + steps on the shared memory DB.

    The DB file is NOT initialized by this class as far as the memory
    schema is concerned — ``RecallStore`` handles that. We can still call
    init() independently, though, because our schema is additive and
    idempotent (``CREATE TABLE IF NOT EXISTS``).
    """

    name: str = "sqlite-tasks"

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Opens the DB connection + runs the additive schema."""
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        # PRAGMAs: WAL + busy_timeout come from the memory schema, but on a
        # still-empty DB we set them here too, just to be safe.
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        schema = SCHEMA_FILE.read_text(encoding="utf-8")
        await self._conn.executescript(schema)
        # Migrate legacy DBs whose trigger_type CHECK predates `every`
        # (added 2026-06-17). CREATE TABLE IF NOT EXISTS never alters an
        # existing table, so this explicit migration is required.
        await self._migrate_trigger_type_check()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> TaskStore:
        await self.init()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError(
                "TaskStore not initialized — call init() or use 'async with'."
            )
        return self._conn

    async def _migrate_trigger_type_check(self) -> None:
        """Rebuild ``tasks`` if its trigger_type CHECK predates ``every``.

        SQLite cannot ALTER a CHECK constraint, so we do the standard
        create-copy-drop-rename dance. Guarded to run at most once: it is a
        no-op once the live CHECK already mentions ``'every'`` (or has no
        CHECK at all).
        """
        conn = self._require_conn()
        cur = await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return
        table_sql = row["sql"] or ""
        if "trigger_type" not in table_sql or "CHECK" not in table_sql:
            return  # loose schema — nothing to migrate
        if "'every'" in table_sql:
            return  # fresh schema or already migrated

        await conn.execute("PRAGMA foreign_keys = OFF")
        try:
            await conn.execute("BEGIN")
            await conn.execute(_TASKS_REBUILD_SQL)
            await conn.execute(
                "INSERT INTO tasks_new SELECT id, trace_id, spec_json, state, "
                "trigger_type, due_at_ns, event_selector, title, created_at_ns, "
                "started_at_ns, finished_at_ns, attempts, last_error, result_json "
                "FROM tasks"
            )
            await conn.execute("DROP TABLE tasks")
            await conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise
        finally:
            await conn.execute("PRAGMA foreign_keys = ON")

        # Indexes were dropped with the old table — recreate them.
        await conn.executescript(
            "CREATE INDEX IF NOT EXISTS idx_tasks_state_due ON tasks(state, due_at_ns);"
            "CREATE INDEX IF NOT EXISTS idx_tasks_trace ON tasks(trace_id);"
            "CREATE INDEX IF NOT EXISTS idx_tasks_event_sel ON tasks(event_selector);"
        )

    async def set_next_due(self, task_id: str, due_at_ns: int) -> None:
        """Update only the ``due_at_ns`` column — used by the scheduler to
        re-arm a recurring (``every``) task for its next interval.
        """
        conn = self._require_conn()
        await conn.execute(
            "UPDATE tasks SET due_at_ns = ? WHERE id = ?", (due_at_ns, task_id)
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_trigger_fields(spec: TaskSpec) -> tuple[str, int | None, str | None]:
        """Extracts ``(trigger_type, due_at_ns, event_selector)`` from a spec.

        ``due_at_ns`` is UTC nanoseconds; the scheduler compares it against
        ``time.time_ns()``.
        """
        trig = spec.trigger
        if trig.type == "after_delay":
            due = time.time_ns() + int(trig.delay_seconds * 1e9)
            return "after_delay", due, None
        if trig.type == "at_time":
            # Parsing responsibility lives with the scheduler (ISO-8601 + TZ).
            # Here just a fallback: if parsing succeeds, we use it.
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(trig.iso_timestamp.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.astimezone()
                due = int(dt.timestamp() * 1e9)
            except ValueError:
                due = 0
            return "at_time", due, None
        if trig.type == "on_event":
            return "on_event", None, trig.event_name
        if trig.type == "every":
            # Recurring: anchor to start_at if given, else now + interval.
            if trig.start_at:
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(trig.start_at.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.astimezone()
                    due = int(dt.timestamp() * 1e9)
                except ValueError:
                    due = time.time_ns() + int(trig.interval_seconds * 1e9)
            else:
                due = time.time_ns() + int(trig.interval_seconds * 1e9)
            return "every", due, None
        raise ValueError(f"Unknown trigger type: {trig.type}")  # pragma: no cover

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def insert(self, spec: TaskSpec, *, trace_id: str | None = None) -> str:
        """Creates a new task with ``state='scheduled'``.

        Returns the task ID (str). ``trace_id`` may optionally be passed in;
        otherwise the spec ID is used as the trace (one task = one trace, as
        long as no other scope overrides it).
        """
        conn = self._require_conn()
        trigger_type, due_at_ns, event_selector = self._compute_trigger_fields(spec)
        created_at_ns = spec.created_at_ns or time.time_ns()
        tid = str(spec.id)
        trace = trace_id or tid

        spec_json = spec.model_dump_json()

        await conn.execute(
            """
            INSERT INTO tasks (id, trace_id, spec_json, state, trigger_type,
                               due_at_ns, event_selector, title,
                               created_at_ns, attempts)
            VALUES (?, ?, ?, 'scheduled', ?, ?, ?, ?, ?, 0)
            """,
            (tid, trace, spec_json, trigger_type, due_at_ns, event_selector,
             spec.title, created_at_ns),
        )
        return tid

    async def update_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        increment_attempts: bool = False,
    ) -> None:
        """Transitions to a new state, atomically, with optional error/result info.

        Automatically sets ``started_at_ns`` on the transition to ``running``
        and ``finished_at_ns`` on terminal states.
        """
        conn = self._require_conn()
        now_ns = time.time_ns()
        sets = ["state = ?"]
        params: list[Any] = [state]

        if state == "running":
            sets.append("started_at_ns = ?")
            params.append(now_ns)
        if state in ("completed", "failed", "cancelled", "interrupted"):
            sets.append("finished_at_ns = ?")
            params.append(now_ns)
        if error is not None:
            sets.append("last_error = ?")
            params.append(error)
        if result is not None:
            sets.append("result_json = ?")
            params.append(json.dumps(result, ensure_ascii=False))
        if increment_attempts:
            sets.append("attempts = attempts + 1")

        params.append(task_id)
        # `sets` is a whitelist of column assignments (built statically
        # above) — no user input flows into the SQL string.
        sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?"  # noqa: S608
        await conn.execute(sql, tuple(params))

    async def append_step(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> int:
        """Appends a step to ``task_steps``. Returns the new ``seq``.

        ``kind`` is ``'observation' | 'action' | 'verify' | 'log'``. No hard
        DB constraint, so the runner can also record other kinds (e.g.
        'retry').
        """
        conn = self._require_conn()
        cur = await conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM task_steps WHERE task_id = ?",
            (task_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        seq = int(row["max_seq"]) + 1 if row else 1
        await conn.execute(
            """
            INSERT INTO task_steps (task_id, seq, kind, payload_json, timestamp_ns)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, seq, kind, json.dumps(payload, ensure_ascii=False), time.time_ns()),
        )
        return seq

    async def list(
        self,
        state_filter: str | list[str] | None = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Returns a list of task rows (without steps), filtered by state."""
        conn = self._require_conn()
        sql = (
            "SELECT id, trace_id, state, trigger_type, due_at_ns, title, "
            "created_at_ns, started_at_ns, finished_at_ns, attempts, last_error "
            "FROM tasks"
        )
        params: list[Any] = []
        if state_filter is not None:
            if isinstance(state_filter, str):
                sql += " WHERE state = ?"
                params.append(state_filter)
            else:
                placeholders = ",".join(["?"] * len(state_filter))
                sql += f" WHERE state IN ({placeholders})"
                params.extend(state_filter)
        sql += " ORDER BY created_at_ns DESC LIMIT ?"
        params.append(limit)
        cur = await conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def get(self, task_id: str) -> dict[str, Any] | None:
        """Returns the full task (incl. steps), or None."""
        conn = self._require_conn()
        cur = await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        task = dict(row)

        cur = await conn.execute(
            "SELECT seq, kind, payload_json, timestamp_ns FROM task_steps "
            "WHERE task_id = ? ORDER BY seq ASC",
            (task_id,),
        )
        step_rows = await cur.fetchall()
        await cur.close()
        task["steps"] = [
            {
                "seq": int(r["seq"]),
                "kind": str(r["kind"]),
                "payload": json.loads(r["payload_json"]),
                "timestamp_ns": int(r["timestamp_ns"]),
            }
            for r in step_rows
        ]
        return task

    async def get_spec(self, task_id: str) -> TaskSpec | None:
        """Deserializes ``spec_json`` back into a TaskSpec."""
        conn = self._require_conn()
        cur = await conn.execute(
            "SELECT spec_json FROM tasks WHERE id = ?", (task_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        return TaskSpec.model_validate_json(row["spec_json"])

    async def delete(self, task_id: str) -> bool:
        """Removes the task + steps (via ON DELETE CASCADE). Returns whether a row was hit."""
        conn = self._require_conn()
        cur = await conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        rowcount = cur.rowcount
        await cur.close()
        return rowcount > 0

    async def all_pending_scheduled(self) -> list[dict[str, Any]]:
        """For scheduler hydration: all tasks in state ``scheduled``."""
        return await self.list(state_filter="scheduled", limit=10_000)

    async def cleanup_interrupted(self) -> int:
        """Startup cleanup: all ``running`` → ``interrupted`` with an error log.

        Returns the number of affected tasks. Per ADR-0003 this must be
        called at app start, before the scheduler hydrates.
        """
        conn = self._require_conn()
        now_ns = time.time_ns()
        cur = await conn.execute(
            """
            UPDATE tasks
            SET state = 'interrupted',
                finished_at_ns = ?,
                last_error = 'App exit detected'
            WHERE state = 'running'
            """,
            (now_ns,),
        )
        rowcount = cur.rowcount
        await cur.close()
        return int(rowcount or 0)
