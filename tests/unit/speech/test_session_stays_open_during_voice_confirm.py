"""The voice session must stay open while a two-turn confirmation is pending.

Forensic 2026-06-26: a voice "switch the subagent brain to antigravity" was an
``ask``-tier tool, so the brain spoke "really do that? say yes or no" and armed a
pending voice-confirm. But ``_finish_after_response`` only kept the session open
for ``_continue_listening_after_response`` / a background mission / a barge — it
had no idea a yes/no was awaited, so in single-turn mode the turn finalized and
the session hung up before the user could answer. These tests pin the same
courtesy that ``_background_mission_in_flight`` gives a running mission.
"""
from __future__ import annotations

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


class _BrainWithConfirm:
    """A brain callback exposing the pending-confirm probe (the real BrainManager
    shape). Callable so the pipeline's other paths still work."""

    def __init__(self, pending: bool) -> None:
        self.pending = pending

    def has_pending_voice_confirm(self) -> bool:
        return self.pending

    async def __call__(self, *a: object, **k: object) -> str:  # pragma: no cover
        return ""


def _pipeline() -> SpeechPipeline:
    return SpeechPipeline(tts=FakeTTS(), bus=EventBus(), enable_whisper_wake=False)


@pytest.mark.asyncio
async def test_finish_after_response_stays_listening_while_confirm_pending() -> None:
    pipe = _pipeline()
    pipe._continue_listening_after_response = False  # single-turn mode
    pipe._brain = _BrainWithConfirm(pending=True)

    # A yes/no is awaited → keep the floor so the answer can land.
    assert await pipe._finish_after_response(barged=False) is True
    assert pipe._turn_state == TurnTakingState.LISTENING
    assert pipe._session_end_reason is None


@pytest.mark.asyncio
async def test_finish_after_response_hangs_up_once_confirm_resolved() -> None:
    pipe = _pipeline()
    pipe._continue_listening_after_response = False  # single-turn mode
    brain = _BrainWithConfirm(pending=True)
    pipe._brain = brain

    assert await pipe._finish_after_response(barged=False) is True

    # Manager cleared the pending state after the user answered — single-turn
    # mode must hang up normally again on the next finalize.
    brain.pending = False
    assert await pipe._finish_after_response(barged=False) is False
    assert pipe._session_end_reason is not None


@pytest.mark.asyncio
async def test_finish_after_response_defensive_when_brain_lacks_probe() -> None:
    """A brain callback without ``has_pending_voice_confirm`` (the echo fake, an
    older build) must not crash the hangup decision — it reports no pending."""
    pipe = _pipeline()
    pipe._continue_listening_after_response = False
    # default self._brain is the plain echo coroutine — no probe method.
    assert await pipe._finish_after_response(barged=False) is False
    assert pipe._session_end_reason is not None
