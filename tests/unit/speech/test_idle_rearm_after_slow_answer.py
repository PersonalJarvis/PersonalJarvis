"""A slow answer must not be hung up on seconds after it lands.

Forensic 2026-06-27 08:49: a voice "switch the worker from antigravity to codex"
was buffered by the delegation grace and dispatched off the main loop (the
completion timer). The whole ~24 s turn (think + speak) therefore ran ALONGSIDE
the idle window that was armed at the user's utterance, so the 30 s window
expired 6 s after Jarvis finished — the session hung up before the user could
respond. "Only with CLI" because tool turns are the slow ones.

Fix: stamp the moment Jarvis stops speaking (SPEAKING -> LISTENING) and let the
idle loop grant ONE fresh window while within that grace.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


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


def _pipeline() -> SpeechPipeline:
    return SpeechPipeline(tts=FakeTTS(), bus=EventBus(), enable_whisper_wake=False)


@pytest.mark.asyncio
async def test_speaking_to_listening_stamps_answer_floor() -> None:
    pipe = _pipeline()
    pipe._last_answer_floor_monotonic = None
    await pipe._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    assert pipe._last_answer_floor_monotonic is not None


@pytest.mark.asyncio
async def test_other_transitions_do_not_stamp_answer_floor() -> None:
    # Going to LISTENING from a non-speaking state (e.g. session start) is not a
    # finished answer — no fresh-window grace owed.
    pipe = _pipeline()
    pipe._last_answer_floor_monotonic = None
    await pipe._set_turn_state(TurnTakingState.IDLE)
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    assert pipe._last_answer_floor_monotonic is None


def test_within_post_answer_grace_true_right_after_answer() -> None:
    pipe = _pipeline()
    pipe._idle_timeout_s = 30.0
    pipe._last_answer_floor_monotonic = time.monotonic()
    assert pipe._within_post_answer_grace() is True


def test_within_post_answer_grace_false_without_answer() -> None:
    pipe = _pipeline()
    pipe._idle_timeout_s = 30.0
    pipe._last_answer_floor_monotonic = None
    assert pipe._within_post_answer_grace() is False


def test_within_post_answer_grace_false_after_window_elapsed() -> None:
    pipe = _pipeline()
    pipe._idle_timeout_s = 30.0
    # Answer finished well over one idle window ago → no more grace.
    pipe._last_answer_floor_monotonic = time.monotonic() - 31.0
    assert pipe._within_post_answer_grace() is False
