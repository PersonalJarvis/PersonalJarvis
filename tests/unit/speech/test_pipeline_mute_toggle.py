"""Pipeline coverage for the global voice mute toggle.

Trigger surface: the desktop mascot publishes ``VoiceMuteToggleRequested``
when the user double-clicks the sprite. The pipeline must:

1. Flip ``self._muted`` (and expose it via ``is_muted``).
2. Leave Jarvis's in-flight voice ALONE — mute is input-only (maintainer
   intent 2026-06-29: "mute my mic for Jarvis, do not mute Jarvis"). Calling
   ``player.stop()`` here used to abort the output stream mid-write and wedge
   the WASAPI device, killing the wake-mic afterwards.
3. Reject wake activations through ``_activation_allowed`` while muted.
4. Suppress every ``AnnouncementRequested`` / ``_speak`` exit while muted.
5. Broadcast a follow-up ``VoiceMuteChanged`` for UI / overlay mirrors.
6. Toggle back to unmuted on the next request (idempotent flip).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AnnouncementRequested,
    VoiceMuteChanged,
    VoiceMuteToggleRequested,
)
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


@dataclass
class FakePlayer:
    stop_calls: int = 0
    plays: int = 0

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self.plays += 1
        async for _ in chunks:
            pass

    def stop(self) -> None:
        self.stop_calls += 1


def _make_pipeline(bus: EventBus) -> tuple[SpeechPipeline, FakeTTS, FakePlayer]:
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    return pipeline, tts, player


@pytest.mark.asyncio
async def test_mute_toggle_flips_flag_and_broadcasts() -> None:
    bus = EventBus()
    pipeline, _tts, _player = _make_pipeline(bus)

    seen: list[VoiceMuteChanged] = []
    bus.subscribe(VoiceMuteChanged, lambda ev: seen.append(ev))

    assert pipeline.is_muted is False
    assert pipeline._activation_allowed() is True

    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))

    assert pipeline.is_muted is True
    assert pipeline._activation_allowed() is False
    assert len(seen) == 1
    assert seen[0].muted is True
    assert seen[0].source == "mascot_dblclick"


@pytest.mark.asyncio
async def test_mute_toggle_is_idempotent_flip() -> None:
    bus = EventBus()
    pipeline, _tts, _player = _make_pipeline(bus)

    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))
    assert pipeline.is_muted is True

    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))
    assert pipeline.is_muted is False
    assert pipeline._activation_allowed() is True


@pytest.mark.asyncio
async def test_mute_suppresses_announcements() -> None:
    bus = EventBus()
    pipeline, tts, player = _make_pipeline(bus)

    # Unmuted baseline — announcement synthesises.
    await bus.publish(
        AnnouncementRequested(text="hallo welt", language="de", priority="normal")
    )
    assert tts.calls == [("hallo welt", "de-DE")]
    assert player.plays == 1

    # Mute, then announce: nothing should reach TTS or player.
    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))
    await bus.publish(
        AnnouncementRequested(text="zweiter satz", language="de", priority="normal")
    )

    assert tts.calls == [("hallo welt", "de-DE")]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_mute_does_not_stop_inflight_playback() -> None:
    """Mute is INPUT-ONLY: it must NOT abort Jarvis's in-flight voice.

    The maintainer asked for "mute my mic FOR Jarvis, do not mute Jarvis — other
    things keep working" (2026-06-29). The old behaviour called ``player.stop()``
    on mute, which ran ``stream.abort()`` (Pa_AbortStream) mid-TTS-write. That
    (a) contradicted the input-only intent and (b) wedged the shared WASAPI
    device: the next wake-mic opened "successfully" but delivered only dead/silent
    frames (no rolling-whisper transcripts, and the 3 s no-chunk Mic-Stall
    watchdog never fired because chunks kept arriving), so "Hey <wake>" silently
    stopped working after a mute-during-speech + hangup. Jarvis now finishes the
    current sentence; new ``_speak`` calls stay suppressed and input frames are
    dropped at our boundary (see ``test_session_input_is_dropped_while_muted``).
    """
    bus = EventBus()
    pipeline, _tts, player = _make_pipeline(bus)

    assert player.stop_calls == 0
    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))
    assert player.stop_calls == 0  # mute no longer aborts the output stream


@pytest.mark.asyncio
async def test_speak_short_circuits_when_muted() -> None:
    bus = EventBus()
    pipeline, tts, player = _make_pipeline(bus)

    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))

    barged = await pipeline._speak("hallo", language="de")
    assert barged is False
    assert tts.calls == []
    assert player.plays == 0


@pytest.mark.asyncio
async def test_session_input_is_dropped_while_muted() -> None:
    """Input mute: while muted Jarvis must STOP HEARING the user mid-session.

    The mute flag gated wake activation + TTS output, but the active-session
    input stream (mic -> VAD -> STT) ignored it, so a user who muted during a
    conversation kept being heard ("ich rede, aber er hoert mich trotzdem",
    2026-06-28). Muting drops every frame at Jarvis' input boundary WITHOUT
    touching the OS mic, so other apps still get the microphone.
    """
    bus = EventBus()
    pipeline, _tts, _player = _make_pipeline(bus)

    async def _src() -> AsyncIterator[AudioChunk]:
        for i in range(3):
            yield AudioChunk(
                pcm=b"\x01\x00" * 1600, sample_rate=16_000, timestamp_ns=i + 1
            )

    # Unmuted: every frame reaches the VAD.
    out = [c async for c in pipeline._session_input_stream(_src())]
    assert len(out) == 3

    # Muted: every frame is dropped — Jarvis is deaf to the user.
    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))
    out_muted = [c async for c in pipeline._session_input_stream(_src())]
    assert out_muted == []

    # Unmute again: hearing resumes.
    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))
    out_resumed = [c async for c in pipeline._session_input_stream(_src())]
    assert len(out_resumed) == 3


@pytest.mark.asyncio
async def test_activation_gate_blocked_even_when_external_gate_says_yes() -> None:
    """Mute must beat the external gate — UI saying ``yes`` cannot un-mute."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = SpeechPipeline(
        tts=tts,
        bus=bus,
        enable_whisper_wake=False,
        activation_gate=lambda: True,
    )
    pipeline._player = player  # type: ignore[assignment]

    assert pipeline._activation_allowed() is True

    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))
    assert pipeline._activation_allowed() is False
