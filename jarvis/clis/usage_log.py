"""``UsageLog`` — SQLite backend for CLI invocation history.

**Privacy-first:** stdout/stderr are not persisted. Only lengths +
the first part of stderr (500 characters, secret-scrubbed) are stored.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from jarvis.core.paths import cli_usage_db_path, ensure_user_dirs

log = logging.getLogger(__name__)


_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"gho_[A-Za-z0-9]{36}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{80,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"ya29\.[0-9A-Za-z_-]{50,}"),
    re.compile(r"xox[bpoa]-[A-Za-z0-9-]{20,}"),
    re.compile(r"sk_(live|test)_[A-Za-z0-9]{20,}"),
    re.compile(r"rk_(live|test)_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(?:authorization|api[_-]?key|bearer)[\s:=]+[A-Za-z0-9._-]{16,}"),
)


def scrub_secrets(text: str) -> str:
    """Replaces known secret patterns with ``<REDACTED>``."""
    if not text:
        return ""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("<REDACTED>", text)
    return text


@dataclass(frozen=True, slots=True)
class UsageRow:
    id: int
    trace_id: str | None
    cli_name: str
    full_command: str
    args_preview: str | None
    exit_code: int | None
    stdout_len: int
    stderr_len: int
    stderr_preview: str | None
    duration_ms: int | None
    caller: str
    started_at: int
    finished_at: int | None
    cwd: str | None


@dataclass(frozen=True, slots=True)
class UsageStats:
    total_calls: int
    success_calls: int
    avg_duration_ms: int
    last_used_at: int | None
    top_commands: tuple[tuple[str, int], ...]
    calls_by_caller: dict[str, int]


class UsageLog:
    def __init__(self, *, db_path: Path | None = None) -> None:
        self._db_path = db_path or cli_usage_db_path()
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def record_start(
        self,
        *,
        cli_name: str,
        full_command: str,
        caller: str,
        trace_id: str | None = None,
        cwd: str | None = None,
        started_at_ms: int,
    ) -> int:
        args_preview = full_command[:200]
        with self._write_cursor() as cur:
            cur.execute(
                """
                INSERT INTO cli_invocations (
                    trace_id, cli_name, full_command, args_preview,
                    exit_code, stdout_len, stderr_len, stderr_preview,
                    duration_ms, caller, started_at, finished_at, cwd
                ) VALUES (?, ?, ?, ?, NULL, 0, 0, NULL, NULL, ?, ?, NULL, ?)
                """,
                (trace_id, cli_name, full_command, args_preview, caller, started_at_ms, cwd),
            )
            return int(cur.lastrowid or 0)

    def record_finish(
        self, row_id: int, *,
        exit_code: int, stdout: str, stderr: str, finished_at_ms: int,
    ) -> None:
        stdout_len = len(stdout or "")
        stderr_len = len(stderr or "")
        stderr_preview = scrub_secrets(stderr or "")[:500] if stderr_len else None
        with self._write_cursor() as cur:
            cur.execute(
                """
                UPDATE cli_invocations
                SET exit_code = ?, stdout_len = ?, stderr_len = ?,
                    stderr_preview = ?, finished_at = ?,
                    duration_ms = ? - started_at
                WHERE id = ?
                """,
                (exit_code, stdout_len, stderr_len, stderr_preview,
                 finished_at_ms, finished_at_ms, row_id),
            )

    def record_failure(self, row_id: int, *, error: str, finished_at_ms: int) -> None:
        preview = scrub_secrets(error or "")[:500]
        with self._write_cursor() as cur:
            cur.execute(
                """
                UPDATE cli_invocations
                SET exit_code = NULL, stdout_len = 0, stderr_len = ?,
                    stderr_preview = ?, finished_at = ?,
                    duration_ms = ? - started_at
                WHERE id = ?
                """,
                (len(error or ""), preview, finished_at_ms, finished_at_ms, row_id),
            )

    def list_for(
        self, cli_name: str, *,
        limit: int = 100, offset: int = 0,
        since_ms: int | None = None, until_ms: int | None = None,
        success_only: bool = False, search: str | None = None,
    ) -> list[UsageRow]:
        clauses: list[str] = ["cli_name = ?"]
        params: list[Any] = [cli_name]
        if since_ms is not None:
            clauses.append("started_at >= ?"); params.append(since_ms)
        if until_ms is not None:
            clauses.append("started_at <= ?"); params.append(until_ms)
        if success_only:
            clauses.append("exit_code = 0")
        if search:
            clauses.append("full_command LIKE ?"); params.append(f"%{search}%")
        sql = (
            "SELECT id, trace_id, cli_name, full_command, args_preview, "
            "       exit_code, stdout_len, stderr_len, stderr_preview, "
            "       duration_ms, caller, started_at, finished_at, cwd "
            "FROM cli_invocations WHERE " + " AND ".join(clauses) +
            " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with self._read_cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [_row_from_tuple(r) for r in rows]

    def list_for_trace(self, trace_id: str, *, limit: int = 200) -> list[UsageRow]:
        """All CLI invocations tagged with one trace_id, oldest first.

        Run Inspector joins a voice turn's trace_id to its tool calls. Returns
        an empty list for a falsy trace_id (turns without a captured trace)."""
        if not trace_id:
            return []
        sql = (
            "SELECT id, trace_id, cli_name, full_command, args_preview, "
            "       exit_code, stdout_len, stderr_len, stderr_preview, "
            "       duration_ms, caller, started_at, finished_at, cwd "
            "FROM cli_invocations WHERE trace_id = ? "
            "ORDER BY started_at ASC LIMIT ?"
        )
        with self._read_cursor() as cur:
            rows = cur.execute(sql, (trace_id, limit)).fetchall()
        return [_row_from_tuple(r) for r in rows]

    def count_for(self, cli_name: str, *, since_ms: int | None = None) -> int:
        if since_ms is None:
            sql = "SELECT COUNT(*) FROM cli_invocations WHERE cli_name = ?"
            params: tuple[Any, ...] = (cli_name,)
        else:
            sql = ("SELECT COUNT(*) FROM cli_invocations "
                   "WHERE cli_name = ? AND started_at >= ?")
            params = (cli_name, since_ms)
        with self._read_cursor() as cur:
            return int(cur.execute(sql, params).fetchone()[0])

    def last_used_at(self, cli_name: str) -> int | None:
        with self._read_cursor() as cur:
            row = cur.execute(
                "SELECT MAX(started_at) FROM cli_invocations WHERE cli_name = ?",
                (cli_name,),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def stats_for(self, cli_name: str, *, since_ms: int | None = None) -> UsageStats:
        where = "WHERE cli_name = ?"
        params: list[Any] = [cli_name]
        if since_ms is not None:
            where += " AND started_at >= ?"
            params.append(since_ms)
        with self._read_cursor() as cur:
            total, success, avg_dur, last = cur.execute(
                "SELECT COUNT(*), "
                "       COALESCE(SUM(CASE WHEN exit_code = 0 THEN 1 ELSE 0 END), 0), "
                "       COALESCE(CAST(AVG(duration_ms) AS INTEGER), 0), "
                "       MAX(started_at) "
                f"FROM cli_invocations {where}",
                params,
            ).fetchone()
            top_rows = cur.execute(
                "SELECT SUBSTR(full_command, 1, 60) AS cmd_pfx, COUNT(*) AS c "
                f"FROM cli_invocations {where} GROUP BY cmd_pfx "
                "ORDER BY c DESC LIMIT 5",
                params,
            ).fetchall()
            caller_rows = cur.execute(
                "SELECT caller, COUNT(*) "
                f"FROM cli_invocations {where} GROUP BY caller",
                params,
            ).fetchall()
        return UsageStats(
            total_calls=int(total or 0),
            success_calls=int(success or 0),
            avg_duration_ms=int(avg_dur or 0),
            last_used_at=int(last) if last is not None else None,
            top_commands=tuple((r[0], int(r[1])) for r in top_rows),
            calls_by_caller={r[0]: int(r[1]) for r in caller_rows},
        )

    def purge_older_than(self, cutoff_ms: int) -> int:
        with self._write_cursor() as cur:
            cur.execute("DELETE FROM cli_invocations WHERE started_at < ?", (cutoff_ms,))
            return int(cur.rowcount or 0)

    def delete_for(self, cli_name: str) -> int:
        with self._write_cursor() as cur:
            cur.execute("DELETE FROM cli_invocations WHERE cli_name = ?", (cli_name,))
            return int(cur.rowcount or 0)

    def close(self) -> None:
        if self._conn is not None:
            try: self._conn.close()
            except Exception: pass  # noqa: BLE001
            self._conn = None

    def _ensure_schema(self) -> None:
        ensure_user_dirs()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS cli_invocations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT,
                    cli_name TEXT NOT NULL,
                    full_command TEXT NOT NULL,
                    args_preview TEXT,
                    exit_code INTEGER,
                    stdout_len INTEGER NOT NULL DEFAULT 0,
                    stderr_len INTEGER NOT NULL DEFAULT 0,
                    stderr_preview TEXT,
                    duration_ms INTEGER,
                    caller TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    finished_at INTEGER,
                    cwd TEXT,
                    detail_blob_path TEXT
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_cli_name_time "
                "ON cli_invocations(cli_name, started_at DESC)"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trace ON cli_invocations(trace_id)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_caller_time "
                "ON cli_invocations(caller, started_at DESC)"
            )

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._db_path, isolation_level=None,
                check_same_thread=False, timeout=5.0,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    @contextmanager
    def _write_cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            conn = self._connection()
            cur = conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    @contextmanager
    def _read_cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            conn = self._connection()
            cur = conn.cursor()
            try:
                yield cur
            finally:
                cur.close()


def _row_from_tuple(r: tuple[Any, ...]) -> UsageRow:
    return UsageRow(
        id=int(r[0]), trace_id=r[1], cli_name=r[2], full_command=r[3],
        args_preview=r[4], exit_code=r[5] if r[5] is not None else None,
        stdout_len=int(r[6] or 0), stderr_len=int(r[7] or 0),
        stderr_preview=r[8], duration_ms=r[9] if r[9] is not None else None,
        caller=r[10], started_at=int(r[11]),
        finished_at=r[12] if r[12] is not None else None, cwd=r[13],
    )


__all__ = ["UsageLog", "UsageRow", "UsageStats", "scrub_secrets"]
