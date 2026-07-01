"""ConductorStore — aiosqlite persistence.

Pattern identical to ``jarvis.workflows.store``: lazy init via ``init()``,
context-manager support, additive schema via ``executescript``.

Default DB path: ``~/.conductor/conductor.sqlite`` — deliberately
separate from Jarvis, so Conductor also runs without a Jarvis install.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .schema import Job

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def default_db_path() -> Path:
    """``~/.conductor/conductor.sqlite`` — cross-platform."""
    return Path.home() / ".conductor" / "conductor.sqlite"


class ConductorStore:
    name: str = "conductor-sqlite"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        schema = SCHEMA_FILE.read_text(encoding="utf-8")
        await self._conn.executescript(schema)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> ConductorStore:
        await self.init()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError(
                "ConductorStore not initialized — call init() or "
                "use 'async with'."
            )
        return self._conn

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    async def upsert_job(self, job: Job) -> str:
        """Creates a job or overwrites an existing ID.

        Also writes denormalized fields (``type``, ``schedule_type``,
        ``schedule_expr``, ``webhook_token``) that the scheduler needs
        for fast queries.
        """
        conn = self._require_conn()
        spec = job.spec
        sched = job.schedule

        schedule_expr: str | None = None
        webhook_token: str | None = None
        if sched.type == "cron":
            schedule_expr = sched.expression
        elif sched.type == "interval":
            schedule_expr = str(sched.seconds)
        elif sched.type == "webhook":
            webhook_token = sched.token

        created = job.created_at_ns or time.time_ns()
        jid = str(job.id)

        await conn.execute(
            """
            INSERT INTO jobs
                (id, name, description, spec_json, schedule_json,
                 enabled, created_at_ns, tags_json,
                 type, schedule_type, schedule_expr, webhook_token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name           = excluded.name,
                description    = excluded.description,
                spec_json      = excluded.spec_json,
                schedule_json  = excluded.schedule_json,
                enabled        = excluded.enabled,
                tags_json      = excluded.tags_json,
                type           = excluded.type,
                schedule_type  = excluded.schedule_type,
                schedule_expr  = excluded.schedule_expr,
                webhook_token  = excluded.webhook_token
            """,
            (
                jid,
                job.name,
                job.description,
                spec.model_dump_json(),
                sched.model_dump_json(),
                1 if job.enabled else 0,
                created,
                json.dumps(list(job.tags), ensure_ascii=False),
                spec.type,
                sched.type,
                schedule_expr,
                webhook_token,
            ),
        )
        return jid

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        conn = self._require_conn()
        cur = await conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row is not None else None

    async def get_job_by_webhook_token(self, token: str) -> dict[str, Any] | None:
        conn = self._require_conn()
        cur = await conn.execute(
            "SELECT * FROM jobs WHERE webhook_token = ?", (token,)
        )
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row is not None else None

    async def list_jobs(self) -> list[dict[str, Any]]:
        conn = self._require_conn()
        cur = await conn.execute(
            "SELECT * FROM jobs ORDER BY created_at_ns DESC"
        )
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def set_enabled(self, job_id: str, enabled: bool) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE jobs SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, job_id),
        )

    async def set_next_run(self, job_id: str, next_at_ns: int | None) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE jobs SET next_run_at_ns = ? WHERE id = ?",
            (next_at_ns, job_id),
        )

    async def set_last_run(self, job_id: str, at_ns: int, state: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE jobs SET last_run_at_ns = ?, last_run_state = ? WHERE id = ?",
            (at_ns, state, job_id),
        )

    async def delete_job(self, job_id: str) -> bool:
        conn = self._require_conn()
        cur = await conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        rc = cur.rowcount
        await cur.close()
        return rc > 0

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def create_run(
        self,
        job_id: str,
        *,
        trigger: str,
        input_data: dict[str, Any] | None = None,
    ) -> str:
        from uuid import uuid4
        conn = self._require_conn()
        rid = str(uuid4())
        await conn.execute(
            """
            INSERT INTO runs (id, job_id, state, trigger, started_at_ns,
                              input_json)
            VALUES (?, ?, 'pending', ?, ?, ?)
            """,
            (
                rid,
                job_id,
                trigger,
                time.time_ns(),
                json.dumps(input_data or {}, ensure_ascii=False),
            ),
        )
        return rid

    async def update_run(
        self,
        run_id: str,
        *,
        state: str | None = None,
        exit_code: int | None = None,
        output: str | None = None,
        error: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        conn = self._require_conn()
        sets: list[str] = []
        params: list[Any] = []
        if state is not None:
            sets.append("state = ?")
            params.append(state)
            if state in ("completed", "failed", "cancelled"):
                sets.append("finished_at_ns = ?")
                params.append(time.time_ns())
        if exit_code is not None:
            sets.append("exit_code = ?")
            params.append(exit_code)
        if output is not None:
            sets.append("output = ?")
            params.append(output)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if metrics is not None:
            sets.append("metrics_json = ?")
            params.append(json.dumps(metrics, ensure_ascii=False))
        if not sets:
            return
        params.append(run_id)
        # ``sets`` is built statically from literal fragments — no
        # user input flows into the SQL string.
        sql = f"UPDATE runs SET {', '.join(sets)} WHERE id = ?"  # noqa: S608
        await conn.execute(sql, tuple(params))

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = self._require_conn()
        cur = await conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        run = dict(row)
        cur = await conn.execute(
            "SELECT seq, kind, label, started_at_ns, finished_at_ns, "
            "success, payload_json FROM run_steps "
            "WHERE run_id = ? ORDER BY seq ASC",
            (run_id,),
        )
        step_rows = await cur.fetchall()
        await cur.close()
        run["steps"] = [dict(r) for r in step_rows]
        return run

    async def list_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        conn = self._require_conn()
        if job_id:
            cur = await conn.execute(
                "SELECT * FROM runs WHERE job_id = ? "
                "ORDER BY started_at_ns DESC LIMIT ?",
                (job_id, limit),
            )
        else:
            cur = await conn.execute(
                "SELECT * FROM runs ORDER BY started_at_ns DESC LIMIT ?",
                (limit,),
            )
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def cleanup_interrupted_runs(self) -> int:
        """Startup: all running/pending → failed (app-exit detected)."""
        conn = self._require_conn()
        cur = await conn.execute(
            """
            UPDATE runs
            SET state = 'failed',
                finished_at_ns = ?,
                error = 'app-exit detected'
            WHERE state IN ('running', 'pending')
            """,
            (time.time_ns(),),
        )
        rc = cur.rowcount
        await cur.close()
        return int(rc or 0)
