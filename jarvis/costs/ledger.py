"""The one place every model call's usage is written down.

The Costs section used to learn about spend from the surfaces that happened
to record it — a voice turn's ``BrainTurnCompleted``, a chat's
``turn_finished``, a mission's draft. Twenty-odd callers of the brain
protocol never told anyone: dictation polish, the wiki curator, awareness
digests, the mission critic, computer-use planning, skill authoring, board
profiles. Their OpenRouter and Gemini bills were real and invisible
(maintainer, 2026-08-25: "I have spent money on OpenRouter, I know it").

So the ledger sits UNDER the surfaces: every provider instance is wrapped at
construction (:mod:`jarvis.brain.usage_meter`) and every usage block a
plugin reports lands here, tagged with the *caller* that asked. Surfaces that
already keep their own richer record (a voice turn knows realtime vs tool
role; a chat turn knows its runner) tag their calls so the read model does
not count them twice — see ``COVERED_ELSEWHERE`` in ``sources.py``.

AP-9: nothing here touches the hot path. ``record_usage`` appends to an
in-memory queue and returns; a daemon thread owns the SQLite file. A failure
to write is logged once and never raised into a caller.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DB_NAME = "llm_usage.db"

#: Who is asking, for the call currently being made. Set by the surface that
#: owns the call (``usage_context("dictation")``); empty means "some
#: background job" and is reported as such.
usage_caller: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jarvis_usage_caller", default=""
)


@contextlib.contextmanager
def usage_context(caller: str) -> Iterator[None]:
    """Tag every model call made inside the block with *caller*."""
    token = usage_caller.set(caller)
    try:
        yield
    finally:
        usage_caller.reset(token)


def current_caller() -> str:
    return usage_caller.get()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms         INTEGER NOT NULL,
    provider      TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    tokens_cached INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    caller        TEXT NOT NULL DEFAULT '',
    label         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage (ts_ms);
"""


@dataclass(frozen=True, slots=True)
class UsageRow:
    ts_ms: int
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    cost_usd: float
    caller: str
    label: str


