"""Scheduler that gates ``WikiCurator`` runs with a cooldown and a file lock.

Architecture
------------
``CuratorScheduler`` is the traffic-light between the rest of the system
and ``WikiCurator.ingest``.  It enforces two independent guards:

1. **VaultLock** — prevents two concurrent curator runs on the same
   vault regardless of how they were triggered.  The lock is always
   respected; even MANUAL triggers must wait for it.

2. **Cooldown** — prevents the curator from being hammered by repeated
   SESSION_END events in a tight loop.  MANUAL triggers bypass this
   guard; PERIODIC and SESSION_END honour it.

Periodic runs are opt-in: when ``config.enable_periodic`` is ``False``
(the default) the periodic task must not be constructed at all — a
clean design instead of a flag that fires and is silently rejected.

Example
-------
::

    scheduler = CuratorScheduler(
        curator=curator,
        lock=VaultLock(Path("data/wiki_curator.lock")),
        config=scheduler_config,
    )

    result = await scheduler.trigger(TriggerSource.SESSION_END,
                                     episode_paths=[p1, p2])
    if not result.triggered:
        print("skipped:", result.skip_reason)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.core.config import SchedulerConfig
    from jarvis.memory.wiki.curator import WikiCurator
    from jarvis.memory.wiki.lock import VaultLock

log = logging.getLogger(__name__)


class TriggerSource(str, Enum):
    """Source that fired a ``CuratorScheduler.trigger`` call."""

    SESSION_END = "session_end"
    PERIODIC = "periodic"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class SchedulerResult:
    """Immutable outcome of a single ``trigger`` call.

    Fields
    ------
    triggered:
        ``True`` when the curator was actually invoked.
    skip_reason:
        Empty string when the curator ran, otherwise a short token
        describing why it was skipped.  Always one of: ``""``,
        ``"cooldown"``, ``"locked"``, ``"periodic_disabled"``.
    curator_output_label:
        The ``source_label`` string passed to ``WikiCurator.ingest``,
        or ``""`` when the curator was not invoked.
    """

    triggered: bool
    skip_reason: str
    curator_output_label: str


class CuratorScheduler:
    """Wraps ``WikiCurator`` with a lock and a cooldown.

    Cooldown rules (in priority order):

    1. ``MANUAL`` triggers bypass cooldown but **not** the lock.
    2. ``SESSION_END`` honours cooldown.
    3. ``PERIODIC`` honours cooldown **and** is a no-op when
       ``config.enable_periodic`` is ``False``.

    The scheduler guarantees ``lock.release()`` runs in a ``try/finally``
    even when the curator raises — the exception is re-raised after the
    lock is released so callers can inspect and log it.

    Logs exactly one line per ``trigger`` call at INFO with the full
    ``SchedulerResult`` rendered as ``key=value`` pairs.
    """

    def __init__(
        self,
        *,
        curator: "WikiCurator",
        lock: "VaultLock",
        config: "SchedulerConfig",
    ) -> None:
        self._curator = curator
        self._lock = lock
        self._config = config
        # Monotonic timestamp of the last completed curator run.
        # 0.0 means "never ran" — always older than any cooldown window.
        self._last_run_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def trigger(
        self,
        source: TriggerSource,
        *,
        episode_paths: list[Path] | None = None,
    ) -> SchedulerResult:
        """Attempt to run the curator from the given *source*.

        Parameters
        ----------
        source:
            What triggered this call.  Controls cooldown and periodic
            gate behaviour.
        episode_paths:
            Optional list of session-digest markdown paths produced by
            the rollup worker.  When provided, the scheduler builds a
            combined source label and passes the file content to the
            curator for context.  When ``None``, a generic label is
            used.

        Returns
        -------
        SchedulerResult
            Always returned, never raises (curator exceptions are
            re-raised after the lock is released — callers must handle
            them).
        """
        result = await self._do_trigger(source, episode_paths=episode_paths)
        # Emit a single structured INFO line per call.
        log.info(
            "CuratorScheduler trigger: source=%s triggered=%s skip_reason=%r label=%r",
            source.value,
            result.triggered,
            result.skip_reason,
            result.curator_output_label,
        )
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _do_trigger(
        self,
        source: TriggerSource,
        *,
        episode_paths: list[Path] | None,
    ) -> SchedulerResult:
        """Core logic — separated so ``trigger`` can log unconditionally."""

        # ----- gate 1: periodic disabled --------------------------------
        if source is TriggerSource.PERIODIC and not self._config.enable_periodic:
            return SchedulerResult(
                triggered=False,
                skip_reason="periodic_disabled",
                curator_output_label="",
            )

        # ----- gate 2: cooldown (MANUAL bypasses) ----------------------
        if source is not TriggerSource.MANUAL:
            elapsed = time.monotonic() - self._last_run_ts
            if elapsed < self._config.cooldown_seconds:
                return SchedulerResult(
                    triggered=False,
                    skip_reason="cooldown",
                    curator_output_label="",
                )

        # ----- gate 3: file lock (nobody bypasses) ---------------------
        acquired = self._lock.acquire(timeout_s=0.0)
        if not acquired:
            return SchedulerResult(
                triggered=False,
                skip_reason="locked",
                curator_output_label="",
            )

        # Lock is held from this point — guarantee release.
        source_label = self._build_source_label(source, episode_paths)
        try:
            source_content = self._build_source_content(episode_paths)
            await self._curator.ingest(source_content, source_label)
            self._last_run_ts = time.monotonic()
        finally:
            self._lock.release()

        return SchedulerResult(
            triggered=True,
            skip_reason="",
            curator_output_label=source_label,
        )

    @staticmethod
    def _build_source_label(
        source: TriggerSource,
        episode_paths: list[Path] | None,
    ) -> str:
        """Return a concise human-readable label for this trigger."""
        if episode_paths:
            names = ", ".join(p.name for p in episode_paths[:3])
            suffix = f" + {len(episode_paths) - 3} more" if len(episode_paths) > 3 else ""
            return f"{source.value}: {names}{suffix}"
        return source.value

    @staticmethod
    def _build_source_content(episode_paths: list[Path] | None) -> str:
        """Read and concatenate episode files when provided.

        Falls back to an empty string when no paths are given or the
        files cannot be read — the curator's salience filter will
        produce an empty update list, which is a valid outcome.
        """
        if not episode_paths:
            return ""
        parts: list[str] = []
        for p in episode_paths:
            try:
                parts.append(p.read_text(encoding="utf-8"))
            except OSError as exc:
                log.warning("CuratorScheduler: cannot read episode %s: %s", p, exc)
        return "\n\n---\n\n".join(parts)


__all__ = ["CuratorScheduler", "SchedulerResult", "TriggerSource"]
