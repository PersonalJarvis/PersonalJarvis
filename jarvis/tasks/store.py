"""TaskStore — aiosqlite-basierte Persistenz fuer die Task-Queue (ADR-0003).

Der Store ist bewusst dumm: kein Scheduling, kein Event-Dispatch. Nur
CRUD + transaktionale State-Uebergaenge + Startup-Cleanup.

Zwei Tabellen:
- ``tasks``      — Hauptzeile pro geschedultem Task (TaskSpec als JSON-Blob).
- ``task_steps`` — Append-only Step-Log (Runner schreibt hier observation/action/
  verify/log-Zeilen, UI rendert sie als Timeline).

Pattern: identisch zu ``jarvis/memory/recall.py`` (``aiosqlite`` + ``ensure_open``
Lazy-Init damit die Store-Instanz synchron konstruiert werden kann).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .schema import TaskSpec, TaskState

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


class TaskStore:
    """CRUD-Store fuer Tasks + Steps auf der gemeinsamen Memory-DB.

    Die DB-Datei wird NICHT von dieser Klasse initialisiert, was das
    Memory-Schema angeht — das uebernimmt ``RecallStore``. Wir koennen
    aber trotzdem init() unabhaengig aufrufen, weil unser Schema
    additiv und idempotent ist (``CREATE TABLE IF NOT EXISTS``).
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
        """Oeffnet DB-Connection + fuehrt additives Schema aus."""
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        # PRAGMAs: WAL + busy_timeout kommen vom Memory-Schema, aber
        # bei einer noch leeren DB setzen wir sie sicherheitshalber auch hier.
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        schema = SCHEMA_FILE.read_text(encoding="utf-8")
        await self._conn.executescript(schema)

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
                "TaskStore nicht initialisiert — rufe init() oder nutze 'async with'."
            )
        return self._conn

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_trigger_fields(spec: TaskSpec) -> tuple[str, int | None, str | None]:
        """Extrahiert ``(trigger_type, due_at_ns, event_selector)`` aus einer Spec.

        ``due_at_ns`` ist UTC-nano-seconds; beim Scheduler wird es mit
        ``time.time_ns()`` verglichen.
        """
        trig = spec.trigger
        if trig.type == "after_delay":
            due = time.time_ns() + int(trig.delay_seconds * 1e9)
            return "after_delay", due, None
        if trig.type == "at_time":
            # Parsing-Responsibility liegt beim Scheduler (ISO-8601 + TZ).
            # Hier nur fallback: wenn parsen klappt, nutzen wir ihn.
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
        raise ValueError(f"Unbekannter Trigger-Typ: {trig.type}")  # pragma: no cover

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def insert(self, spec: TaskSpec, *, trace_id: str | None = None) -> str:
        """Legt einen neuen Task mit ``state='scheduled'`` an.

        Returnt die Task-ID (str). ``trace_id`` darf optional mitgegeben
        werden; sonst wird die Spec-ID als Trace verwendet (ein Task = ein
        Trace, solange kein anderer Scope ueberschreibt).
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
        """Uebergang in einen neuen State, atomar mit optionaler Fehler-/Result-Info.

        Setzt automatisch ``started_at_ns`` beim Uebergang in ``running``
        und ``finished_at_ns`` in Terminal-States.
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
        # `sets` ist eine Whitelist von Spalten-Zuweisungen (oben statisch
        # erzeugt) — kein User-Input fliesst in den SQL-String.
        sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?"  # noqa: S608
        await conn.execute(sql, tuple(params))

    async def append_step(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> int:
        """Haengt einen Step an ``task_steps`` an. Returnt die neue ``seq``.

        ``kind`` ist ``'observation' | 'action' | 'verify' | 'log'``. Keine
        harte DB-Constraint, damit Runner auch andere Kinds (z.B. 'retry')
        eintragen kann.
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
        """Returnt eine Liste von Task-Rows (ohne Steps), gefiltert nach State."""
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
        """Returnt Full-Task (inkl. Steps) oder None."""
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
        """Deserialisiert ``spec_json`` zurueck zu einer TaskSpec."""
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
        """Entfernt Task + Steps (via ON DELETE CASCADE). Returnt ob ein Row getroffen wurde."""
        conn = self._require_conn()
        cur = await conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        rowcount = cur.rowcount
        await cur.close()
        return rowcount > 0

    async def all_pending_scheduled(self) -> list[dict[str, Any]]:
        """Fuer Scheduler-Hydration: alle Tasks im State ``scheduled``."""
        return await self.list(state_filter="scheduled", limit=10_000)

    async def cleanup_interrupted(self) -> int:
        """Startup-Cleanup: alle ``running`` → ``interrupted`` mit Error-Log.

        Returnt die Anzahl der betroffenen Tasks. Laut ADR-0003 muss das
        beim App-Start aufgerufen werden, bevor der Scheduler haydriert.
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
