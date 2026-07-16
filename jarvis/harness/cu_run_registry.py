"""In-process registry of Computer-Use runs (deep-dive 2026-07-15, H-09).

The run-control REST surface (start / status / cancel) needs an inventory of
missions: id, goal, lifecycle status, timestamps, result. The harness itself
records every mission here — regardless of launch route (voice, LLM tool,
scheduled task, REST) — so the API sees them all.

Design constraints:

* **Pure in-memory and bounded** — this is live run control, not history;
  durable evidence already lives in the flight recorder and session store.
* **Platform-neutral** — plain Python, no OS APIs, works identically on
  Windows / macOS / Linux / headless.
* **Never breaks a mission** — every mutation is defensive; a registry bug
  must not take down the harness (mirror of the token-registry contract in
  ``computer_use_context``).
* **Single event loop** — mutations happen on the backend loop only (the
  harness and the REST routes share it), so no locking is required.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Bounded history: the oldest TERMINAL runs are evicted past this size.
_MAX_RUNS = 100

#: Truncation bound for the stored final output of a run.
_RESULT_TEXT_MAX = 2000

TERMINAL_STATUSES = frozenset({"finished", "error", "cancelled", "timeout"})
ACTIVE_STATUSES = frozenset({"queued", "running"})


@dataclass
class CURun:
    """One Computer-Use mission's control-plane view."""

    mission_id: str
    goal: str
    source: str
    status: str = "queued"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    exit_code: int | None = None
    result_text: str = ""
    #: CancelToken while the run is active; cleared when it ends.
    token: Any = None

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe public view (never exposes the token)."""
        return {
            "mission_id": self.mission_id,
            "goal": self.goal,
            "source": self.source,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "result_text": self.result_text,
        }


_RUNS: OrderedDict[str, CURun] = OrderedDict()


def _evict_if_needed() -> None:
    if len(_RUNS) <= _MAX_RUNS:
        return
    for mission_id, run in list(_RUNS.items()):
        if len(_RUNS) <= _MAX_RUNS:
            break
        if run.status in TERMINAL_STATUSES:
            _RUNS.pop(mission_id, None)


def register_run(
    mission_id: str, goal: str, token: Any, *, source: str = "app",
) -> None:
    """Record a mission the moment its cancel token exists (status queued)."""
    try:
        _RUNS[mission_id] = CURun(
            mission_id=mission_id,
            goal=str(goal or "")[:500],
            source=str(source or "app"),
            token=token,
        )
        _evict_if_needed()
    except Exception:  # noqa: BLE001 — the registry must never break a mission
        log.debug("cu run register failed", exc_info=True)


def mark_running(mission_id: str) -> None:
    """The mission holds the desktop lock and is actually driving input."""
    run = _RUNS.get(mission_id)
    if run is not None and run.status == "queued":
        run.status = "running"


def finish_run(
    mission_id: str,
    status: str,
    *,
    exit_code: int | None = None,
    result_text: str = "",
) -> None:
    """Terminal transition; clears the token so cancel becomes a no-op."""
    run = _RUNS.get(mission_id)
    if run is None:
        return
    run.status = status if status in TERMINAL_STATUSES else "finished"
    run.ended_at = time.time()
    run.exit_code = exit_code
    run.result_text = str(result_text or "")[:_RESULT_TEXT_MAX]
    run.token = None


def cancel_run(mission_id: str, reason: str = "api_cancel") -> bool:
    """Cancel ONE active run by id. False when unknown or already terminal.

    The status flips to ``cancelled`` when the harness observes the token —
    this only fires the token, mirroring the voice-hangup contract.
    """
    run = _RUNS.get(mission_id)
    if run is None or run.status in TERMINAL_STATUSES or run.token is None:
        return False
    try:
        run.token.cancel(reason)
    except Exception:  # noqa: BLE001 — a broken token must not 500 the API
        log.debug("cu run cancel failed", exc_info=True)
        return False
    return True


def cancel_all_runs(reason: str = "api_cancel") -> int:
    """Cancel every active run; returns how many tokens were fired."""
    cancelled = 0
    for run in list(_RUNS.values()):
        if run.status in ACTIVE_STATUSES and run.token is not None:
            try:
                run.token.cancel(reason)
                cancelled += 1
            except Exception:  # noqa: BLE001 — keep cancelling the rest
                log.debug("cu run cancel failed", exc_info=True)
    return cancelled


def get_run(mission_id: str) -> dict[str, Any] | None:
    run = _RUNS.get(mission_id)
    return run.snapshot() if run is not None else None


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first snapshots, active runs included."""
    runs = list(_RUNS.values())
    runs.sort(key=lambda r: r.started_at, reverse=True)
    return [r.snapshot() for r in runs[: max(1, int(limit))]]


def active_run_count() -> int:
    return sum(1 for r in _RUNS.values() if r.status in ACTIVE_STATUSES)


def clear_runs() -> None:
    """Test/teardown helper — wipes the registry."""
    _RUNS.clear()
