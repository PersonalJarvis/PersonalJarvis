"""WorkflowStore — aiosqlite-basierte Persistenz.

Pattern identisch zu ``jarvis.tasks.store`` (Lazy-Init via ``init()``/
``ensure_open``). Drei Tabellen: ``workflows``, ``workflow_runs``,
``workflow_run_steps`` — siehe ``schema.sql``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .schema import WorkflowDef

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


class WorkflowStore:
    """CRUD fuer Workflows + Runs + Run-Steps."""

    name: str = "sqlite-workflows"

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
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

    async def __aenter__(self) -> WorkflowStore:
        await self.init()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError(
                "WorkflowStore nicht initialisiert — rufe init() oder nutze 'async with'."
            )
        return self._conn

    # ------------------------------------------------------------------
    # Workflow CRUD
    # ------------------------------------------------------------------

    async def upsert_workflow(self, wf: WorkflowDef) -> str:
        """Legt einen Workflow an oder ueberschreibt eine bestehende ID."""
        conn = self._require_conn()
        trig = wf.trigger
        trigger_type = trig.type
        cron_expr = getattr(trig, "expression", None) if trig.type == "cron" else None
        created = wf.created_at_ns or time.time_ns()
        wid = str(wf.id)

        await conn.execute(
            """
            INSERT INTO workflows
                (id, name, description, def_json, enabled, created_at_ns,
                 created_by, trigger_type, cron_expression)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name            = excluded.name,
                description     = excluded.description,
                def_json        = excluded.def_json,
                enabled         = excluded.enabled,
                trigger_type    = excluded.trigger_type,
                cron_expression = excluded.cron_expression
            """,
            (
                wid,
                wf.name,
                wf.description,
                wf.model_dump_json(),
                1 if wf.enabled else 0,
                created,
                wf.created_by,
                trigger_type,
                cron_expr,
            ),
        )
        return wid

    async def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        conn = self._require_conn()
        cur = await conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row is not None else None

    async def get_def(self, workflow_id: str) -> WorkflowDef | None:
        row = await self.get_workflow(workflow_id)
        if row is None:
            return None
        try:
            return WorkflowDef.model_validate_json(row["def_json"])
        except Exception:
            return None

    async def list_workflows(self) -> list[dict[str, Any]]:
        """Liefert alle Workflow-Rows (ohne Runs) — fuer das Dashboard."""
        conn = self._require_conn()
        cur = await conn.execute(
            "SELECT * FROM workflows ORDER BY created_at_ns ASC"
        )
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def set_enabled(self, workflow_id: str, enabled: bool) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE workflows SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, workflow_id),
        )

    async def set_next_run(self, workflow_id: str, next_run_at_ns: int | None) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE workflows SET next_run_at_ns = ? WHERE id = ?",
            (next_run_at_ns, workflow_id),
        )

    async def set_last_run(
        self, workflow_id: str, at_ns: int, state: str,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE workflows SET last_run_at_ns = ?, last_run_state = ? WHERE id = ?",
            (at_ns, state, workflow_id),
        )

    async def delete_workflow(self, workflow_id: str) -> bool:
        conn = self._require_conn()
        cur = await conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        rowcount = cur.rowcount
        await cur.close()
        return rowcount > 0

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def create_run(
        self,
        workflow_id: str,
        *,
        trigger: str,
        input_data: dict[str, Any] | None = None,
    ) -> str:
        """Legt einen Run (Status 'pending') an und returnt die Run-ID."""
        conn = self._require_conn()
        from uuid import uuid4
        run_id = str(uuid4())
        await conn.execute(
            """
            INSERT INTO workflow_runs
                (id, workflow_id, state, trigger, started_at_ns, input_json)
            VALUES (?, ?, 'pending', ?, ?, ?)
            """,
            (
                run_id,
                workflow_id,
                trigger,
                time.time_ns(),
                json.dumps(input_data or {}, ensure_ascii=False),
            ),
        )
        return run_id

    async def update_run_state(
        self,
        run_id: str,
        state: str,
        *,
        error: str | None = None,
    ) -> None:
        conn = self._require_conn()
        now = time.time_ns()
        if state in ("completed", "failed", "cancelled"):
            await conn.execute(
                """
                UPDATE workflow_runs
                SET state = ?, finished_at_ns = ?, error = COALESCE(?, error)
                WHERE id = ?
                """,
                (state, now, error, run_id),
            )
        else:
            await conn.execute(
                "UPDATE workflow_runs SET state = ?, error = COALESCE(?, error) WHERE id = ?",
                (state, error, run_id),
            )

    async def list_runs(
        self,
        workflow_id: str | None = None,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        conn = self._require_conn()
        if workflow_id is None:
            cur = await conn.execute(
                "SELECT * FROM workflow_runs ORDER BY started_at_ns DESC LIMIT ?",
                (limit,),
            )
        else:
            cur = await conn.execute(
                "SELECT * FROM workflow_runs WHERE workflow_id = ? "
                "ORDER BY started_at_ns DESC LIMIT ?",
                (workflow_id, limit),
            )
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = self._require_conn()
        cur = await conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        run = dict(row)
        cur = await conn.execute(
            "SELECT seq, kind, label, started_at_ns, finished_at_ns, "
            "success, output, error FROM workflow_run_steps "
            "WHERE run_id = ? ORDER BY seq ASC",
            (run_id,),
        )
        step_rows = await cur.fetchall()
        await cur.close()
        run["steps"] = [dict(r) for r in step_rows]
        return run

    # ------------------------------------------------------------------
    # Run-Steps
    # ------------------------------------------------------------------

    async def start_step(
        self,
        run_id: str,
        seq: int,
        kind: str,
        label: str,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT OR REPLACE INTO workflow_run_steps
                (run_id, seq, kind, label, started_at_ns, success, output)
            VALUES (?, ?, ?, ?, ?, NULL, '')
            """,
            (run_id, seq, kind, label, time.time_ns()),
        )

    async def finish_step(
        self,
        run_id: str,
        seq: int,
        *,
        success: bool,
        output: str = "",
        error: str | None = None,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE workflow_run_steps
            SET finished_at_ns = ?, success = ?, output = ?, error = ?
            WHERE run_id = ? AND seq = ?
            """,
            (time.time_ns(), 1 if success else 0, output, error, run_id, seq),
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup_interrupted_runs(self) -> int:
        """Beim App-Start: ``running`` → ``failed`` (app exit)."""
        conn = self._require_conn()
        cur = await conn.execute(
            """
            UPDATE workflow_runs
            SET state = 'failed',
                finished_at_ns = ?,
                error = 'App exit detected'
            WHERE state IN ('running', 'pending')
            """,
            (time.time_ns(),),
        )
        rc = cur.rowcount
        await cur.close()
        return int(rc or 0)
