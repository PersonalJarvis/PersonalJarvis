"""Speaker output state, independent volume and cross-thread UI snapshots.

The real SpeechPipeline owns output mute. Every sink receives its snapshot;
muting never changes the microphone or overwrites the configured volume.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.events import PetVisibilityToggleRequested, VoiceSpeakerMuteChanged
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator[AudioChunk]:
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


@dataclass
class VolumePlayer:
    """The slice of ``AudioPlayer`` the volume path touches."""

    _volume: float = 1.0
    calls: list[float] = field(default_factory=list)
    muted: bool = False

    def set_muted(self, muted: bool) -> None:
        self.muted = muted

    def set_volume(self, volume: float) -> None:
        self.calls.append(volume)
        self._volume = max(0.0, min(1.0, float(volume)))

    def stop(self) -> None:
        return None


def _pipeline(bus: EventBus, *, volume: float = 1.0) -> tuple[SpeechPipeline, VolumePlayer]:
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = VolumePlayer(_volume=volume)
    pipeline._player = player  # type: ignore[assignment]
    return pipeline, player


def _collect(bus: EventBus, event_type: type) -> list:
    """Subscribe an async recorder (typed bus handlers are awaited)."""
    seen: list = []

    async def _record(event) -> None:
        seen.append(event)

    bus.subscribe(event_type, _record)
    return seen


async def _drain() -> None:
    """Let scheduled publish tasks run to completion."""
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# get_tts_volume
# ---------------------------------------------------------------------------


def test_get_tts_volume_reads_the_live_player() -> None:
    pipeline, player = _pipeline(EventBus(), volume=0.6)
    assert pipeline.get_tts_volume() == pytest.approx(0.6)
    player._volume = 0.0
    assert pipeline.get_tts_volume() == 0.0


def test_get_tts_volume_without_a_player_falls_back_to_what_was_asked() -> None:
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=None, enable_whisper_wake=False)
    pipeline._player = None  # type: ignore[assignment]
    assert pipeline.get_tts_volume() == 1.0  # nothing configured, nothing asked
    pipeline.set_tts_volume(0.0)
    assert pipeline.get_tts_volume() == 0.0


# ---------------------------------------------------------------------------
# set_tts_volume → VoiceSpeakerMuteChanged
# ---------------------------------------------------------------------------


async def test_output_snapshots_include_volume_and_ordered_revisions() -> None:
    bus = EventBus()
    pipeline, player = _pipeline(bus, volume=0.8)
    seen: list[VoiceSpeakerMuteChanged] = _collect(bus, VoiceSpeakerMuteChanged)

    pipeline.set_tts_volume(0.5, source="settings")  # audible → audible
    await _drain()
    assert [(e.muted, e.volume, e.revision) for e in seen] == [(False, 0.5, 1)]

    pipeline.set_tts_volume(0.0, source="pet")  # audible → silent
    await _drain()
    assert (seen[-1].muted, seen[-1].volume, seen[-1].source) == (False, 0.0, "pet")

    pipeline.set_tts_volume(0.0, source="pet")  # silent → silent
    await _drain()
    assert len(seen) == 3

    pipeline.set_tts_volume(0.7, source="settings")  # silent → audible
    await _drain()
    assert (seen[-1].muted, seen[-1].source, seen[-1].revision) == (False, "settings", 4)
    assert player.calls == [0.5, 0.0, 0.0, 0.7]


async def test_a_pipeline_that_boots_silent_reports_the_unmute() -> None:
    """The first change is compared against the volume in effect, not "audible"."""
    bus = EventBus()
    pipeline, _player = _pipeline(bus, volume=0.0)
    seen: list[VoiceSpeakerMuteChanged] = _collect(bus, VoiceSpeakerMuteChanged)

    pipeline.set_tts_volume(1.0)
    await _drain()
    assert [e.muted for e in seen] == [False]


async def test_a_call_from_another_thread_publishes_on_the_pipeline_loop() -> None:
    """The REST route and the Tk thread have no loop; the event must still
    reach subscribers on the backend loop — never a throwaway one."""
    bus = EventBus()
    pipeline, _player = _pipeline(bus)
    loop = asyncio.get_running_loop()
    pipeline._runtime_loop = loop
    delivered = asyncio.Event()
    seen: list[tuple[bool, int]] = []

    async def _record(event: VoiceSpeakerMuteChanged) -> None:
        seen.append((event.muted, threading.get_ident()))
        delivered.set()

    bus.subscribe(VoiceSpeakerMuteChanged, _record)
    await asyncio.to_thread(pipeline.set_speaker_muted, True, source="pet")
    await asyncio.wait_for(delivered.wait(), timeout=2.0)

    assert seen == [(True, threading.get_ident())]


def test_without_a_bus_the_volume_still_applies() -> None:
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=None, enable_whisper_wake=False)
    player = VolumePlayer()
    pipeline._player = player  # type: ignore[assignment]
    pipeline.set_tts_volume(0.0)
    assert player.calls == [0.0]
    assert pipeline.get_tts_volume() == 0.0


def test_the_speaker_disc_can_unmute_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reported bug: the disc muted, and every later click muted again."""
    from ui.orb import controls

    pipeline, player = _pipeline(EventBus(), volume=0.9)
    monkeypatch.setattr(runtime_refs, "_SPEECH_PIPELINE", [pipeline])

    assert controls.toggle_speaker_mute() is True
    assert player._volume == pytest.approx(0.9)
    assert player.muted is True
    assert controls.speaker_is_muted() is True

    assert controls.toggle_speaker_mute() is False
    assert player._volume == pytest.approx(0.9)
    assert player.muted is False
    assert controls.speaker_is_muted() is False


# ---------------------------------------------------------------------------
# The pet shortcut
# ---------------------------------------------------------------------------


async def test_pet_toggle_hotkey_publishes_the_visibility_request() -> None:
    bus = EventBus()
    pipeline = SpeechPipeline(
        tts=FakeTTS(), bus=bus, enable_whisper_wake=False, pet_toggle_hotkeys=("alt+win+p",)
    )
    seen: list[PetVisibilityToggleRequested] = _collect(bus, PetVisibilityToggleRequested)

    pipeline._dispatch_hotkey_event("pet_toggle")
    await _drain()

    assert [e.source for e in seen] == ["hotkey"]


def test_pet_toggle_is_armed_single_fire_and_only_when_bound() -> None:
    pipeline = SpeechPipeline(
        tts=FakeTTS(), bus=None, enable_whisper_wake=False, pet_toggle_hotkeys=("alt+win+p",)
    )
    bindings, edges = pipeline._build_hotkey_bindings()
    assert bindings["pet_toggle"] == ["alt+win+p"]
    assert "pet_toggle" not in edges

    pipeline.set_keybinds(pet_toggle=[])
    bindings, _edges = pipeline._build_hotkey_bindings()
    assert "pet_toggle" not in bindings
