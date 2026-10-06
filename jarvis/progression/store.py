"""SQLite ledger of every XP award and the running total per subject.

One small database (``<data_dir>/progression.db``) opened on first use, never
on the boot path (AP-26). Every award is one transaction that checks the
rule's limits against the ledger itself — duplicate reference, cooldown,
daily cap — so a replayed bus event or a client that reports twice can never
pay twice. All calls are synchronous; the service runs them off the event
loop.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .rules import SUBJECT_KINDS, XpRule, level_for_xp, subject_kind

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS progression_subjects (
    subject_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ({", ".join(repr(k) for k in SUBJECT_KINDS)})),
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    updated_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS progression_awards (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL,
    source TEXT NOT NULL,
    xp INTEGER NOT NULL,
    level_before INTEGER NOT NULL,
    level_after INTEGER NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    ts_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_progression_awards_subject
    ON progression_awards (subject_id, source, ts_ms);
CREATE UNIQUE INDEX IF NOT EXISTS uq_progression_awards_ref
    ON progression_awards (subject_id, source, ref) WHERE ref <> '';
"""


@dataclass(frozen=True, slots=True)
class Award:
    seq: int
    subject_id: str
    kind: str
    source: str
    xp: int
    total_xp: int
    level_before: int
    level_after: int
    ref: str
    ts_ms: int

    @property
    def leveled_up(self) -> bool:
        return self.level_after > self.level_before

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "subject_id": self.subject_id,
            "kind": self.kind,
            "source": self.source,
            "xp": self.xp,
            "total_xp": self.total_xp,
            "level_before": self.level_before,
            "level_after": self.level_after,
            "ref": self.ref,
            "ts_ms": self.ts_ms,
        }


def _local_day_start_ms(now_ms: int) -> int:
    """Midnight of the machine's local day containing ``now_ms``."""
    local = datetime.fromtimestamp(now_ms / 1000).astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000)


class ProgressionStore:
    def __init__(self, path: Path, *, clock: Callable[[], int] | None = None) -> None:
        self._path = Path(path)
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                self._path, isolation_level=None, check_same_thread=False, timeout=5.0
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------ writes

    def award(self, subject_id: str, rule: XpRule, *, ref: str = "") -> Award | None:
        """Pay ``rule`` to ``subject_id`` unless one of its limits says no.

        Returns the stored award, or ``None`` when the reference was already
        paid, the cooldown is still running or the day's cap is spent. A cap
        that has a little room left pays only that remainder.
        """
        kind = subject_kind(subject_id)
        if kind is None or kind != rule.kind:
            raise ValueError(f"rule {rule.source!r} does not pay subject {subject_id!r}")
        ref = (ref or "")[:200]
        now = self._clock()
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = self._award_locked(conn, subject_id, kind, rule, ref, now)
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return result

    def _award_locked(
        self,
        conn: sqlite3.Connection,
        subject_id: str,
        kind: str,
        rule: XpRule,
        ref: str,
        now: int,
    ) -> Award | None:
        if ref:
            # once_per_ref rules key their reference alone; others repeat it at most once too.
            dup = conn.execute(
                "SELECT 1 FROM progression_awards WHERE subject_id = ? AND source = ? AND ref = ?",
                (subject_id, rule.source, ref),
            ).fetchone()
            if dup is not None:
                return None
        if rule.cooldown_s > 0:
            row = conn.execute(
                "SELECT MAX(ts_ms) FROM progression_awards WHERE subject_id = ? AND source = ?",
                (subject_id, rule.source),
            ).fetchone()
            last = row[0] if row is not None else None
            if last is not None and now - int(last) < rule.cooldown_s * 1000:
                return None
        xp = rule.xp
        if rule.daily_cap > 0:
            row = conn.execute(
                "SELECT COALESCE(SUM(xp), 0) FROM progression_awards"
                " WHERE subject_id = ? AND source = ? AND ts_ms >= ?",
                (subject_id, rule.source, _local_day_start_ms(now)),
            ).fetchone()
            xp = min(xp, rule.daily_cap - int(row[0] if row else 0))
        if xp <= 0:
            return None
        current = conn.execute(
            "SELECT xp, level FROM progression_subjects WHERE subject_id = ?", (subject_id,)
        ).fetchone()
        before_xp = int(current["xp"]) if current else 0
        level_before = int(current["level"]) if current else 1
        total = before_xp + xp
        level_after = level_for_xp(total)
        conn.execute(
            """
            INSERT INTO progression_subjects (subject_id, kind, xp, level, updated_ms)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(subject_id) DO UPDATE SET
                xp = excluded.xp, level = excluded.level, updated_ms = excluded.updated_ms
            """,
            (subject_id, kind, total, level_after, now),
        )
        cur = conn.execute(
            """
            INSERT INTO progression_awards
                (subject_id, source, xp, level_before, level_after, ref, ts_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (subject_id, rule.source, xp, level_before, level_after, ref, now),
        )
        return Award(
            seq=int(cur.lastrowid or 0),
            subject_id=subject_id,
            kind=kind,
            source=rule.source,
            xp=xp,
            total_xp=total,
            level_before=level_before,
            level_after=level_after,
            ref=ref,
            ts_ms=now,
        )

    # ------------------------------------------------------------ reads

    def subjects(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT subject_id, kind, xp, level, updated_ms FROM progression_subjects"
                " ORDER BY kind, subject_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def recent(self, *, after_seq: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        """Awards newer than ``after_seq``, oldest first (the last ``limit`` of them)."""
        limit = max(1, min(200, int(limit)))
        with self._lock:
            rows = self._connection().execute(
                """
                SELECT a.seq, a.subject_id, s.kind, a.source, a.xp, a.level_before,
                       a.level_after, a.ref, a.ts_ms
                FROM progression_awards a
                JOIN progression_subjects s ON s.subject_id = a.subject_id
                WHERE a.seq > ?
                ORDER BY a.seq DESC
                LIMIT ?
                """,
                (int(after_seq), limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def latest_seq(self) -> int:
        with self._lock:
            row = self._connection().execute(
                "SELECT COALESCE(MAX(seq), 0) FROM progression_awards"
            ).fetchone()
        return int(row[0]) if row else 0
