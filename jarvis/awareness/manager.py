"""AwarenessManager — lifecycle holder and live-state access point.

Phase A1: extended with ``async start()`` / ``async stop()`` that start
and stop the watchers (``WindowFocusWatcher`` + ``IdleDetector``).
Backward-compat with A0: without the ``bus`` parameter the manager is a
pure state holder (start/stop are no-ops).

Dependency injection: the manager is built in ``jarvis.brain.factory``
and injected into ``AwarenessSnapshotTool`` and watchers. No singleton —
Plan §5 hard-negative explicitly forbids it.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.context import resolve_context
from jarvis.awareness.state import AwarenessState
from jarvis.awareness.working_set import WorkingSet

if TYPE_CHECKING:
    from jarvis.awareness.probes.base import Probe
    from jarvis.awareness.probes.filesystem import FileSystemProbe
    from jarvis.awareness.story import StoryTracker
    from jarvis.awareness.verdichter import Verdichter
    from jarvis.awareness.watchers.base import AwarenessWatcher
    from jarvis.core.bus import EventBus
    from jarvis.core.events import EpisodeRecorded, FrameUpdated

logger = logging.getLogger(__name__)

_STOP_TIMEOUT_S: float = 2.0    # Plan §5 + §10 hard-negative
_PROBE_BUDGET_S_DEFAULT: float = 0.2    # Plan §9 hard cap for probe_all


class AwarenessManager:
    """Holder of live state, watcher lifecycle, and (A2) StoryTracker.

    A0 use without ``bus``: state reads only (``manager.state``). Watchers
    are NOT started — suitable for tests and pure read-only scenarios.

    A1 use with ``bus``: ``await manager.start()`` builds watchers (according
    to config.watchers.enable_*) and starts them. ``await manager.stop()``
    cleans up in <2 s.

    A2 use: ``factory.py`` sets ``_verdichter`` and ``_story_tracker`` as
    post-init attributes after construction. The manager then automatically
    starts/stops StoryTracker in start()/stop() — if not set, this is a
    no-op (backward-compat).
    """

    def __init__(
        self,
        config: AwarenessConfig,
        *,
        bus: EventBus | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._state = AwarenessState()
        self._watchers: list[AwarenessWatcher] = []
        self._started: bool = False
        # A2 post-init attributes (set by factory). Initialized to None here
        # so that getattr reads in tests and lifecycle code do not raise
        # AttributeError.
        self._verdichter: Verdichter | None = None
        self._story_tracker: StoryTracker | None = None
        # A5 post-init attributes. Probes list is filled by the factory
        # (or stays empty if config.probes.enabled=False — probe_all is then
        # a no-op and returns an empty dict).
        self._probes: list[Probe] = []
        # Special subsystem: FileSystemProbe requires start/stop and is
        # therefore referenced separately.
        self._fs_probe: FileSystemProbe | None = None
        # A4 Working Set: per-manager LRU of active contexts. RAM-only,
        # no persistence (the DB holds everything). Plan §8 hard negative:
        # no singletons — one per manager instance.
        self._working_set: WorkingSet = WorkingSet()
        # State property for snapshot_for_prompt — kept in sync with
        # _working_set (single writer in manager).
        self._state.working_set = self._working_set

    # ---- Properties ---------------------------------------------------------

    @property
    def state(self) -> AwarenessState:
        """Current live state. Synchronous read for the awareness-snapshot tool."""
        return self._state

    @property
    def config(self) -> AwarenessConfig:
        return self._config

    @property
    def working_set(self) -> WorkingSet:
        """A4 Working Set — multi-context LRU. Read-only access for tools."""
        return self._working_set

    # ---- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start watchers according to config. Idempotent.

        If ``config.enabled is False`` OR ``bus is None`` → no-op.
        """
        if self._started:
            return
        if not self._config.enabled or self._bus is None:
            self._started = True
            return

        # Lazy import to avoid a module-level cycle (manager → watchers →
        # state → manager would theoretically be possible).
        from jarvis.awareness.privacy import PrivacyFilter

        privacy = PrivacyFilter(self._config)

        if self._config.watchers.enable_window:
            from jarvis.awareness.watchers.window import WindowFocusWatcher

            self._watchers.append(WindowFocusWatcher(
                manager=self, privacy=privacy, bus=self._bus,
            ))

        if self._config.watchers.enable_idle:
            from jarvis.awareness.watchers.idle import IdleDetector

            threshold_s = self._config.watchers.idle_threshold_minutes * 60
            self._watchers.append(IdleDetector(
                manager=self, bus=self._bus, threshold_s=threshold_s,
            ))

        # Start watchers in parallel — if one hangs it does not block the others.
        results = await asyncio.gather(
            *(w.start() for w in self._watchers),
            return_exceptions=True,
        )
        for w, res in zip(self._watchers, results, strict=True):
            if isinstance(res, Exception):
                logger.warning("Watcher %s start failed: %s", type(w).__name__, res)

        # Phase A5: FileSystemProbe requires its own start (watchdog thread).
        # GitProbe is stateless and needs no start call.
        if self._fs_probe is not None:
            try:
                await self._fs_probe.start()
            except Exception:    # noqa: BLE001
                logger.exception("FileSystemProbe.start() failed — continuing without FS-probe")

        # Phase A2: start StoryTracker (if set by factory). Only AFTER
        # watchers — the tracker subscribes to FrameUpdated events that come
        # from WindowFocusWatcher; no point if the watcher is not running yet.
        if self._story_tracker is not None:
            try:
                await self._story_tracker.start()
            except Exception:    # noqa: BLE001
                logger.exception("StoryTracker.start() failed — continuing without L2")

        # Phase A4 Working Set: subscribe to FrameUpdated (context promote)
        # and EpisodeRecorded (bind episode ID to slot). We let StoryTracker
        # subscribe first so its episode persist runs before our set_episode()
        # (order is not strictly required — both are idempotent — but this
        # matches the intended mental model).
        from jarvis.core.events import (  # noqa: PLC0415
            EpisodeRecorded as _EpisodeRecorded,
        )
        from jarvis.core.events import (
            FrameUpdated as _FrameUpdated,
        )

        self._bus.subscribe(_FrameUpdated, self._on_frame_updated_for_working_set)
        self._bus.subscribe(_EpisodeRecorded, self._on_episode_recorded_for_working_set)

        self._started = True

    async def stop(self) -> None:
        """Stop StoryTracker (final flush) and all watchers. <2 s total timeout."""
        if not self._started:
            return
        watchers = list(self._watchers)
        self._watchers = []
        self._started = False

        # Phase A2: stop StoryTracker first — it receives a final
        # flush(trigger="stop") and cancels the hard timer. Watchers are
        # stopped afterward; otherwise in-flight FrameUpdated events could
        # trigger a builder reset after the tracker has already stopped.
        story = self._story_tracker
        self._story_tracker = None
        if story is not None:
            try:
                await asyncio.wait_for(story.stop(), timeout=_STOP_TIMEOUT_S)
            except TimeoutError:
                logger.warning(
                    "StoryTracker.stop: timeout %.1fs", _STOP_TIMEOUT_S,
                )
            except Exception:    # noqa: BLE001
                logger.exception("StoryTracker.stop raised")

        # Phase A5: stop FileSystemProbe (join the observer thread).
        fs_probe = self._fs_probe
        self._fs_probe = None
        if fs_probe is not None:
            try:
                await asyncio.wait_for(fs_probe.stop(), timeout=_STOP_TIMEOUT_S)
            except TimeoutError:
                logger.warning("FileSystemProbe.stop: timeout %.1fs", _STOP_TIMEOUT_S)
            except Exception:    # noqa: BLE001
                logger.exception("FileSystemProbe.stop raised")

        if not watchers:
            return

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(w.stop() for w in watchers),
                    return_exceptions=True,
                ),
                timeout=_STOP_TIMEOUT_S,
            )
        except TimeoutError:
            logger.warning(
                "AwarenessManager.stop: timeout %.1fs during watcher shutdown",
                _STOP_TIMEOUT_S,
            )

    # ---- Probes (A5) -------------------------------------------------------

    async def probe_all(
        self, *, pid: int, process_name: str = "",
    ) -> dict[str, Any]:
        """Run all registered probes in parallel with a total budget cap.

        Returns a merged dict of all probe outputs (e.g. ``git_branch``,
        ``open_file_hint``). On timeout: empty dict. On probe exception
        (return_exceptions via gather): the probe field is absent from output.

        cwd is resolved here from pid via psutil — probes receive the
        resolved cwd and do not need to look it up themselves.

        Hard negative §9: errors NEVER propagate. Total budget enforced
        via asyncio.wait_for(timeout=config.probes.total_budget_ms / 1000).
        """
        if not self._probes:
            return {}
        budget_s = self._config.probes.total_budget_ms / 1000.0
        # Codex-BLOCKER-1-Fix (2026-04-26): cwd resolve MUST fall within the
        # total budget. psutil.cwd() can hang on Windows (UAC, disconnected
        # network drives). Cap cwd resolve at 25 % of the budget or 50 ms
        # max — on timeout: cwd=None, probes still run with reduced budget.
        cwd_budget_s = min(budget_s * 0.25, 0.05)
        try:
            cwd = await asyncio.wait_for(
                asyncio.to_thread(self._resolve_cwd, pid),
                timeout=cwd_budget_s,
            )
        except TimeoutError:
            cwd = None
        tasks = [
            p.probe(cwd=cwd, process_name=process_name) for p in self._probes
        ]
        # Remaining budget = total minus cwd time. Use 75 % pessimistically
        # so that even after a cwd timeout enough budget remains for probes.
        probes_budget_s = budget_s * 0.75
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=probes_budget_s,
            )
        except TimeoutError:
            logger.debug(
                "probe_all: probes-budget %.0fms exceeded — returning empty",
                probes_budget_s * 1000,
            )
            return {}
        out: dict[str, Any] = {}
        for r in results:
            if isinstance(r, dict):
                out.update(r)
        return out

    # ---- Working Set Bus-Handler (A4) --------------------------------------

    async def _on_frame_updated_for_working_set(
        self, ev: FrameUpdated,
    ) -> None:
        """Promote or insert the context for the current frame into the WorkingSet.

        The frame is read from ``state.current_frame`` (single-writer invariant
        from A1) — the bus event is only the trigger. Privacy-blocked frames
        are skipped (same hard-negatives as StoryTracker).

        On an app switch a ``ContextSwitched`` event is published so that
        UI and the flight recorder can observe the promotion.
        """
        if not ev.is_capture_allowed:
            return
        frame = self._state.current_frame
        if frame is None or not frame.is_capture_allowed:
            return

        old_root = self._working_set.current.project_root if self._working_set.current else None
        # resolve_context calls psutil.Process(pid).cwd() for IDE/terminal
        # frames; that can block the event loop on UAC prompts, disconnected
        # drives, or simply slow process introspection. Offload to a worker
        # thread (same pattern as the A5 probes path at line 261).
        ctx = await asyncio.to_thread(resolve_context, frame)
        evicted = self._working_set.observe(ctx)
        new_root = ctx.project_root

        if self._bus is not None and old_root is not None and old_root != new_root:
            from jarvis.core.events import ContextSwitched  # noqa: PLC0415

            try:
                await self._bus.publish(ContextSwitched(
                    from_context=old_root,
                    to_context=new_root,
                ))
            except Exception:    # noqa: BLE001
                logger.debug("ContextSwitched publish failed", exc_info=True)

        if evicted is not None:
            logger.debug(
                "WorkingSet evicted oldest context: project_root=%s last_seen_ns=%d",
                evicted.project_root, evicted.last_seen_ns,
            )

    async def _on_episode_recorded_for_working_set(
        self, ev: EpisodeRecorded,
    ) -> None:
        """Link the persisted episode ID to the current context slot.

        Hard negative §8: episodes remain in the DB even if the context
        pointer is evicted. We ONLY call ``set_episode`` — no DB mutation.
        If the slot has since been evicted (multi-context stress),
        set_episode silently swallows it (returns False).
        """
        current = self._working_set.current
        if current is None:
            return
        # Set the episode on the current top-of-LRU — that is the slot that
        # was active when the episode was flushed.
        # Heuristic accepted (Plan §8 — explicitly desired).
        self._working_set.set_episode(current.project_root, ev.episode_id)

    @staticmethod
    def _resolve_cwd(pid: int) -> str | None:
        """Fetch cwd via psutil. Returns None on any failure. Lazy import.

        Non-Windows: psutil works there too, but FrameSnapshots only arrive
        on Windows (WindowFocusWatcher is a no-op elsewhere). Defensive
        without an explicit platform check.
        """
        if pid <= 0:
            return None
        if os.name != "nt":
            # Non-Windows: psutil cwd works, but A5 is Win32-centric.
            # Probes themselves are platform-agnostic (GitProbe uses a real
            # subprocess), but for the awareness-watcher path non-nt is a
            # test environment — return None rather than risk a hang.
            return None
        try:
            import psutil  # noqa: PLC0415

            return psutil.Process(pid).cwd()
        except Exception:    # noqa: BLE001
            return None
