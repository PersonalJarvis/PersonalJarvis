"""Bootstrap for the voice-session recorder.

Called at app startup from ``jarvis/ui/web/server.py``,
analogous to ``bootstrap_missions`` (Phase 6).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jarvis.core.bus import EventBus

from .constants import HANGUP_SHUTDOWN
from .recorder import SessionRecorder
from .store import SessionStore

log = logging.getLogger(__name__)


def bootstrap_sessions(
    *,
    bus: EventBus,
    db_path: Path,
    enabled: bool = True,
    retention_days: int = 30,
) -> dict[str, Any]:
    """Initializes SessionStore + SessionRecorder and attaches them to the bus.

    Args:
        bus: The desktop app's main EventBus.
        db_path: Path to sessions.db (default: ``data/sessions.db``).
        enabled: If False, returns a ``None`` recorder (feature off).
        retention_days: Sessions older than N days are removed at
            bootstrap. ``0`` disables pruning.

    Returns:
        ``{"store": SessionStore | None, "recorder": SessionRecorder | None}``.
    """
    if not enabled:
        log.info("Voice-Session-Recorder disabled via config")
        return {"store": None, "recorder": None}

    store = SessionStore(db_path=db_path)
    store.open()

    pruned = store.prune_older_than(retention_days)
    if pruned:
        log.info("SessionRecorder: pruned %d sessions older than %d days", pruned, retention_days)

    # Crash recovery: sessions without ended_ms stem from an unclean
    # shutdown of the previous process (hard kill, crash). Without
    # this cleanup they would show up permanently as "running" in the UI.
    # Seal them at their LAST RECORDED ACTIVITY — never at boot time, which
    # could be hours later and would inflate every duration-based stat
    # (the board once showed a 14.8 h phantom session created this way).
    open_ids = store.list_open_sessions()
    for sid in open_ids:
        try:
            row = store.get_session(sid)
            started_ms = row.started_ms if row is not None else 0
            ended_ms = max(store.last_activity_ms(sid) or 0, started_ms)
            store.finalize_session(
                session_id=sid,
                ended_ms=ended_ms,
                hangup_reason=HANGUP_SHUTDOWN,
                turn_count=0,  # conservative: we don't know the aggregates
                total_cost_usd=0.0,
                total_tokens_in=0,
                total_tokens_out=0,
                providers_used=[],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("SessionRecorder: failed to finalize stale session %s: %s", sid, exc)
    if open_ids:
        log.info("SessionRecorder: marked %d stale session(s) as shutdown", len(open_ids))

    # One-time-per-row repair of seals created by the old boot-time stamping.
    # Idempotent and cheap: only 'shutdown' rows are inspected, and honest
    # seals sit inside the tolerance.
    try:
        repaired = store.repair_crash_seals()
        if repaired:
            log.info("SessionRecorder: repaired %d inflated crash seal(s)", repaired)
    except Exception as exc:  # noqa: BLE001
        log.warning("SessionRecorder: crash-seal repair failed: %s", exc)

    recorder = SessionRecorder(store=store)
    recorder.attach(bus)

    log.info("Voice-Session-Recorder ready (db=%s, retention=%dd)", db_path, retention_days)
    return {"store": store, "recorder": recorder}


def shutdown_sessions(bootstrap_result: dict[str, Any]) -> None:
    """Closes the store cleanly. The recorder has no own state to clean up."""
    store: SessionStore | None = bootstrap_result.get("store")
    if store is not None:
        try:
            store.wal_checkpoint()
        except Exception as exc:  # noqa: BLE001
            log.debug("WAL checkpoint failed at shutdown: %s", exc)
        store.close()
        log.info("Voice-Session-Recorder shut down")


__all__ = ["bootstrap_sessions", "shutdown_sessions"]