class _Writer:
    """One daemon thread, one connection, one queue. Drops nothing it can help."""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Any, ...] | None] = queue.Queue(maxsize=10_000)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._warned = False
        self._queued = 0
        self._done = 0
        self._done_cv = threading.Condition()

    def path(self) -> Path:
        with self._lock:
            if self._path is None:
                from jarvis.core import config as cfg

                self._path = Path(cfg.DATA_DIR) / DB_NAME
            return self._path

    def set_path(self, path: Path | None) -> None:
        """Test hook — point the ledger somewhere else (or back to default).

        The writer thread holds a connection to the OLD file; it is stopped
        first so nothing queued afterwards lands in the wrong place.
        """
        self._stop_thread()
        with self._lock:
            self._path = Path(path) if path is not None else None

    def put(self, row: tuple[Any, ...]) -> None:
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            if not self._warned:
                self._warned = True
                log.warning("usage ledger: queue full, dropping usage rows")
            return
        with self._done_cv:
            self._queued += 1
        self._ensure_thread()

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="jarvis-usage-ledger", daemon=True
            )
            self._thread.start()

    def _stop_thread(self) -> None:
        with self._lock:
            thread = self._thread
        if thread is None or not thread.is_alive():
            return
        self._queue.put(None)
        thread.join(timeout=5.0)

    def flush(self, timeout_s: float = 2.0) -> None:
        """Wait until everything queued so far is on disk (tests, shutdown)."""
        with self._done_cv:
            target = self._queued
            deadline = time.monotonic() + timeout_s
            while self._done < target:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._done_cv.wait(remaining)

    def _mark_done(self) -> None:
        with self._done_cv:
            self._done += 1
            self._done_cv.notify_all()

    def _run(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            while True:
                try:
                    item = self._queue.get(timeout=5.0)
                except queue.Empty:
                    # Idle: release the file so a report can read it freely and
                    # the thread can be re-armed by the next record.
                    break
                if item is None:
                    break
                try:
                    if conn is None:
                        conn = self._open()
                    if conn is not None:
                        conn.execute(
                            "INSERT INTO llm_usage (ts_ms, provider, model, tokens_in,"
                            " tokens_out, tokens_cached, cost_usd, caller, label)"
                            " VALUES (?,?,?,?,?,?,?,?,?)",
                            item,
                        )
                        conn.commit()
                except sqlite3.Error as exc:
                    if not self._warned:
                        self._warned = True
                        log.warning("usage ledger: write failed (%s); further rows are lost", exc)
                finally:
                    self._mark_done()
        finally:
            if conn is not None:
                conn.close()

    def _open(self) -> sqlite3.Connection | None:
        path = self.path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            return conn
        except (OSError, sqlite3.Error) as exc:
            if not self._warned:
                self._warned = True
                log.warning("usage ledger: %s not writable (%s)", path, exc)
            return None


_writer = _Writer()


def record_usage(
    *,
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    tokens_cached: int = 0,
    cost_usd: float = 0.0,
    caller: str | None = None,
    label: str = "",
    ts_ms: int | None = None,
) -> None:
    """Write one model call down. Never raises, never blocks the caller.

    ``caller`` defaults to the current :func:`usage_context`; ``cost_usd`` is
    what the provider itself reported, when it did — the report re-prices
    anything left at 0.0 from the rate tables.
    """
    try:
        total = max(0, int(tokens_in)) + max(0, int(tokens_out)) + max(0, int(tokens_cached))
        if total <= 0 and float(cost_usd) <= 0:
            return
        _writer.put(
            (
                int(ts_ms if ts_ms is not None else time.time() * 1000),
                str(provider or ""),
                str(model or ""),
                max(0, int(tokens_in)),
                max(0, int(tokens_out)),
                max(0, int(tokens_cached)),
                float(cost_usd or 0.0),
                str(caller if caller is not None else current_caller()),
                str(label or "")[:120],
            )
        )
    except Exception:  # noqa: BLE001 — accounting must never cost a call
        log.debug("usage ledger: record failed", exc_info=True)


def flush(timeout_s: float = 2.0) -> None:
    _writer.flush(timeout_s)


def set_ledger_path(path: Path | None) -> None:
    _writer.set_path(path)


def ledger_path() -> Path:
    return _writer.path()


def read_usage(path: Path, since_ms: int, until_ms: int) -> Iterator[UsageRow]:
    """Rows in the window, oldest first. A missing file yields nothing."""
    if not path.exists():
        return
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        log.warning("usage ledger: %s not readable (%s)", path, exc)
        return
    try:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            "SELECT ts_ms, provider, model, tokens_in, tokens_out, tokens_cached, cost_usd,"
            " caller, label FROM llm_usage WHERE ts_ms BETWEEN ? AND ? ORDER BY ts_ms",
            (since_ms, until_ms),
        ):
            yield UsageRow(
                ts_ms=int(row["ts_ms"] or 0),
                provider=str(row["provider"] or ""),
                model=str(row["model"] or ""),
                tokens_in=int(row["tokens_in"] or 0),
                tokens_out=int(row["tokens_out"] or 0),
                tokens_cached=int(row["tokens_cached"] or 0),
                cost_usd=float(row["cost_usd"] or 0.0),
                caller=str(row["caller"] or ""),
                label=str(row["label"] or ""),
            )
    except sqlite3.Error as exc:
        log.warning("usage ledger: read failed (%s)", exc)
    finally:
        conn.close()


__all__ = [
    "DB_NAME",
    "UsageRow",
    "current_caller",
    "flush",
    "ledger_path",
    "read_usage",
    "record_usage",
    "set_ledger_path",
    "usage_caller",
    "usage_context",
]
