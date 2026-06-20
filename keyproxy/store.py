"""SQLite store — open + migrate from ``schema.sql``.

Synchronous ``sqlite3`` (no extra dependency beyond the stdlib). The proxy
opens a single shared connection guarded by a re-entrant lock; the few writes
(token issue/revoke, best-effort usage rows) are short and serialized, which is
the right trade-off for a lean metering store. ``check_same_thread=False`` plus
the lock lets FastAPI's threadpool call the store from worker threads.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def default_db_path() -> Path:
    """Default DB location — overridable via ``KEYPROXY_DB_PATH``."""
    return Path.home() / ".keyproxy" / "keyproxy.sqlite"


class Store:
    """A thread-safe SQLite wrapper with an additive migrate-on-open schema."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None or str(db_path) == ":memory:":
            # An in-memory DB must use a single connection for the whole
            # process; ``check_same_thread=False`` keeps it usable from the
            # threadpool while the lock serializes access.
            self._db_path = ":memory:"
        else:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Low-level helpers (all guarded by the lock)
    # ------------------------------------------------------------------

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query_one(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(sql, params)
            row = cur.fetchone()
            cur.close()
            return row

    def query_all(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
            return list(rows)
