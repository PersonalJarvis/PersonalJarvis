"""Process-wide screenshot sound works with voice, chat-only, and no audio."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jarvis.audio.chime import CHIME_SAMPLE_RATE, SCREEN_CAPTURE_PCM
from jarvis.audio.effects import AudioEffectsService
from jarvis.core.bus import EventBus
from jarvis.core.events import ScreenCaptureGrabbed


class RecordingPlayer:
    def __init__(self) -> None:
        self.plays: list[tuple[bytes, int | None]] = []

    async def play_pcm(self, pcm: bytes, sample_rate: int | None = None) -> None:
        self.plays.append((pcm, sample_rate))


async def _finish(service: AudioEffectsService) -> None:
    for _ in range(20):
        task = service._capture_task
        if task is None:
            return
        await asyncio.sleep(0)
    task = service._capture_task
    if task is not None:
        await task


async def test_capture_cue_reuses_shared_voice_player_without_blocking_bus(
    monkeypatch,
) -> None:
    service = AudioEffectsService()
    player = RecordingPlayer()
    service.bind_player(player)
    monkeypatch.setattr(
        service,
        "_load_config",
        lambda: SimpleNamespace(ui=SimpleNamespace(sound_effects=True)),
    )
    bus = EventBus()
    service.attach(bus)

    await asyncio.wait_for(
        bus.publish(ScreenCaptureGrabbed(width=1920, height=1080)), timeout=0.05
    )
    await _finish(service)

    assert player.plays == [(SCREEN_CAPTURE_PCM, CHIME_SAMPLE_RATE)]


async def test_chat_only_capture_lazily_creates_one_player(monkeypatch) -> None:
    service = AudioEffectsService()
    player = RecordingPlayer()
    config = SimpleNamespace(ui=SimpleNamespace(sound_effects=True))
    builds: list[object] = []
    monkeypatch.setattr(service, "_load_config", lambda: config)
    monkeypatch.setattr(
        service, "_make_player", lambda cfg: (builds.append(cfg), player)[1]
    )
    bus = EventBus()
    service.attach(bus)

    await bus.publish(ScreenCaptureGrabbed(width=800, height=600))
    await _finish(service)

    assert builds == [config]
    assert player.plays == [(SCREEN_CAPTURE_PCM, CHIME_SAMPLE_RATE)]


async def test_global_sound_switch_mutes_capture_cue(monkeypatch) -> None:
    service = AudioEffectsService()
    player = RecordingPlayer()
    service.bind_player(player)
    monkeypatch.setattr(
        service,
        "_load_config",
        lambda: SimpleNamespace(ui=SimpleNamespace(sound_effects=False)),
    )
    bus = EventBus()
    service.attach(bus)

    await bus.publish(ScreenCaptureGrabbed(width=800, height=600))
    await _finish(service)

    assert player.plays == []
