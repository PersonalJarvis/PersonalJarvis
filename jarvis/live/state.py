"""Durable receipts and independent transcript streams for continuous voice."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class TranscriptFragment:
    session_id: str
    event_id: str
    role: Literal["user", "assistant"]
    delta: str
    start_ms: int
    end_ms: int


class LiveLedger:
    """Claim before execution; uncertain receipts are never automatically replayed."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS live_operations (
                    session_id TEXT NOT NULL, call_id TEXT NOT NULL,
                    tool TEXT NOT NULL, arguments TEXT NOT NULL, revision INTEGER NOT NULL,
                    status TEXT NOT NULL, result TEXT, updated REAL NOT NULL,
                    PRIMARY KEY(session_id, call_id));
                CREATE TABLE IF NOT EXISTS live_transcripts (
                    session_id TEXT NOT NULL, event_id TEXT NOT NULL, role TEXT NOT NULL,
                    delta TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                    PRIMARY KEY(session_id, event_id));
                CREATE TABLE IF NOT EXISTS live_usage (
                    session_id TEXT PRIMARY KEY, seconds REAL NOT NULL,
                    finalized INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS live_backend_usage (
                    session_id TEXT NOT NULL, response_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL, usage TEXT NOT NULL);
            """)

    def claim(
        self, session: str, call: str, tool: str, arguments: dict, revision: int
    ) -> dict | None:
        encoded = json.dumps(arguments, sort_keys=True)
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT tool, arguments, status, result FROM live_operations "
                "WHERE session_id=? AND call_id=?",
                (session, call),
            ).fetchone()
            if row:
                if row[0] != tool or row[1] != encoded:
                    return {"success": False, "error": "Call ID reused with different arguments."}
                return (
                    json.loads(row[3])
                    if row[3]
                    else {
                        "success": False,
                        "status": "uncertain",
                        "error": "Earlier execution unconfirmed. Reconcile before retrying.",
                    }
                )
            self._db.execute(
                "INSERT INTO live_operations VALUES (?, ?, ?, ?, ?, 'running', NULL, ?)",
                (session, call, tool, encoded, revision, time.time()),
            )
        return None

    def finish(self, session: str, call: str, result: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE live_operations SET status='finished', result=?, updated=? "
                "WHERE session_id=? AND call_id=?",
                (json.dumps(result, default=str), time.time(), session, call),
            )

    def append(self, fragment: TranscriptFragment) -> bool:
        with self._lock, self._db:
            return (
                self._db.execute(
                    "INSERT OR IGNORE INTO live_transcripts VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        fragment.session_id,
                        fragment.event_id,
                        fragment.role,
                        fragment.delta,
                        fragment.start_ms,
                        fragment.end_ms,
                    ),
                ).rowcount
                == 1
            )

    def usage(self, session: str, seconds: float, *, finalized: bool = False) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO live_usage VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "seconds=MAX(seconds, excluded.seconds), "
                "finalized=MAX(finalized, excluded.finalized)",
                (session, max(0, seconds), int(finalized)),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def backend_usage(self, session: str, response: str, model: str, usage: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO live_backend_usage VALUES (?, ?, ?, ?)",
                (session, response, model, json.dumps(usage)),
            )

    def transcript(self, session: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT role, delta, start_ms, end_ms FROM live_transcripts "
                "WHERE session_id=? ORDER BY start_ms, rowid",
                (session,),
            ).fetchall()
        return [
            dict(zip(("role", "delta", "start_ms", "end_ms"), row, strict=True)) for row in rows
        ]
