"""Runaway guard for the model calls the wiki makes on its own.

The wiki calls a model without a user waiting on the answer in a few places:
the Stage-2 consolidator that files pending candidate facts, the search-alias
generator that runs for each page the consolidator writes, and the Stage-1
extractor when a turn is explicitly saved. Before this guard nothing bounded
them: a failing chain was re-tried every two minutes by the age flush and
every six hours by the auto-backfill, on whatever API key happened to be
stored. The cost ledger showed ~300 wiki calls on paid keys in 30 days, and
~5 EUR went in three hours while nobody was at the PC.

Two independent brakes live here, and both hold on key-only installs too:

* **Backoff.** When the work has to wait — the install runs on a subscription
  that is logged out, rate limited or otherwise not answering (see
  :mod:`jarvis.brain.background_policy`) — or the whole provider chain
  failed, the next attempt is pushed out exponentially (5 min, doubling,
  capped at one hour) instead of every trigger retrying at once.
* **Daily call cap.** A hard ceiling on the NUMBER of background wiki model
  calls per local day (``[wiki_integration] max_background_llm_calls_per_day``,
  default 200). Every provider attempt counts, including the split-and-retry
  halves of a truncated batch. This is purely a runaway guard on how OFTEN
  the wiki may call a model: it never shortens a prompt, a context window or
  an output budget. When the cap is reached the work waits for the next day
  and one log line says so.

The day's counter is persisted, so a crash-restart loop cannot reset it.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis.brain.background_policy import BackgroundDeferred

log = logging.getLogger(__name__)

#: Shipped default for ``[wiki_integration] max_background_llm_calls_per_day``.
#: Generous on purpose: a normal day files a handful of facts. The cap exists
#: to stop a runaway loop, never to ration ordinary use.
DEFAULT_DAILY_CALL_CAP = 200

#: First wait after a deferral or a failed chain, doubled per repeat.
_BACKOFF_BASE_S = 300.0
#: Longest wait between attempts, so a reconnected subscription is picked up
#: within the hour without anyone restarting the app.
_BACKOFF_MAX_S = 3600.0

#: Health states surfaced on the wiki health strip / ``GET /api/wiki/health``.
WAITING_FOR_SUBSCRIPTION = "waiting_for_subscription"
DAILY_CAP_REACHED = "daily_cap_reached"


def daily_cap(config: Any) -> int:
    """The configured daily cap, never below one call.

    ``config`` is the root ``JarvisConfig``; anything without the field (a
    test double, ``None``) gets the shipped default.
    """
    section = getattr(config, "wiki_integration", None)
    raw = getattr(section, "max_background_llm_calls_per_day", None)
    if raw is None:
        return DEFAULT_DAILY_CALL_CAP
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning(
            "wiki background guard: unusable max_background_llm_calls_per_day %r "
            "— using the default %d",
            raw,
            DEFAULT_DAILY_CALL_CAP,
        )
        return DEFAULT_DAILY_CALL_CAP
    return max(1, value)


def _record_wait(state: str, detail: str, retry_at: float | None) -> None:
    try:
        from jarvis.memory.wiki.health import health

        health.record_background_wait(state, detail=detail, retry_at=retry_at)
    except Exception:  # noqa: BLE001 - health recording must never break the guard
        log.debug("wiki background guard: health.record_background_wait failed", exc_info=True)


def _clear_wait() -> None:
    try:
        from jarvis.memory.wiki.health import health

        health.clear_background_wait()
    except Exception:  # noqa: BLE001 - health recording must never break the guard
        log.debug("wiki background guard: health.clear_background_wait failed", exc_info=True)


class WikiBackgroundGuard:
    """Daily call cap plus shared backoff for background wiki model calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state_path_override: Path | None = None
        # (local day, calls) — the in-memory mirror of the persisted counter.
        # A failing disk only loses persistence, never the cap itself.
        self._day: str = ""
        self._calls: int = 0
        self._loaded = False
        self._cap_logged_day: str = ""
        self._setbacks = 0
        self._retry_at_monotonic = 0.0
        self._retry_at_wall: float | None = None
        self._last_reason = ""

    # ------------------------------------------------------------------
    # persisted daily counter
    # ------------------------------------------------------------------

    def _state_path(self) -> Path:
        if self._state_path_override is not None:
            return self._state_path_override
        from jarvis.core.paths import user_data_dir

        return user_data_dir() / "data" / "wiki_background_calls.json"

    @staticmethod
    def _today() -> str:
        return _dt.date.today().isoformat()

    def _load_locked(self) -> None:
        today = self._today()
        if self._loaded and self._day == today:
            return
        day, calls = today, 0
        try:
            raw = json.loads(self._state_path().read_text(encoding="utf-8"))
        except FileNotFoundError:  # first call of the day on this install: nothing spent yet
            raw = None
        except (OSError, ValueError):
            log.warning("wiki background guard: unreadable call counter, starting at zero")
            raw = None
        if isinstance(raw, dict) and raw.get("day") == today:
            try:
                calls = max(0, int(raw.get("calls", 0)))
            except (TypeError, ValueError):  # a corrupt count restarts at zero; the cap still holds
                calls = 0
        # Never lower an in-memory count for the same day: the file may be
        # read-only and lag behind what this process already spent.
        if self._day == today:
            calls = max(calls, self._calls)
        self._day, self._calls, self._loaded = day, calls, True

    def _persist_locked(self) -> None:
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".wiki_background_calls.")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"day": self._day, "calls": self._calls}, handle)
            os.replace(tmp, path)
        except OSError:
            # The in-memory counter still enforces the cap for this process.
            log.warning(
                "wiki background guard: could not persist the call counter",
                exc_info=True,
            )

    def calls_today(self) -> int:
        with self._lock:
            self._load_locked()
            return self._calls

    # ------------------------------------------------------------------
    # gates
    # ------------------------------------------------------------------

    def _cap_deferred_locked(self, cap: int) -> BackgroundDeferred:
        detail = (
            f"{self._calls}/{cap} background wiki model calls used today; "
            "the rest waits until tomorrow"
        )
        if self._cap_logged_day != self._day:
            self._cap_logged_day = self._day
            log.warning(
                "wiki background guard: daily cap of %d background model calls "
                "reached — memory work waits until tomorrow "
                "([wiki_integration] max_background_llm_calls_per_day)",
                cap,
            )
        _record_wait(DAILY_CAP_REACHED, detail, retry_at=None)
        return BackgroundDeferred(detail)

    def check_ready(self, config: Any = None) -> None:
        """Raise :class:`BackgroundDeferred` while the wiki must not call a model.

        Cheap: no provider probe, no model call — only the cap and the backoff
        window. Callers run it before claiming work so a waiting install does
        not burn retry attempts on work it never started.
        """
        cap = daily_cap(config)
        with self._lock:
            self._load_locked()
            if self._calls >= cap:
                raise self._cap_deferred_locked(cap)
            remaining = self._retry_at_monotonic - time.monotonic()
            if remaining > 0:
                raise BackgroundDeferred(
                    f"backing off for {remaining:.0f}s after "
                    f"{self._setbacks} unsuccessful attempt(s): {self._last_reason}"
                )

    def ready(self, config: Any = None) -> bool:
        try:
            self.check_ready(config)
        except BackgroundDeferred:  # a yes/no probe; the wait was logged when it began
            return False
        return True

    def reserve_call(self, config: Any = None, *, label: str = "wiki") -> None:
        """Count one model call, or raise :class:`BackgroundDeferred` at the cap."""
        cap = daily_cap(config)
        with self._lock:
            self._load_locked()
            if self._calls >= cap:
                raise self._cap_deferred_locked(cap)
            self._calls += 1
            self._persist_locked()
            log.debug(
                "wiki background guard: call %d/%d today (%s)", self._calls, cap, label
            )

    def hook(self, config: Any, label: str) -> Callable[[str], None]:
        """A ``before_attempt`` callback for ``complete_with_fallback``."""

        def _before_attempt(provider: str) -> None:
            self.reserve_call(config, label=f"{label}:{provider}")

        return _before_attempt

    # ------------------------------------------------------------------
    # outcomes
    # ------------------------------------------------------------------

    def _backoff_locked(self, reason: str) -> float:
        self._setbacks += 1
        delay = min(_BACKOFF_MAX_S, _BACKOFF_BASE_S * (2 ** (self._setbacks - 1)))
        self._retry_at_monotonic = time.monotonic() + delay
        self._retry_at_wall = time.time() + delay
        self._last_reason = reason
        return delay

    def note_waiting(self, detail: str) -> None:
        """The install runs on a subscription that cannot take the work now."""
        with self._lock:
            delay = self._backoff_locked(detail)
            retry_at = self._retry_at_wall
        log.info(
            "wiki background guard: waiting for the subscription (%s) — next "
            "attempt in %.0f min, never on an API key",
            detail,
            delay / 60,
        )
        _record_wait(WAITING_FOR_SUBSCRIPTION, detail, retry_at=retry_at)

    def note_failure(self, detail: str) -> None:
        """Every provider failed (key mode): retry later, not on every trigger."""
        with self._lock:
            delay = self._backoff_locked(detail)
        log.info(
            "wiki background guard: provider chain failed (%s) — next attempt "
            "in %.0f min",
            detail,
            delay / 60,
        )

    def note_progress(self) -> None:
        """A provider answered: clear the backoff and any waiting state."""
        with self._lock:
            had_setbacks = self._setbacks > 0
            self._setbacks = 0
            self._retry_at_monotonic = 0.0
            self._retry_at_wall = None
            self._last_reason = ""
        if had_setbacks:
            log.info("wiki background guard: provider answered again — backoff cleared")
        _clear_wait()

    # ------------------------------------------------------------------
    # tests
    # ------------------------------------------------------------------

    def reset_for_tests(self, *, state_path: Path | None = None) -> None:
        with self._lock:
            self._state_path_override = state_path
            self._day = ""
            self._calls = 0
            self._loaded = False
            self._cap_logged_day = ""
            self._setbacks = 0
            self._retry_at_monotonic = 0.0
            self._retry_at_wall = None
            self._last_reason = ""


#: Process-wide singleton: every background wiki caller shares one budget.
guard = WikiBackgroundGuard()


__all__ = [
    "DAILY_CAP_REACHED",
    "DEFAULT_DAILY_CALL_CAP",
    "WAITING_FOR_SUBSCRIPTION",
    "BackgroundDeferred",
    "WikiBackgroundGuard",
    "daily_cap",
    "guard",
]
