"""Process-wide wiki health state (spec A5): honest, not silent.

The wiki subsystem is fire-and-forget by design (AP-9) — failures must
never interrupt a voice turn. This module is the other half of that
contract: every swallowed failure is recorded HERE so the Wiki tab and
``GET /api/wiki/health`` can show it. Pure in-memory state guarded by a
lock; recording must never raise (a health write failing a write path
would invert the design).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class WikiHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bootstrap_ok: bool | None = None
        self._bootstrap_error: str | None = None
        self._last_write: dict[str, Any] | None = None
        self._last_index: dict[str, Any] | None = None
        self._last_chain_failure: dict[str, Any] | None = None
        self._journal_backlog: int = 0

    def record_bootstrap(self, ok: bool, error: str | None = None) -> None:
        with self._lock:
            self._bootstrap_ok = ok
            self._bootstrap_error = error

    def record_write(
        self, ok: bool, *, pages: list[str], error: str | None, source: str,
    ) -> None:
        with self._lock:
            self._last_write = {
                "ts": time.time(),
                "ok": ok,
                "pages": list(pages),
                "error": error,
                "source": source,
            }

    def record_chain_failure(self, detail: str) -> None:
        with self._lock:
            self._last_chain_failure = {"ts": time.time(), "detail": detail}

    def record_index(
        self,
        ok: bool,
        *,
        operation: str,
        path: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record the latest live index-maintenance outcome."""
        with self._lock:
            self._last_index = {
                "ts": time.time(),
                "ok": bool(ok),
                "operation": operation,
                "path": path,
                "error": error,
            }

    def record_backlog(self, count: int) -> None:
        with self._lock:
            self._journal_backlog = max(0, int(count))

    def snapshot(self) -> dict[str, Any]:
        from jarvis.memory.wiki.vault_root import last_resolution

        res = last_resolution()
        with self._lock:
            return {
                "bootstrap_ok": self._bootstrap_ok,
                "bootstrap_error": self._bootstrap_error,
                "vault_root": str(res.path) if res else None,
                "vault_root_source": res.source if res else None,
                "vault_legacy_conflict": bool(res.legacy_conflict) if res else False,
                "last_write": dict(self._last_write) if self._last_write else None,
                "last_index": dict(self._last_index) if self._last_index else None,
                "last_chain_failure": (
                    dict(self._last_chain_failure)
                    if self._last_chain_failure else None
                ),
                "journal_backlog": self._journal_backlog,
            }


def inspect_index_health(
    vault_root: Path | None,
    db_path: Path,
) -> dict[str, Any]:
    """Compare the active vault with the derived FTS index without writes.

    The result is intentionally count-based: it reports parity and freshness
    without exposing private page names through a diagnostic endpoint.
    """
    state: dict[str, Any] = {
        "index_available": False,
        "indexed_pages": 0,
        "indexed_rows": 0,
        "vault_pages": 0,
        "missing_pages": 0,
        "orphaned_pages": 0,
        "outdated_pages": 0,
        "duplicate_index_rows": 0,
        "last_index_at": None,
        "last_index_operation": None,
        "last_index_path": None,
        "index_lag_seconds": None,
        "index_state": "stale",
        "index_state_reason": "vault_unavailable",
        "index_state_reasons": ["vault_unavailable"],
    }
    if vault_root is None or not vault_root.is_dir():
        return state

    from jarvis.memory.wiki.fts_index import (
        read_index_metadata,
        vault_page_mtimes,
    )

    vault_mtimes = vault_page_mtimes(vault_root)
    vault_paths = set(vault_mtimes)
    state["vault_pages"] = len(vault_paths)
    if not db_path.is_file():
        state["missing_pages"] = len(vault_paths)
        state["index_state_reason"] = "index_unavailable"
        state["index_state_reasons"] = ["index_unavailable"]
        return state

    try:
        conn = sqlite3.connect(
            f"{db_path.resolve(strict=False).as_uri()}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
    except sqlite3.Error:
        state["missing_pages"] = len(vault_paths)
        state["index_state_reason"] = "index_unavailable"
        state["index_state_reasons"] = ["index_unavailable"]
        return state

    try:
        try:
            rows = conn.execute("SELECT path, mtime FROM wiki_fts").fetchall()
        except sqlite3.OperationalError:
            state["missing_pages"] = len(vault_paths)
            state["index_state_reason"] = "index_unavailable"
            state["index_state_reasons"] = ["index_unavailable"]
            return state

        metadata = read_index_metadata(conn)
    finally:
        conn.close()

    index_mtimes: dict[str, float | None] = {}
    for raw_path, raw_mtime in rows:
        path = str(raw_path)
        try:
            mtime: float | None = float(raw_mtime)
        except (TypeError, ValueError):
            mtime = None
        # Duplicate rows are already unhealthy; retaining the newest mtime
        # avoids also inflating the outdated-page count for the same path.
        previous = index_mtimes.get(path)
        if previous is None or (mtime is not None and mtime > previous):
            index_mtimes[path] = mtime

    index_paths = set(index_mtimes)
    missing = vault_paths - index_paths
    orphaned = index_paths - vault_paths
    outdated = {
        path
        for path in vault_paths & index_paths
        if index_mtimes[path] is None
        or abs(vault_mtimes[path] - float(index_mtimes[path])) > 1e-6
    }

    state.update(
        {
            "index_available": True,
            "indexed_pages": len(index_paths),
            "indexed_rows": len(rows),
            "missing_pages": len(missing),
            "orphaned_pages": len(orphaned),
            "outdated_pages": len(outdated),
            "duplicate_index_rows": max(0, len(rows) - len(index_paths)),
        }
    )
    if metadata is not None:
        last_index_at = float(metadata["last_indexed_at"])
        latest_vault_mtime = max(vault_mtimes.values(), default=last_index_at)
        state.update(
            {
                "last_index_at": last_index_at,
                "last_index_operation": metadata["operation"],
                "last_index_path": metadata["path"],
                "index_lag_seconds": max(0.0, latest_vault_mtime - last_index_at),
            }
        )

    reasons: list[str] = []
    if missing:
        reasons.append("missing_pages")
    if orphaned:
        reasons.append("orphaned_pages")
    if outdated:
        reasons.append("outdated_pages")
    if state["duplicate_index_rows"]:
        reasons.append("duplicate_rows")
    if reasons:
        state["index_state_reason"] = reasons[0]
        state["index_state_reasons"] = reasons
    else:
        state["index_state"] = "ok"
        state["index_state_reason"] = "in_sync"
        state["index_state_reasons"] = []
    return state


#: Process-wide singleton — import as ``from jarvis.memory.wiki.health import health``.
health = WikiHealth()


__all__ = ["WikiHealth", "health", "inspect_index_health"]
