"""Lightweight in-memory telemetry for the wiki memory pipeline (B8.7).

Why this exists
---------------
After the 2026-05-14 incident, debugging "is the memory pipeline alive?"
required grepping stack traces across half a dozen log files. This
module exposes the same answer as a single dict with named counters,
queryable from code (``snapshot()``), the running app (``GET /api/wiki
/telemetry``) and the hourly log line emitted by
:func:`run_hourly_summary_loop`.

Design constraints
------------------
* **In-memory only.** Counters reset on every Jarvis restart; nothing
  is persisted. This is observability, not metrics-as-a-product.
* **Thread-safe.** ``inc()`` runs under a ``threading.Lock`` so it is
  safe to call from both the asyncio event loop and from any worker
  thread the pipeline currently uses (curator, atomic writer).
* **Free names.** ``inc("some.new.counter")`` registers the counter
  lazily; new instrumentation does not need to touch this module.
* **Defaults registered up-front.** The eight counters listed in the
  B8.7 brief are pre-registered so an early ``snapshot()`` returns
  them as ``0`` rather than omitting them. That keeps the JSON shape
  stable for the dashboard.

Public surface
--------------
* :data:`telemetry` — module-level singleton; ``from
  jarvis.memory.wiki.telemetry import telemetry``.
* :class:`MemoryTelemetry` — same singleton's class; can be re-
  instantiated in tests via :func:`get_telemetry` + ``reset()``.
* :func:`run_hourly_summary_loop` — coroutine that logs the snapshot
  once an hour. Started from the wiki-integration bootstrap.
"""
from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger(__name__)

# The canonical counter names the B8.7 brief enumerates. Listing them
# here means every fresh ``snapshot()`` includes them as ``0`` even if
# nothing has fired yet -- the dashboard never sees a key vanish.
DEFAULT_COUNTERS: tuple[str, ...] = (
    "voice_turns_seen",
    "voice_turns_ingested_ack",
    "voice_turns_ingested_aggressive",
    "wiki_context_hits",
    "wiki_context_misses",
    "session_rollups_succeeded",
    "session_rollups_failed",
    "wiki_pages_created",
    "wiki_pages_updated",
    # D2 (2026-06): session-page feed retirement + conversation-only feed.
    "session_rollups_wiki_write_disabled",
    "wiki_links_refused_dangling",
    "wiki_writes_blocked_pii",
    "wiki_writes_blocked_truncated",
    # Wave-2 two-stage curator quality counters (B8). Decision names follow
    # constants.CURATOR_DECISIONS (parity-tested).
    "wiki_candidates_extracted",
    "wiki_consolidator_add",
    "wiki_consolidator_update",
    "wiki_consolidator_noop",
    "wiki_consolidator_invalidate",
    "wiki_consolidator_runs",
)


class MemoryTelemetry:
    """Thread-safe named-counter store.

    Instances are cheap; the module-level :data:`telemetry` singleton
    is the one that production code consumes. Tests construct their
    own instance to avoid leaking counter state across runs.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {name: 0 for name in DEFAULT_COUNTERS}

    def inc(self, name: str, amount: int = 1) -> None:
        """Atomically add ``amount`` to ``name``. Unknown names auto-register at 0."""
        if amount == 0:
            return
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def get(self, name: str) -> int:
        """Return the current value (0 when unknown)."""
        with self._lock:
            return self._counters.get(name, 0)

    def snapshot(self) -> dict[str, int]:
        """Return a stable copy of all counters at this instant.

        The returned dict is a fresh copy -- mutating it does not affect
        the store. Counters are sorted alphabetically so JSON output
        stays diff-friendly across runs.
        """
        with self._lock:
            return {k: self._counters[k] for k in sorted(self._counters)}

    def reset(self) -> None:
        """Reset every counter to zero. Keeps the registered names.

        Reserved for tests and the optional hourly-summary reset hook;
        production code never calls this.
        """
        with self._lock:
            for k in self._counters:
                self._counters[k] = 0


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


telemetry = MemoryTelemetry()


def get_telemetry() -> MemoryTelemetry:
    """Return the module-level :class:`MemoryTelemetry`.

    Provided so callers don't import the bare name (easier to mock in
    tests via ``patch("jarvis.memory.wiki.telemetry.get_telemetry")``).
    """
    return telemetry


# ---------------------------------------------------------------------------
# Hourly summary task
# ---------------------------------------------------------------------------


_HOUR_SECONDS: int = 60 * 60


def format_summary(snapshot: dict[str, int]) -> str:
    """One-line human-readable summary used by the hourly log."""
    parts = [f"{k}={v}" for k, v in snapshot.items()]
    return " ".join(parts)


async def run_hourly_summary_loop(
    *,
    interval_seconds: int = _HOUR_SECONDS,
    instance: MemoryTelemetry | None = None,
) -> None:
    """Log the telemetry snapshot once per ``interval_seconds``.

    The loop runs until cancelled. The integration bootstrap creates
    this as ``asyncio.create_task(run_hourly_summary_loop())`` and
    cancels the task on shutdown. ``CancelledError`` is propagated so
    the caller's ``await task`` resolves cleanly.

    Failures inside the snapshot or the log call are caught and logged
    as warnings -- a broken telemetry sink must never crash the host
    process or interrupt the loop.
    """
    target = instance if instance is not None else telemetry
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            log.info("wiki_telemetry: hourly summary loop cancelled")
            raise
        try:
            snap = target.snapshot()
            log.info("wiki_telemetry hourly: %s", format_summary(snap))
        except Exception:                                      # noqa: BLE001
            log.warning(
                "wiki_telemetry: hourly summary failed to emit", exc_info=True,
            )


__all__ = [
    "DEFAULT_COUNTERS",
    "MemoryTelemetry",
    "format_summary",
    "get_telemetry",
    "run_hourly_summary_loop",
    "telemetry",
]
