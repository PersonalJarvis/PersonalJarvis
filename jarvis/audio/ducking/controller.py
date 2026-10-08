"""AudioDuckController — mutes other apps' audio for the duration of a session.

Subscribes ``VoiceSessionStarted`` (mute others) / ``VoiceSessionEnded``
(restore). An owned task runs blocking backend work in ``asyncio.to_thread``
with ``CoInitialize`` without delaying the wake publisher. Our own PID is excluded
from the mute sweep, which automatically protects Jarvis's own TTS voice.

A session never asks the OS for anything: ``mute_others`` is non-interactive.
The only asking path is :meth:`AudioDuckController.set_enabled` (the user turning
the feature on), which runs the backend's ``prewarm`` off ``self._lock``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Coroutine
from typing import Any

from jarvis.audio.ducking.factory import make_audio_ducker
from jarvis.core.events import VoiceSessionEnded, VoiceSessionStarted

log = logging.getLogger("jarvis.audio.ducking")

_SHUTDOWN_LOCK_TIMEOUT_S = 0.25


class AudioDuckController:
    def __init__(self, bus: Any, cfg: Any, ducker: Any) -> None:
        self._bus = bus
        self._cfg = cfg
        self._ducker = ducker
        self._muted: list[int] = []
        self._own_pid = os.getpid()
        self._lock = asyncio.Lock()
        # The worker owns both the native effect and its receipt. Cancelling an
        # asyncio waiter must not lose a late mute, including during shutdown.
        self._native_lock = threading.Lock()
        self._closed = threading.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._duck_requested = False
        # Bumped on every session start. A delayed _on_end restore only runs if
        # the generation is unchanged — so a new session that starts during the
        # restore-delay window keeps the music muted (its own _on_end restores).
        self._session_gen = 0

    def attach(self) -> None:
        try:
            self._bus.subscribe(VoiceSessionStarted, self._on_start)
            self._bus.subscribe(VoiceSessionEnded, self._on_end)
            log.info("AudioDuckController attached (enabled=%s)", self._enabled())
        except Exception:  # noqa: BLE001
            log.exception("AudioDuckController.attach failed")

    # ---- config helpers -------------------------------------------------
    def _enabled(self) -> bool:
        return bool(getattr(self._cfg.ducking, "enabled", False))

    def _never(self) -> frozenset[str]:
        return frozenset(getattr(self._cfg.ducking, "never_mute", []) or [])

    def _restore_delay_s(self) -> float:
        return max(0.0, getattr(self._cfg.ducking, "restore_delay_ms", 400) / 1000.0)

    # ---- bus handlers ---------------------------------------------------
    async def _on_start(self, _ev: Any) -> None:
        if not self._enabled() or self._closed.is_set():
            return
        self._session_gen += 1
        self._duck_requested = True
        self._schedule(self._mute_locked(self._session_gen, self._never()))

    async def _on_end(self, _ev: Any) -> None:
        # Keep cleanup owned even when the publisher is cancelled at hangup.
        await asyncio.shield(self._schedule(self._restore_after_delay(self._session_gen)))

    async def _restore_after_delay(self, gen: int) -> None:
        delay = self._restore_delay_s()
        if delay > 0:
            await asyncio.sleep(delay)  # let the TTS tail finish before music returns
        if self._session_gen != gen:
            # A new session started during the delay — leave the music muted; its
            # own _on_end will restore. (Avoids un-muting an active session.)
            return
        self._duck_requested = False
        await self._restore_locked(gen)

    # ---- public live controls ------------------------------------------
    async def set_enabled(self, enabled: bool) -> Any:
        """Live-apply the toggle. Turning OFF mid-session restores immediately.

        Turning ON is the user's gesture, so it is the ONE place that may ask the
        OS for a permission: the backend's ``prewarm`` (macOS: Automation consent
        for each running player, through the permission service) runs here, on a
        worker thread and NOT under ``self._lock``, so a session that starts while
        a dialog is open is never held up by it. Returns the backend's permission
        report (``DuckPermissionReport``), or ``None`` when the backend has none
        (Windows, no backend) or the feature was switched off.
        """
        try:
            self._cfg.ducking.enabled = bool(enabled)
        except Exception:  # noqa: BLE001
            log.debug("in-memory ducking.enabled update skipped", exc_info=True)
        if not enabled:
            await self.restore()
            return None
        pre = getattr(self._ducker, "prewarm", None)
        if callable(pre):
            report = await self._run(pre)
            # _run answers [] when the backend call failed (already logged).
            return report if hasattr(report, "as_dict") else None
        return None

    async def restore(self) -> None:
        """Force-restore (live path: turning the toggle off)."""
        self._session_gen += 1
        self._duck_requested = False
        await asyncio.shield(self._schedule(self._restore_locked(self._session_gen)))

    def restore_sync(self) -> None:
        """Synchronous force-restore for shutdown (no event loop available).

        Close admission before briefly waiting for the native operation lock.
        A wedged audio driver must not hold desktop shutdown indefinitely. If
        the wait expires, the worker restores its own late mute on return,
        independently of the event loop.
        """
        self._closed.set()
        if not self._native_lock.acquire(timeout=_SHUTDOWN_LOCK_TIMEOUT_S):
            log.warning("ducking: shutdown cleanup is pending on a native audio operation")
            return
        try:
            self._call(self._restore_receipt)
        except Exception:  # noqa: BLE001
            log.debug("shutdown restore failed", exc_info=True)
        finally:
            self._native_lock.release()

    # ---- internals ------------------------------------------------------
    def _schedule(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(work, name="audio-ducking")
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error("ducking task failed", exc_info=(type(error), error, error.__traceback__))

    async def _mute_locked(self, gen: int, never: frozenset[str]) -> None:
        async with self._lock:
            await self._run(self._mute_native, gen, never)

    def _mute_native(self, gen: int, never: frozenset[str]) -> None:
        with self._native_lock:
            if (
                self._closed.is_set()
                or not self._enabled()
                or not self._duck_requested
                or self._session_gen != gen
                or self._muted
            ):
                return
            self._muted = self._ducker.mute_others(own_pid=self._own_pid, never=never)
            log.info("ducking: muted %d other session(s)", len(self._muted))
            if self._closed.is_set() or not self._enabled() or not self._duck_requested:
                self._restore_receipt()

    async def _restore_locked(self, gen: int) -> None:
        async with self._lock:
            await self._run(self._restore_native, gen)

    def _restore_native(self, gen: int | None = None) -> None:
        with self._native_lock:
            # Recheck here too: a new start can arrive while this worker waits
            # for an earlier native call, even if the asyncio check was current.
            if gen is not None and self._session_gen != gen:
                return
            self._restore_receipt()

    def _restore_receipt(self) -> None:
        """Restore under the native lock; retain the receipt if the backend raises."""
        if not self._muted:
            return
        pids = self._muted
        self._ducker.restore(pids)
        self._muted = []
        log.info("ducking: restored %d session(s)", len(pids))

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking backend call off the loop, with COM init on the worker.

        Never called with ``self._lock`` held around an ask: only the silent,
        bounded ``mute_others`` / ``restore`` calls run under the lock.
        """

        try:
            return await asyncio.to_thread(self._call, fn, *args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("ducking COM call failed")
            return []

    @staticmethod
    def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
        initialized = False
        if os.name == "nt":
            try:
                import comtypes  # noqa: PLC0415

                comtypes.CoInitialize()
                initialized = True
            except Exception:  # noqa: BLE001 — optional COM bridge or existing apartment
                log.debug("ducking COM initialization unavailable", exc_info=True)
        try:
            return fn(*args, **kwargs)
        finally:
            if initialized:
                try:
                    comtypes.CoUninitialize()
                except Exception:  # noqa: BLE001 — routine teardown
                    log.debug("ducking COM teardown failed", exc_info=True)


def make_audio_duck_controller(bus: Any, cfg: Any) -> AudioDuckController:
    """Construct a controller with the platform-appropriate ducker backend."""
    return AudioDuckController(bus=bus, cfg=cfg, ducker=make_audio_ducker(cfg))
