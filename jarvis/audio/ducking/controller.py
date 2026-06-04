"""AudioDuckController — mutes other apps' audio for the duration of a session.

Subscribes ``VoiceSessionStarted`` (mute others) / ``VoiceSessionEnded``
(restore). The blocking pycaw/COM work runs in ``asyncio.to_thread`` with
``CoInitialize`` so it never touches the event loop. Our own PID is excluded
from the mute sweep, which automatically protects Jarvis's own TTS voice.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from jarvis.audio.ducking.factory import make_audio_ducker
from jarvis.core.events import VoiceSessionEnded, VoiceSessionStarted

log = logging.getLogger("jarvis.audio.ducking")


class AudioDuckController:
    def __init__(self, bus: Any, cfg: Any, ducker: Any) -> None:
        self._bus = bus
        self._cfg = cfg
        self._ducker = ducker
        self._muted: list[int] = []
        self._own_pid = os.getpid()
        self._lock = asyncio.Lock()
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
        if not self._enabled():
            return
        async with self._lock:
            self._session_gen += 1  # every start owns the current generation
            if self._muted:  # already muted (carry the existing mute over)
                return
            self._muted = await self._run(
                self._ducker.mute_others, own_pid=self._own_pid, never=self._never()
            )
            log.info("ducking: muted %d other session(s)", len(self._muted))

    async def _on_end(self, _ev: Any) -> None:
        gen = self._session_gen
        delay = self._restore_delay_s()
        if delay > 0:
            await asyncio.sleep(delay)  # let the TTS tail finish before music returns
        if self._session_gen != gen:
            # A new session started during the delay — leave the music muted; its
            # own _on_end will restore. (Avoids un-muting an active session.)
            return
        await self._restore_locked()

    # ---- public live controls ------------------------------------------
    async def set_enabled(self, enabled: bool) -> None:
        """Live-apply the toggle. Turning OFF mid-session restores immediately."""
        try:
            self._cfg.ducking.enabled = bool(enabled)
        except Exception:  # noqa: BLE001
            log.debug("in-memory ducking.enabled update skipped", exc_info=True)
        if not enabled:
            await self._restore_locked()

    async def restore(self) -> None:
        """Force-restore (live path: turning the toggle off)."""
        await self._restore_locked()

    def restore_sync(self) -> None:
        """Synchronous force-restore for shutdown (no event loop available).

        Unmutes the sessions we muted directly on the calling thread (with COM
        init) so a quit/crash mid-session never leaves the user's music muted.
        """
        if not self._muted:
            return
        pids, self._muted = self._muted, []
        initialized = False
        try:
            import comtypes

            comtypes.CoInitialize()
            initialized = True
        except Exception:  # noqa: BLE001, S110 — non-Windows / already-init
            pass
        try:
            self._ducker.restore(pids)
            log.info("ducking: restored %d session(s) on shutdown", len(pids))
        except Exception:  # noqa: BLE001
            log.debug("shutdown restore failed", exc_info=True)
        finally:
            if initialized:
                try:
                    import comtypes

                    comtypes.CoUninitialize()
                except Exception:  # noqa: BLE001, S110 — routine teardown
                    pass

    # ---- internals ------------------------------------------------------
    async def _restore_locked(self) -> None:
        async with self._lock:
            if not self._muted:
                return
            pids, self._muted = self._muted, []
            await self._run(self._ducker.restore, pids)
            log.info("ducking: restored %d session(s)", len(pids))

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking pycaw call off the loop, with COM init on the worker."""

        def _call() -> Any:
            initialized = False
            try:
                import comtypes

                comtypes.CoInitialize()
                initialized = True
            except Exception:  # noqa: BLE001, S110 — non-Windows/already-init, routine
                pass
            try:
                return fn(*args, **kwargs)
            finally:
                if initialized:
                    try:
                        import comtypes

                        comtypes.CoUninitialize()
                    except Exception:  # noqa: BLE001, S110 — routine teardown
                        pass

        try:
            return await asyncio.to_thread(_call)
        except Exception:  # noqa: BLE001
            log.exception("ducking COM call failed")
            return []


def make_audio_duck_controller(bus: Any, cfg: Any) -> AudioDuckController:
    """Construct a controller with the platform-appropriate ducker backend."""
    return AudioDuckController(bus=bus, cfg=cfg, ducker=make_audio_ducker())
