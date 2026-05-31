"""StoryTracker — bus subscriber for L2 episode condensation.

Lifecycle:

1. Instantiated by ``AwarenessManager.start()`` (when
   ``config.story.enabled and config.verdichter.enabled``).
2. Subscribes to ``FrameUpdated``, ``IdleEntered``, ``ResponseGenerated``.
3. Discards privacy-filter-blocked frames without processing them
   (plan §6 hard negative — NO PII frame in Verdichter input).
4. For each salient frame (``SalienceScorer.score_frame >= 30``):
   append to ``EpisodeBuilder``.
5. On trigger (app switch / idle / hard timer / stop): snapshot the
   builder (atomic, under lock), call the Verdichter (LOCK-FREE),
   persist the episode, update ``state.last_episode_summary``, publish
   ``EpisodeRecorded`` event.

Hard negatives (all from plan §6):
- NO spawn_worker — the Verdichter has its own brain instance.
- Synchronous DB inserts forbidden — everything via ``await recall.record_episode``.
- Privacy-filter-blocked frames NEVER in Verdichter input.
- Episodes < 60 s = spam, skip flush (except trigger ``"stop"``).
- Builder cap: max ``buffer_max`` frames; overflow forces a flush.

Concurrency (Codex-BLOCKER B1+B2 fix, 2026-05-11):

Previously each bus handler held ``_lock`` across the ~5 s Verdichter call,
blocking every subsequent ``on_frame_updated`` / ``on_idle_entered`` /
``on_response_generated`` during the async brain RPC (B1). Additionally
a frame could be lost between the snapshot and ``self._builder = None`` (B2).

New pattern: snapshot (sync, under lock) and dispatch (async, lock-free)
are separated:

- ``_extract_snapshot_locked``: SYNC. Caller MUST hold ``_lock``. Validates
  the builder against the spam guard, extracts a double-buffer snapshot via
  ``EpisodeBuilder.detach_frames``/``detach_events`` (B2), sets
  ``self._builder = None``, and returns a frozen ``_FlushSnapshot``
  dataclass. No ``await``, no yield point.
- ``_run_flush``: ASYNC. Caller MUST have released ``_lock``. Calls the
  Verdichter, persists in ``RecallStore``, updates ``manager.state``, and
  publishes ``EpisodeRecorded``.

Every bus handler follows the same pattern:

    pending: list[_FlushSnapshot] = []
    async with self._lock:
        snap = self._extract_snapshot_locked(trigger_kind="...")
        if snap is not None:
            pending.append(snap)
    for snap in pending:
        await self._run_flush(snap)

``_on_frame_updated`` may extract up to two snapshots in a single lock
acquisition (app-switch flush + buffer/max-duration overflow after the new
``add_frame``); both are dispatched in order outside the lock.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jarvis.awareness.episode import (
    Episode,
    EpisodeBuilder,
    primary_app_from_snapshot,
)
from jarvis.awareness.salience import SALIENCE_THRESHOLD, SalienceScorer
from jarvis.core.events import (
    EpisodeRecorded,
    FrameUpdated,
    IdleEntered,
    ResponseGenerated,
)

if TYPE_CHECKING:
    from jarvis.awareness.config import AwarenessStoryConfig
    from jarvis.awareness.manager import AwarenessManager
    from jarvis.awareness.state import FrameSnapshot
    from jarvis.awareness.verdichter import Verdichter
    from jarvis.core.bus import EventBus
    from jarvis.memory.recall import RecallStore

logger = logging.getLogger(__name__)

_TIMER_STOP_TIMEOUT_S: float = 1.5

_FORCED_TRIGGERS: frozenset[str] = frozenset({
    "stop", "buffer_overflow", "max_duration",
})


@dataclass(frozen=True, slots=True)
class _FlushSnapshot:
    """Immutable snapshot of an ``EpisodeBuilder``, ready for lock-free
    persistence.

    Fully decoupled from the builder — the builder can be reused or
    discarded after the snapshot without touching it. This is the B2
    guarantee (atomic double-buffer).
    """
    trigger_kind: str
    started_at_ns: int
    frames: list[dict[str, Any]]
    events: list[dict[str, Any]]
    primary_app: str
    frame_count: int


class StoryTracker:
    """Bus subscriber. Accumulates salient frames and events, flushes on trigger."""

    def __init__(
        self,
        *,
        manager: AwarenessManager,
        bus: EventBus,
        recall: RecallStore,
        verdichter: Verdichter,
        config: AwarenessStoryConfig,
        scorer: SalienceScorer | None = None,
    ) -> None:
        self._manager = manager
        self._bus = bus
        self._recall = recall
        self._verdichter = verdichter
        self._config = config
        self._scorer = scorer or SalienceScorer()

        self._builder: EpisodeBuilder | None = None
        self._prev_frame: FrameSnapshot | None = None
        self._lock = asyncio.Lock()
        self._timer_task: asyncio.Task[None] | None = None
        self._started: bool = False

    # ---- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Subscribes to the bus and starts the hard-timer task. Idempotent."""
        if self._started:
            return
        self._bus.subscribe(FrameUpdated, self._on_frame_updated)
        self._bus.subscribe(IdleEntered, self._on_idle_entered)
        self._bus.subscribe(ResponseGenerated, self._on_response_generated)
        loop = asyncio.get_running_loop()
        self._timer_task = loop.create_task(
            self._hard_timer_loop(), name="story-tracker-timer",
        )
        self._started = True

    async def stop(self) -> None:
        """Final flush + cancel timer. Best-effort.

        B1 fix: snapshot under lock, dispatch lock-free — so the final
        flush does not block a parallel bus handler either (edge case
        during shutdown races).
        """
        if not self._started:
            return
        self._started = False

        task = self._timer_task
        self._timer_task = None
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=_TIMER_STOP_TIMEOUT_S)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception:    # noqa: BLE001
                logger.debug(
                    "StoryTracker timer task ended with exception",
                    exc_info=True,
                )

        snap: _FlushSnapshot | None
        async with self._lock:
            snap = self._extract_snapshot_locked(trigger_kind="stop")
        if snap is not None:
            await self._run_flush(snap)

    # ---- Bus-Handlers -------------------------------------------------------

    async def _on_frame_updated(self, ev: FrameUpdated) -> None:
        """Per frame: privacy check, trigger check (app switch), salience
        filter, buffering, buffer-overflow check.

        Order is CRITICAL:
        1. The app-switch trigger MUST be checked before the salience filter,
           because the score of an app-switch frame is often below
           SALIENCE_THRESHOLD (app switch +20 < 30) — yet the OLD builder
           with highly salient code frames must still flush.
        2. Salience filter afterwards: frame without another signal is dropped.
        3. add_frame only for high-salience frames.
        4. Buffer-overflow check after add_frame.

        B1 concurrency: all snapshots are extracted UNDER the lock; the
        actual ``_run_flush`` calls run AFTER the lock is released. This
        way the ~5 s Verdichter call does not block a single frame update.
        """
        # Hard negative §6: privacy-filter-blocked frames NEVER in Verdichter input.
        if not ev.is_capture_allowed:
            return

        # Read the current FrameSnapshot from AwarenessState — the
        # WindowFocusWatcher.drain_loop just wrote it there
        # (single-writer invariant from A1).
        frame = self._manager.state.current_frame
        if frame is None:
            return

        # M2 defense (code-reviewer 2026-04-26): theoretically a blocked frame
        # could be written to state.current_frame between
        # FrameUpdated(allowed=True) and our state read (race window <1 ms).
        # Re-checking is_capture_allowed protects against leaking a blocked
        # title into the Verdichter input. Cheap one-liner — no performance hit.
        if not frame.is_capture_allowed:
            return

        pending: list[_FlushSnapshot] = []
        async with self._lock:
            # Step 1: app-switch trigger FIRST. Snapshots the OLD builder
            # under lock — the Verdichter call runs later, lock-free.
            is_app_switch = (
                self._prev_frame is not None
                and self._builder is not None
                and frame.active_process_name != self._prev_frame.active_process_name
            )
            if is_app_switch:
                snap = self._extract_snapshot_locked(
                    trigger_kind="window_switch",
                )
                if snap is not None:
                    pending.append(snap)

            # Step 2: salience filter for the NEW frame.
            score = self._scorer.score_frame(frame, prev=self._prev_frame)
            if score < SALIENCE_THRESHOLD:
                self._prev_frame = frame
            else:
                # Step 3: open builder if needed (possibly empty after flush).
                if self._builder is None:
                    self._builder = EpisodeBuilder(
                        started_at_ns=frame.timestamp_ns,
                    )

                self._builder.add_frame(frame, score)
                self._prev_frame = frame

                # Step 4a: buffer overflow after add_frame. Snapshot; the
                # current frame is part of the overflowing episode.
                if self._builder.frame_count > self._config.buffer_max:
                    snap = self._extract_snapshot_locked(
                        trigger_kind="buffer_overflow",
                    )
                    if snap is not None:
                        pending.append(snap)
                else:
                    # Step 4b (m4 fix): hard cap on episode duration.
                    # Plan §6 EPISODE_MAX_DURATION_MIN=30 — otherwise a
                    # "still active" episode grows without bound.
                    max_dur_ns = (
                        self._config.episode_max_duration_min
                        * 60 * 1_000_000_000
                    )
                    if self._builder.duration_ns > max_dur_ns:
                        snap = self._extract_snapshot_locked(
                            trigger_kind="max_duration",
                        )
                        if snap is not None:
                            pending.append(snap)

        # Lock released — Verdichter + persist run lock-free.
        for snap in pending:
            await self._run_flush(snap)

    async def _on_idle_entered(self, ev: IdleEntered) -> None:
        """Idle = user is gone = episode boundary. Flush immediately (lock-free)."""
        snap: _FlushSnapshot | None
        async with self._lock:
            snap = self._extract_snapshot_locked(trigger_kind="idle_entered")
        if snap is not None:
            await self._run_flush(snap)

    async def _on_response_generated(self, ev: ResponseGenerated) -> None:
        """Brain turn complete = high-salience event in the running episode."""
        async with self._lock:
            if self._builder is None:
                return
            score = self._scorer.score_event("BrainTurnCompleted")
            self._builder.add_event(
                "BrainTurnCompleted",
                score,
                payload={"text_len": len(ev.text), "language": ev.language},
            )

    # ---- Snapshot + Dispatch ------------------------------------------------

    def _extract_snapshot_locked(
        self, *, trigger_kind: str,
    ) -> _FlushSnapshot | None:
        """Atomic snapshot + builder reset. SYNC.

        Caller MUST hold ``self._lock``. Returns None when there is nothing
        to flush (no builder, empty builder, or spam-skip on min-duration).

        Spam skip: triggers outside ``_FORCED_TRIGGERS`` are dropped when
        the builder duration is below ``episode_min_duration_s`` — the
        builder is discarded (``self._builder = None``) so the next frame
        opens a fresh episode.

        Atomic snapshot: ``EpisodeBuilder.detach_frames`` /
        ``detach_events`` swap the internal lists with fresh buckets
        (double-buffer, B2 fix). The returned snapshot is therefore fully
        decoupled from the builder — no concurrent ``add_frame`` can touch it.
        """
        builder = self._builder
        if builder is None or builder.is_empty():
            return None

        duration_s = builder.duration_ns // 1_000_000_000
        forced = trigger_kind in _FORCED_TRIGGERS
        if not forced and duration_s < self._config.episode_min_duration_s:
            # Too short = spam — drop builder, next frame opens fresh.
            logger.debug(
                "StoryTracker skip flush: duration=%ds < min=%ds (trigger=%s)",
                duration_s, self._config.episode_min_duration_s, trigger_kind,
            )
            self._builder = None
            return None

        # Atomic extract: detach_* empty the builder buffers atomically (B2).
        # primary_app is computed on the detached list — after detach the
        # builder is empty and ``builder.primary_app`` would return "".
        started_at_ns = builder.started_at_ns
        raw_frames = builder.detach_frames()
        events_dict = builder.detach_events()
        primary_app = primary_app_from_snapshot(raw_frames)

        frames_dict: list[dict[str, Any]] = [
            {
                "timestamp_ns": f.timestamp_ns,
                "process_name": f.active_process_name,
                "window_title": f.active_window_title,
            }
            for f in raw_frames
        ]

        snap = _FlushSnapshot(
            trigger_kind=trigger_kind,
            started_at_ns=started_at_ns,
            frames=frames_dict,
            events=events_dict,
            primary_app=primary_app,
            frame_count=len(raw_frames),
        )
        # Reset BEFORE returning — the next bus handler acquiring the lock
        # can cleanly open a fresh EpisodeBuilder. detach_* already emptied
        # the buffer; this reference reset makes the state ``no episode in
        # flight`` explicit.
        self._builder = None
        return snap

    async def _run_flush(self, snap: _FlushSnapshot) -> None:
        """Verdichter -> RecallStore -> state update -> EpisodeRecorded.

        LOCK-FREE: this method MUST be called WITHOUT ``self._lock``;
        otherwise the ~5 s Verdichter call blocks all parallel bus handlers
        (Codex-BLOCKER B1).
        """
        # Defense-in-depth: Verdichter.call() already wraps its brain call
        # in its own asyncio.wait_for(timeout=cfg.timeout_s) — but a future
        # refactor or alternative Verdichter implementation might drop that
        # guard. The caller-side wait_for guarantees this code path can
        # never hang indefinitely no matter what changes inside Verdichter.
        # Buffer (+ 2.0s) so the inner timeout fires first under normal
        # operation and yields the clean error_reason="timeout" usage dict;
        # the outer wait_for only ever trips when the inner one is missing
        # or broken.
        _inner_timeout_s: float = float(
            getattr(getattr(self._verdichter, "_config", None), "timeout_s", 5.0)
        )
        try:
            summary, usage = await asyncio.wait_for(
                self._verdichter.call(
                    frames=snap.frames,
                    events=snap.events,
                    primary_app=snap.primary_app,
                ),
                timeout=_inner_timeout_s + 2.0,
            )
        except Exception:    # noqa: BLE001
            # Defensive: any verdichter failure (crash, outer-timeout trip,
            # cancellation) → persist an empty episode rather than lose it.
            logger.exception(
                "Verdichter call raised — persisting empty episode",
            )
            summary = ""
            usage = {
                "tokens_in": 0, "tokens_out": 0, "duration_ms": 0,
                "error_reason": "verdichter_exception",
            }

        ended_at_ns = time.time_ns()
        episode = Episode(
            started_at_ns=snap.started_at_ns,
            ended_at_ns=ended_at_ns,
            trigger_kind=snap.trigger_kind,
            summary=summary,
            frame_count=snap.frame_count,
            primary_app=snap.primary_app,
            tokens_in=int(usage.get("tokens_in", 0)),
            tokens_out=int(usage.get("tokens_out", 0)),
        )

        try:
            episode_id = await self._recall.record_episode(
                started_at_ns=episode.started_at_ns,
                ended_at_ns=episode.ended_at_ns,
                trigger_kind=snap.trigger_kind,
                summary=summary,
                frame_count=episode.frame_count,
                primary_app=snap.primary_app,
                tokens_in=episode.tokens_in,
                tokens_out=episode.tokens_out,
            )
        except Exception:    # noqa: BLE001
            logger.exception("recall.record_episode failed — episode lost")
            return

        # Update state (snapshot_for_prompt will now reflect last_episode_summary).
        # Single-writer pattern from A2 — no lock needed; the tracker is the
        # sole mutator of both fields.
        self._manager.state.last_episode_summary = summary
        self._manager.state.last_episode_id = episode_id

        # Publish event — UI/flight-recorder listen for it.
        duration_ms = max(
            0, (ended_at_ns - episode.started_at_ns) // 1_000_000,
        )
        await self._bus.publish(EpisodeRecorded(
            episode_id=episode_id,
            summary_preview=summary[:80],
            primary_app=snap.primary_app,
            frame_count=episode.frame_count,
            duration_ms=int(duration_ms),
        ))

        logger.info(
            "Episode persisted: id=%d trigger=%s primary_app=%s frames=%d "
            "duration_s=%d tokens_in=%d tokens_out=%d",
            episode_id, snap.trigger_kind, snap.primary_app,
            episode.frame_count, duration_ms // 1000,
            episode.tokens_in, episode.tokens_out,
        )

    async def _maybe_flush(self, *, trigger_kind: str) -> None:
        """Convenience helper: lock acquire + snapshot + release + dispatch.

        Used by tests and the internal hard-timer loop. Production bus
        handlers use ``_extract_snapshot_locked`` directly to batch the
        snapshot with other state mutations in a single lock acquisition.

        IMPORTANT: this method acquires ``self._lock`` itself — callers
        must NOT hold the lock, or a deadlock will occur on the non-reentrant
        asyncio.Lock.
        """
        snap: _FlushSnapshot | None
        async with self._lock:
            snap = self._extract_snapshot_locked(trigger_kind=trigger_kind)
        if snap is not None:
            await self._run_flush(snap)

    # ---- Hard-Timer ---------------------------------------------------------

    async def _hard_timer_loop(self) -> None:
        """Every ``hard_timer_min`` minutes: call ``_maybe_flush`` with trigger='hard_timer'."""
        interval_s = self._config.hard_timer_min * 60
        try:
            while self._started:
                try:
                    await asyncio.sleep(interval_s)
                except asyncio.CancelledError:
                    break
                await self._maybe_flush(trigger_kind="hard_timer")
        except asyncio.CancelledError:
            pass
        except Exception:    # noqa: BLE001
            logger.exception("StoryTracker hard-timer loop crashed")
