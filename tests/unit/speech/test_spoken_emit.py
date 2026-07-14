"""Every playback-confirmed phrase must be announced as ``SpeechSpoken``.

The SessionRecorder uses this authoritative audible track for normal replies
and supplemental output alike.

Two seams are under test:

- ``_emit_spoken(text, language, kind)`` — the fire-and-forget publish helper.
  It accepts playback-confirmed replies, suppresses empty text, and never raises
  when there is no bus.
- ``_speak(..., kind=...)`` — the universal non-streaming speak chokepoint.
  It emits canned phrases and normal replies only after playback succeeds.
- ``_announcement_spoken_kind`` — maps an ``AnnouncementRequested.kind`` to the
  spoken-track tag.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import ListeningStarted, SpeechSpoken
from jarvis.core.protocols import AudioChunk
from jarvis.sessions.constants import SPOKEN_KINDS
from jarvis.speech.pipeline import SpeechPipeline, _announcement_spoken_kind

# --- minimal __new__ pipe for the pure _emit_spoken helper -----------------


def _bare_pipe(bus: EventBus | None) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._bus = bus  # type: ignore[attr-defined]
    return pipe


async def _capture(bus: EventBus) -> list[SpeechSpoken]:
    captured: list[SpeechSpoken] = []

    async def _cap(e: SpeechSpoken) -> None:
        captured.append(e)

    bus.subscribe(SpeechSpoken, _cap)
    return captured


@pytest.mark.asyncio
async def test_emit_spoken_publishes_for_nonreply_kind() -> None:
    bus = EventBus()
    captured = await _capture(bus)
    pipe = _bare_pipe(bus)

    pipe._emit_spoken("That took too long.", "de", "timeout")
    await asyncio.sleep(0.05)  # let the fire-and-forget publish run

    assert len(captured) == 1, captured
    ev = captured[0]
    assert ev.text == "That took too long."
    assert ev.language == "de"
    assert ev.spoken_kind == "timeout"
    assert ev.spoken_kind in SPOKEN_KINDS


@pytest.mark.asyncio
async def test_emit_spoken_publishes_playback_confirmed_reply() -> None:
    bus = EventBus()
    captured = await _capture(bus)
    pipe = _bare_pipe(bus)

    pipe._emit_spoken("Hello, how can I help?", "de", "reply")
    await asyncio.sleep(0.05)

    assert len(captured) == 1
    assert captured[0].spoken_kind == "reply"
    assert captured[0].text == "Hello, how can I help?"


@pytest.mark.asyncio
async def test_emit_spoken_skips_empty_text() -> None:
    bus = EventBus()
    captured = await _capture(bus)
    pipe = _bare_pipe(bus)

    pipe._emit_spoken("   ", "de", "timeout")
    await asyncio.sleep(0.05)

    assert captured == []


@pytest.mark.asyncio
async def test_emit_spoken_is_a_noop_without_a_bus() -> None:
    pipe = _bare_pipe(None)
    # Must not raise even though there is no bus to publish on.
    pipe._emit_spoken("Still working on it.", "en", "progress")


def test_announcement_spoken_kind_mapping() -> None:
    assert _announcement_spoken_kind("preamble") == "preamble"
    assert _announcement_spoken_kind("completion") == "completion"
    assert _announcement_spoken_kind("progress") == "progress"
    # "info" and the legacy None default both fall back to the generic tag.
    assert _announcement_spoken_kind("info") == "announcement"
    assert _announcement_spoken_kind(None) == "announcement"
    # Every produced kind is part of the documented vocabulary.
    for produced in ("preamble", "completion", "progress", "announcement"):
        assert produced in SPOKEN_KINDS


# --- real _speak() wiring (fake TTS + player) ------------------------------


@dataclass
class _OneShotTTS:
    name: str = "one-shot-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(pcm=text.encode("utf-8"), sample_rate=24_000, timestamp_ns=0, channels=1)


@dataclass
class _CompletingPlayer:
    consumed: list[str] = field(default_factory=list)

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for chunk in chunks:
            self.consumed.append(chunk.pcm.decode("utf-8"))

    def stop(self) -> None:  # pragma: no cover - not hit on the happy path
        pass


@dataclass
class _DroppingPlayer:
    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> bool:
        async for _chunk in chunks:
            pass
        return False

    def stop(self) -> None:
        pass


def _make_speak_pipeline(bus: EventBus) -> SpeechPipeline:
    pipeline = SpeechPipeline(tts=_OneShotTTS(), bus=bus, enable_whisper_wake=False)
    pipeline._player = _CompletingPlayer()  # type: ignore[assignment]
    pipeline._latency_tracker = None

    async def _never_barge(**_kwargs) -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never_barge  # type: ignore[assignment]
    return pipeline


@pytest.mark.asyncio
async def test_speak_emits_spoken_for_a_canned_kind() -> None:
    bus = EventBus()
    captured = await _capture(bus)
    pipeline = _make_speak_pipeline(bus)

    await pipeline._speak("That took too long.", language="de", kind="timeout")
    await asyncio.sleep(0.05)

    assert len(captured) == 1, captured
    assert captured[0].spoken_kind == "timeout"
    assert captured[0].text == "That took too long."


@pytest.mark.asyncio
async def test_speak_default_reply_emits_after_playback() -> None:
    bus = EventBus()
    captured = await _capture(bus)
    pipeline = _make_speak_pipeline(bus)

    # The normal reply now joins the authoritative audible transcript track.
    await pipeline._speak("Hello, how can I help?", language="de")
    await asyncio.sleep(0.05)

    assert len(captured) == 1
    assert captured[0].spoken_kind == "reply"
    assert captured[0].text == "Hello, how can I help?"


@pytest.mark.asyncio
async def test_speak_does_not_record_audio_rejected_by_player() -> None:
    bus = EventBus()
    captured = await _capture(bus)
    pipeline = _make_speak_pipeline(bus)
    pipeline._player = _DroppingPlayer()  # type: ignore[assignment]

    await pipeline._speak("This must stay out of the transcript.", language="en")
    await asyncio.sleep(0.05)

    assert captured == []


@pytest.mark.asyncio
async def test_emit_spoken_carries_optional_detail() -> None:
    # A failed Computer-Use readback speaks a HUMANIZED sentence (no cryptic
    # "exit 5"), but the transcript should ALSO carry the technical reason —
    # the exit code + raw harness detail — for debugging. It rides the optional
    # `detail` field and is NEVER spoken (user request 2026-06-16).
    bus = EventBus()
    captured = await _capture(bus)
    pipe = _bare_pipe(bus)

    pipe._emit_spoken(
        "That didn't work on screen.",
        "en",
        "completion",
        detail="exit 5 · 5 guard-blocked actions this mission",
    )
    await asyncio.sleep(0.05)

    assert len(captured) == 1, captured
    assert captured[0].text == "That didn't work on screen."
    assert captured[0].detail == "exit 5 · 5 guard-blocked actions this mission"


@pytest.mark.asyncio
async def test_emit_spoken_detail_defaults_to_none() -> None:
    # The common case — a plain canned phrase — carries no technical detail.
    bus = EventBus()
    captured = await _capture(bus)
    pipe = _bare_pipe(bus)

    pipe._emit_spoken("That took too long.", "de", "timeout")
    await asyncio.sleep(0.05)

    assert len(captured) == 1
    assert captured[0].detail is None


@pytest.mark.asyncio
async def test_on_announcement_forwards_detail_to_spoken() -> None:
    # A CU failure rides AnnouncementRequested(kind="completion") carrying the
    # optional technical `detail`; it must reach the recorded SpeechSpoken so
    # the transcript shows the exit code while the voice stays humanized.
    from jarvis.core.events import AnnouncementRequested

    bus = EventBus()
    captured = await _capture(bus)
    pipeline = _make_speak_pipeline(bus)

    await pipeline._on_announcement(
        AnnouncementRequested(
            source_layer="brain.manager",
            text="That didn't work on screen.",
            language="en",
            kind="completion",
            detail="exit 5 · the BridgeMind server has no news channel",
        )
    )
    await asyncio.sleep(0.05)

    assert len(captured) == 1, captured
    assert captured[0].spoken_kind == "completion"
    assert captured[0].detail == "exit 5 · the BridgeMind server has no news channel"


@pytest.mark.asyncio
async def test_on_announcement_emits_spoken_with_mapped_kind() -> None:
    # Announcements (skill output, mission completion, spawn ack, progress
    # nudge, flash preamble) reach TTS through _on_announcement, not _speak —
    # so they need their own emit, tagged from the AnnouncementRequested.kind.
    from jarvis.core.events import AnnouncementRequested

    bus = EventBus()
    captured = await _capture(bus)
    pipeline = _make_speak_pipeline(bus)

    await pipeline._on_announcement(
        AnnouncementRequested(
            source_layer="missions.voice",
            text="The research is done.",
            language="en",
            kind="completion",
        )
    )
    await asyncio.sleep(0.05)

    assert len(captured) == 1, captured
    assert captured[0].spoken_kind == "completion"
    assert "research" in captured[0].text


@pytest.mark.asyncio
async def test_end_to_end_voiced_phrase_reaches_the_session_store(tmp_path) -> None:
    """The whole chain: the pipeline voices a timeout phrase, the passive
    SessionRecorder on the same bus persists it, and it shows up in the session
    log — the literal user requirement ('everything the TTS speaks documented
    there')."""
    from jarvis.core.events import VoiceSessionEnded, VoiceSessionStarted
    from jarvis.sessions.recorder import SessionRecorder
    from jarvis.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        pipeline = _make_speak_pipeline(bus)

        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="s1",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        await pipeline._speak(
            "That took too long.", language="de", kind="timeout"
        )
        await asyncio.sleep(0.05)  # let the fire-and-forget publish + record run
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="s1",
                hangup_reason="voice_pattern",
            )
        )

        spoken = [e for e in store.get_events("s1") if e.kind == "SpeechSpoken"]
        assert len(spoken) == 1, "the voiced timeout phrase never reached the log"
        assert spoken[0].payload["spoken_kind"] == "timeout"
        assert spoken[0].payload["text"] == "That took too long."
        assert spoken[0].turn_id is not None
    finally:
        store.close()
