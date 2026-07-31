"""Process-wide, best-effort playback for short product sound effects.

Attaching this service imports no native audio backend and opens no device. The
first audible event reuses the SpeechPipeline player when one exists, or lazily
creates one standalone player for a chat-only desktop. Headless hosts log and
continue without audio.
"""
from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any

from jarvis.audio.chime import CHIME_SAMPLE_RATE, SCREEN_CAPTURE_PCM
from jarvis.core.events import ScreenCaptureGrabbed

log = logging.getLogger(__name__)

_PLAYBACK_TIMEOUT_S = 1.0


class AudioEffectsService:
    """Own one serialized capture cue across voice and chat surfaces."""

    def __init__(self) -> None:
        self._attached_buses: weakref.WeakSet[Any] = weakref.WeakSet()
        self._shared_player_ref: weakref.ReferenceType[Any] | None = None
        self._owned_player: Any | None = None
        self._capture_task: asyncio.Task[None] | None = None

    def attach(self, bus: Any) -> None:
        """Subscribe once to a bus without initializing audio."""
        if not hasattr(bus, "subscribe") or bus in self._attached_buses:
            return
        bus.subscribe(ScreenCaptureGrabbed, self._on_screen_capture_grabbed)
        self._attached_buses.add(bus)

    def bind_player(self, player: Any) -> None:
        """Prefer the SpeechPipeline's shared, lock-serialized player."""
        try:
            self._shared_player_ref = weakref.ref(player)
        except TypeError:
            # Extension-backed player objects may not support weak references.
            self._shared_player_ref = lambda: player  # type: ignore[assignment]

    async def _on_screen_capture_grabbed(
        self, _event: ScreenCaptureGrabbed
    ) -> None:
        """Schedule and return; EventBus publication must never wait on audio."""
        current = self._capture_task
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._play_capture(), name="screen-capture-cue")
        self._capture_task = task

        def _clear(done: asyncio.Task[None]) -> None:
            if self._capture_task is done:
                self._capture_task = None

        task.add_done_callback(_clear)

    async def _play_capture(self) -> None:
        try:
            config = await asyncio.to_thread(self._load_config)
            if not bool(getattr(getattr(config, "ui", None), "sound_effects", True)):
                return
            player = self._bound_player()
            if player is None:
                player = await asyncio.to_thread(self._make_player, config)
                self._owned_player = player
            await asyncio.wait_for(
                player.play_pcm(SCREEN_CAPTURE_PCM, sample_rate=CHIME_SAMPLE_RATE),
                timeout=_PLAYBACK_TIMEOUT_S,
            )
        except TimeoutError:
            log.warning("Screen capture cue timed out and was safely aborted.")
        except Exception as exc:  # noqa: BLE001 - headless/no-audio is supported
            log.info("Screen capture cue unavailable; continuing without audio (%s).", exc)

    def _bound_player(self) -> Any | None:
        if self._shared_player_ref is not None:
            player = self._shared_player_ref()
            if player is not None:
                return player
        return self._owned_player

    @staticmethod
    def _load_config() -> Any:
        from jarvis.core.config import load_config  # noqa: PLC0415

        return load_config()

    @staticmethod
    def _make_player(config: Any) -> Any:
        from jarvis.audio.player import AudioPlayer  # noqa: PLC0415

        audio = getattr(config, "audio", None)
        tts = getattr(config, "tts", None)
        return AudioPlayer(
            device=getattr(audio, "output_device", None),
            volume=getattr(tts, "volume", 1.0),
            device_priority=tuple(
                getattr(audio, "output_device_priority", None) or ()
            ),
        )


_SERVICE = AudioEffectsService()


def attach_audio_effects(bus: Any) -> None:
    _SERVICE.attach(bus)


def bind_shared_audio_player(player: Any) -> None:
    _SERVICE.bind_player(player)


def get_audio_effects_service() -> AudioEffectsService:
    """Return the singleton for lifecycle integration and focused tests."""
    return _SERVICE


__all__ = [
    "AudioEffectsService",
    "attach_audio_effects",
    "bind_shared_audio_player",
    "get_audio_effects_service",
]
