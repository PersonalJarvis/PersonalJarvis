"""Bootstrap fuer den Voice-Session-Recorder.

Wird beim App-Start aus ``jarvis/ui/web/server.py`` aufgerufen,
analog zu ``bootstrap_missions`` (Phase 6).
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
    """Initialisiert SessionStore + SessionRecorder, dockt am Bus an.

    Args:
        bus: Der Haupt-EventBus der Desktop-App.
        db_path: Pfad zur sessions.db (Default: ``data/sessions.db``).
        enabled: Wenn False, gibt ``None``-Recorder zurueck (Feature off).
        retention_days: Sessions aelter als N Tage werden beim Bootstrap
            entfernt. ``0`` deaktiviert das Pruning.

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

    # Crash-Recovery: Sessions ohne ended_ms stammen aus einem nicht
    # sauberen Shutdown des Vorgaenger-Prozesses (Hard-Kill, Crash). Ohne
    # diese Bereinigung erscheinen sie permanent als "laeuft" in der UI.
    # Mark sie als "shutdown" mit ended_ms = startup-Zeit.
    import time as _time
    open_ids = store.list_open_sessions()
    for sid in open_ids:
        try:
            store.finalize_session(
                session_id=sid,
                ended_ms=int(_time.time() * 1000),
                hangup_reason=HANGUP_SHUTDOWN,
                turn_count=0,  # konservativ: Aggregate kennen wir nicht
                total_cost_usd=0.0,
                total_tokens_in=0,
                total_tokens_out=0,
                providers_used=[],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("SessionRecorder: failed to finalize stale session %s: %s", sid, exc)
    if open_ids:
        log.info("SessionRecorder: marked %d stale session(s) as shutdown", len(open_ids))

    recorder = SessionRecorder(store=store)
    recorder.attach(bus)

    log.info("Voice-Session-Recorder ready (db=%s, retention=%dd)", db_path, retention_days)
    return {"store": store, "recorder": recorder}


def shutdown_sessions(bootstrap_result: dict[str, Any]) -> None:
    """Schliesst den Store sauber. Recorder hat keinen eigenen State zum Cleanup."""
    store: SessionStore | None = bootstrap_result.get("store")
    if store is not None:
        try:
            store.wal_checkpoint()
        except Exception as exc:  # noqa: BLE001
            log.debug("WAL checkpoint failed at shutdown: %s", exc)
        store.close()
        log.info("Voice-Session-Recorder shut down")


__all__ = ["bootstrap_sessions", "shutdown_sessions"]
