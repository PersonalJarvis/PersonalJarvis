"""Realtime endpointing guards for orchestrator-backed voice turns."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.turn_planner import TurnPath, TurnPlan, TurnReason
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession


class _DelayedBrain:
    conversation_language = "en"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def plan_turn(self, _text: str) -> TurnPlan:
        return TurnPlan(
            path=TurnPath.ORCHESTRATOR,
            reasons=frozenset({TurnReason.LOCAL_STATE}),
            requires_evidence=True,
        )

    async def __call__(self, _text: str) -> str:
        self.started.set()
        await self.release.wait()
        return "The wiki contains three folders."


class _EndpointRaceWire:
    session_id = "endpoint-race-wire"
    supports_tool_updates = True
    creates_responses_automatically = False
    isolates_response_generations = True

    def __init__(self, brain: _DelayedBrain) -> None:
        self._brain = brain
        self.text_inputs: list[str] = []
        self.text_sent = asyncio.Event()
        self.interrupts = 0
        self.closed = False

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="Search my wiki.",
            is_final=True,
            item_id="wiki-turn",
        )
        await self._brain.started.wait()
        # OpenAI server VAD can emit a new start edge for room noise after the
        # previous utterance was already committed. With no following final
        # transcript, this is not evidence that the user interrupted the turn.
        yield RealtimeEvent(type="speech_started")
        self._brain.release.set()
        await self.text_sent.wait()
        yield RealtimeEvent(
            type="output_transcript_delta",
            text="The wiki contains three folders.",
        )
        yield RealtimeEvent(type="turn_complete")

    async def send_audio(self, _chunk: Any) -> None:
        return None

    async def update_session(self, **_kwargs: Any) -> None:
        return None

    async def request_response(self, **_kwargs: Any) -> None:
        return None

    async def send_text(self, text: str) -> None:
        self.text_inputs.append(text)
        self.text_sent.set()

    async def truncate(self, _audio_end_ms: int) -> None:
        return None

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def send_tool_result(self, *_args: Any) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _EndpointRaceProvider:
    name = "endpoint-race"
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, brain: _DelayedBrain) -> None:
        self.session = _EndpointRaceWire(brain)

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, _config: Any) -> _EndpointRaceWire:
        return self.session


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


@pytest.mark.asyncio
async def test_unconfirmed_speech_start_does_not_abandon_pending_delegate() -> None:
    brain = _DelayedBrain()
    provider = _EndpointRaceProvider(brain)
    messages: list[dict[str, Any]] = []
    session = RealtimeVoiceSession(
        session_id="delegate-endpoint-race",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_config(),
        bus=None,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=brain,
    )

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        await asyncio.wait_for(provider.session.text_sent.wait(), timeout=0.5)
        await asyncio.wait_for(session.wait_finished(), timeout=0.5)
    finally:
        await session.end(reason="test")

    assert provider.session.interrupts == 0
    assert len(provider.session.text_inputs) == 1
    assert {
        "type": "transcript",
        "role": "assistant",
        "text": "The wiki contains three folders.",
        "is_final": False,
    } in messages
    assert {"type": "turn_complete"} in messages
