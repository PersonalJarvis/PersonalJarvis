"""Process-wide wiki health state (spec A5): honest, not silent.

The wiki subsystem is fire-and-forget by design (AP-9) — failures must
never interrupt a voice turn. This module is the other half of that
contract: every swallowed failure is recorded HERE so the Wiki tab and
``GET /api/wiki/health`` can show it. Pure in-memory state guarded by a
lock; recording must never raise (a health write failing a write path
would invert the design).
"""
from __future__ import annotations

import threading
import time
from typing import Any


class WikiHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bootstrap_ok: bool | None = None
        self._bootstrap_error: str | None = None
        self._last_write: dict[str, Any] | None = None
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
                "last_chain_failure": (
                    dict(self._last_chain_failure)
                    if self._last_chain_failure else None
                ),
                "journal_backlog": self._journal_backlog,
            }


#: Process-wide singleton — import as ``from jarvis.memory.wiki.health import health``.
health = WikiHealth()
