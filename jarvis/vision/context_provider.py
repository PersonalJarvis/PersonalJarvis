"""VisionContextProvider - background cache of fresh screen observations.

Hybrid refresh strategy: an async background task refreshes every
`refresh_interval_s` via `VisionEngine.observe(mode=capture_mode)` and
keeps the latest observation in the cache. `current()` returns it
immediately - or forces a fresh capture if the cache is older than
`max_staleness_s` (the liveness guarantee).

Lifecycle:
    provider = VisionContextProvider(engine, bus=bus)
    await provider.start()       # task is running
    obs = await provider.current()
    provider.pause()             # privacy toggle
    provider.resume()
    await provider.stop()        # task canceled, < 500ms

While paused, `current()` raises `VisionPaused`. Exceptions in the loop are
logged; the loop keeps running (it only dies via stop()).

Design note (reality check: Provider.start() is sync-startable): `start()`
is declared `async def`, but only does `asyncio.create_task(...)` - that
runs in any event-loop context and does not block. Anyone starting the
provider from sync code can use `asyncio.run(provider.start())` or create
the task later in their own loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Literal

from jarvis.core.protocols import Observation

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.vision.engine import VisionEngine

log = logging.getLogger(__name__)

CaptureMode = Literal["auto", "screenshot", "ui_tree", "composite"]


class VisionPaused(RuntimeError):
    """Raised by VisionContextProvider.current() while paused."""


class VisionContextProvider:
    """Background cache layer in front of a VisionEngine.

    Keeps the most recent observation in memory and refreshes it
    periodically in the background. Consumers thus get screen context
    without the latency of a fresh capture - unless the cache is older
    than `max_staleness_s`, in which case it catches up synchronously.
    """

    def __init__(
        self,
        engine: VisionEngine,
        *,
        bus: EventBus | None = None,
        refresh_interval_s: float = 2.0,
        max_staleness_s: float = 2.0,
        capture_mode: CaptureMode = "screenshot",
    ) -> None:
        self._engine = engine
        self._bus = bus
        self._refresh_interval_s = float(refresh_interval_s)
        self._max_staleness_s = float(max_staleness_s)
        self._capture_mode: CaptureMode = capture_mode
        self._latest: Observation | None = None
        self._paused: bool = False
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False

    # ---------- Lifecycle ----------

    async def start(self) -> None:
        """Starts the background refresh loop on the current event loop.

        Idempotent: calling it again without a prior stop() is a no-op.
        """
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._refresh_loop(),
            name="vision-context-refresh",
        )

    async def stop(self) -> None:
        """Cancels the loop and waits at most 500ms for a clean shutdown.

        After stop(), the provider is startable again via start().
        """
        self._stopping = True
        t = self._task
        if t is None:
            return
        if not t.done():
            t.cancel()
            try:
                await asyncio.wait_for(t, timeout=0.5)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("VisionContextProvider stop() swallow: %s", exc)
        self._task = None

    # ---------- Public Access ----------

    async def current(self, *, force_refresh: bool = False) -> Observation:
        """Returns the most recent observation.

        Forces a fresh capture if (a) explicitly via `force_refresh`,
        (b) no observation exists yet, or (c) the existing one is older
        than `max_staleness_s`.

        Raises:
            VisionPaused: if the provider is currently paused.
        """
        if self._paused:
            raise VisionPaused("Vision paused")
        need_fresh = (
            force_refresh
            or self._latest is None
            or self._age_s(self._latest) > self._max_staleness_s
        )
        if need_fresh:
            obs = await self._engine.observe(mode=self._capture_mode)
            if obs is not None:
                # None = transient BitBlt skip; keep the stale observation
                # rather than replacing with None (caller gets something useful).
                self._latest = obs
            if self._latest is None:
                # No good observation yet AND current grab also failed —
                # propagate None so the caller can decide (e.g. skip vision context).
                return None  # type: ignore[return-value]
            return self._latest
        return self._latest

    def pause(self) -> None:
        """Privacy toggle: the loop stops refreshing, current() raises VisionPaused."""
        self._paused = True

    def resume(self) -> None:
        """Undoes pause(). The loop resumes refreshing on the next tick."""
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def latest(self) -> Observation | None:
        """Last cached observation without a refresh check (may be stale)."""
        return self._latest

    # ---------- Internals ----------

    @staticmethod
    def _age_s(obs: Observation) -> float:
        """Age of the observation in seconds, clamped to >= 0."""
        return max(0.0, time.time_ns() / 1e9 - obs.timestamp_ns / 1e9)

    async def _refresh_loop(self) -> None:
        """Background task: periodically call observe() and cache the result.

        Exceptions are logged but not propagated - the loop only dies via
        stop() (-> CancelledError).

        Error logging: the FIRST exception (or the first after 5 consecutive
        failures) is logged as `error` with a stack trace, so it's visible in
        the flight recorder + UI. Further errors of the same kind are only
        reported as `warning` without a trace, so the logs don't flood
        (typical case: mss fails due to an RDP lock screen and repeats every
        2s until unlock).

        None return from engine.observe(): ScreenshotSource already handled the
        BitBlt error (logged once), returning None means "skip this frame".
        We keep _latest as-is (last good observation), increment no error
        counter, and do NOT log here — the screenshot source owns that log.
        """
        consecutive_errors = 0
        while not self._stopping:
            try:
                if not self._paused:
                    obs = await self._engine.observe(mode=self._capture_mode)
                    if obs is None:
                        # Transient BitBlt skip — keep last good observation,
                        # do not count as a loop error (already logged at source).
                        pass
                    else:
                        self._latest = obs
                        if consecutive_errors > 0:
                            log.info(
                                "VisionContextProvider recovered after %d errors.",
                                consecutive_errors,
                            )
                        consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                consecutive_errors += 1
                # The first error and every 5th subsequent one: log loudly with a trace.
                if consecutive_errors == 1 or consecutive_errors % 5 == 0:
                    log.error(
                        "VisionContextProvider Loop-Exception (#%d): %s "
                        "(retry in %.2fs)",
                        consecutive_errors,
                        exc,
                        self._refresh_interval_s,
                        exc_info=True,
                    )
                else:
                    log.warning(
                        "VisionContextProvider Loop-Exception (#%d): %s",
                        consecutive_errors,
                        exc,
                    )
            try:
                await asyncio.sleep(self._refresh_interval_s)
            except asyncio.CancelledError:
                raise
