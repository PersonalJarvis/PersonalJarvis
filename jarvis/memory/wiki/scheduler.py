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

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.core.config import SchedulerConfig
    from jarvis.memory.wiki.curator import WikiCurator
    from jarvis.memory.wiki.lock import VaultLock

log = logging.getLogger(__name__)


class TriggerSource(StrEnum):
    """Source that fired a ``CuratorScheduler.trigger`` call."""

    SESSION_END = "session_end"
    PERIODIC = "periodic"
    MANUAL = "manual"
    # Wave-2: Stage-1 journal pressure — drain a candidate batch through
    # the Stage-2 consolidator (honours cooldown + lock like SESSION_END).
    JOURNAL = "journal"


def fire_journal_trigger(
    scheduler: Any,
    *,
    name: str = "wiki-journal-trigger",
    log_context: str = "journal trigger",
) -> asyncio.Task[Any]:
    """Fire ONE background ``JOURNAL`` trigger (fire-and-forget, AP-9).

    The single shared entry point for EVERY journal-pressure site so the
    ``scheduler.trigger(TriggerSource.JOURNAL)`` call and its done-callback
    logging cannot drift apart between them:

    * the per-turn count-threshold trigger
      (``extractor.ConversationFactExtractor._maybe_trigger_consolidation``),
    * the boot-time backlog drain (``integration.kick_journal_backlog``), and
    * the below-threshold age-based flush
      (``integration._journal_age_flush_loop``).

    Lives here (a runtime-stdlib-only leaf, next to ``TriggerSource``) so
    ``extractor`` and ``integration`` can both import it without an import
    cycle. Callers own their own preconditions (scheduler present, backlog
    over the count threshold / over the age / non-empty) and any
    ``RuntimeError`` guard for "no running event loop". Returns the created
    task so a caller may await or track it.
    """
    schedule = getattr(scheduler, "schedule_journal_trigger", None)
    task = (
        schedule(name=name)
        if callable(schedule)
        else asyncio.create_task(scheduler.trigger(TriggerSource.JOURNAL), name=name)
    )

    def _log_outcome(t: asyncio.Task[Any]) -> None:
        # A lost trigger is retried on the next append/tick, so WARNING
        # (not ERROR) is the right severity.
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.warning("wiki journal trigger (%s) failed: %s", log_context, exc)

    if not getattr(task, "_jarvis_journal_outcome_callback", False):
        task.add_done_callback(_log_outcome)
        task._jarvis_journal_outcome_callback = True  # type: ignore[attr-defined]
    return task


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
        ``"cooldown"``, ``"locked"``, ``"periodic_disabled"``,
        ``"no_consolidator"``.
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
    2. ``JOURNAL`` bypasses cooldown so each reviewed durable turn can become
       visible without waiting for unrelated future conversation.
    3. ``SESSION_END`` honours cooldown.
    4. ``PERIODIC`` honours cooldown **and** is a no-op when
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
        curator: WikiCurator,
        lock: VaultLock,
        config: SchedulerConfig,
        consolidator: Any = None,
    ) -> None:
        self._curator = curator
        self._lock = lock
        self._config = config
        # Wave-2: optional Stage-2 consolidator. When present, JOURNAL
        # triggers drain the candidate journal through it instead of the
        # legacy curator.ingest path. Duck-typed: needs ``run_once()``.
        self._consolidator = consolidator
        # All overlapping JOURNAL requests share one drain task. A request that
        # arrives during a drain marks it dirty, causing exactly one additional
        # pass after the active batch. This preserves newly appended candidates
        # without creating an unbounded convoy of redundant empty runs.
        self._journal_drain_task: asyncio.Task[SchedulerResult] | None = None
        self._journal_fire_task: asyncio.Task[SchedulerResult] | None = None
        self._journal_dirty = False
        # Monotonic timestamp of the last completed curator run.
        # ``None`` means "never ran" — the first trigger always passes the
        # cooldown gate. (A 0.0 sentinel is wrong: when the machine's
        # monotonic clock is younger than the cooldown window — e.g. a
        # freshly booted host with cooldown_seconds=3600 — elapsed would
        # be < cooldown and the very first run would be throttled.)
        self._last_run_ts: float | None = None

    def attach_consolidator(self, consolidator: Any) -> None:
        """Late-bind the Stage-2 consolidator (built after the scheduler)."""
        self._consolidator = consolidator

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
        if source is TriggerSource.JOURNAL:
            active = self._journal_drain_task
            if active is not None and not active.done():
                self._journal_dirty = True
                result = await asyncio.shield(active)
            else:
                self._journal_dirty = False
                active = asyncio.create_task(
                    self._drain_journal(), name="wiki-journal-drain"
                )
                self._journal_drain_task = active
                try:
                    result = await asyncio.shield(active)
                finally:
                    if self._journal_drain_task is active and active.done():
                        self._journal_drain_task = None
        else:
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

    def schedule_journal_trigger(
        self, *, name: str = "wiki-journal-trigger"
    ) -> asyncio.Task[SchedulerResult]:
        """Return one shared fire-and-forget task for journal pressure.

        A request made while the task is actively draining marks the drain dirty
        so one follow-up pass observes candidates appended during that run.
        """
        active = self._journal_fire_task
        if active is not None and not active.done():
            drain = self._journal_drain_task
            if drain is not None and not drain.done():
                self._journal_dirty = True
            return active

        active = asyncio.create_task(self.trigger(TriggerSource.JOURNAL), name=name)
        self._journal_fire_task = active

        def _clear(done: asyncio.Task[SchedulerResult]) -> None:
            if self._journal_fire_task is done:
                self._journal_fire_task = None

        active.add_done_callback(_clear)
        return active

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _drain_journal(self) -> SchedulerResult:
        """Drain one batch and at most one coalesced follow-up at a time."""
        result = await self._do_trigger(TriggerSource.JOURNAL, episode_paths=None)
        while self._journal_dirty:
            self._journal_dirty = False
            result = await self._do_trigger(
                TriggerSource.JOURNAL, episode_paths=None
            )
        return result

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

        # ----- gate 1b: journal trigger needs a consolidator ------------
        if source is TriggerSource.JOURNAL and self._consolidator is None:
            return SchedulerResult(
                triggered=False,
                skip_reason="no_consolidator",
                curator_output_label="",
            )

        # ----- gate 2: cooldown (MANUAL/JOURNAL bypass; first run always passes)
        if (
            source not in {TriggerSource.MANUAL, TriggerSource.JOURNAL}
            and self._last_run_ts is not None
        ):
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
            if source is TriggerSource.JOURNAL:
                # Wave-2: drain one candidate batch through the body-aware
                # Stage-2 consolidator (it does its own retrieval + writes).
                source_label = await self._consolidator.run_once()
            else:
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
