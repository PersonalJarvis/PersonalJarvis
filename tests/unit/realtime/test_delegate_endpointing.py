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


class _InstantBrain:
    """Orchestrator-path brain that answers immediately."""

    conversation_language = "en"

    def __init__(self, reply: str = "Tomorrow is Friday.") -> None:
        self.reply = reply
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def plan_turn(self, _text: str) -> TurnPlan:
        return TurnPlan(
            path=TurnPath.ORCHESTRATOR,
            reasons=frozenset({TurnReason.LOCAL_STATE}),
            requires_evidence=True,
        )

    async def __call__(self, text: str) -> str:
        self.calls.append(text)
        self.started.set()
        await self.release.wait()
        return self.reply


class _AutoResponseWire:
    """Gemini-shaped wire: automatic responses, no manual response calls.

    ``script`` controls what the provider does after the final input
    transcript; a completely silent provider keeps the iterator open until
    the session closes it.
    """

    session_id = "auto-response-wire"
    supports_tool_updates = False
    creates_responses_automatically = True
    isolates_response_generations = True

    def __init__(self, brain: Any, *, script: str) -> None:
        self._brain = brain
        self._script = script
        self.text_inputs: list[str] = []
        self.text_sent = asyncio.Event()
        self.interrupts = 0
        self.closed = asyncio.Event()

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="What day is tomorrow?",
            is_final=True,
            item_id="date-turn",
        )
        if self._script == "silent":
            # The provider never answers, never calls the tool, and never
            # sends a boundary — the orchestrator must still act.
            await self.closed.wait()
            return
        if self._script == "interrupted-while-pending":
            await self._brain.started.wait()
            # A phantom VAD edge (noise) with no following final transcript.
            yield RealtimeEvent(type="interrupted")
            self._brain.release.set()
        await self.text_sent.wait()
        if self._script == "interrupted-after-delivery":
            # The trusted readback was injected but no PCM is audible yet.
            yield RealtimeEvent(type="interrupted")
        yield RealtimeEvent(
            type="output_transcript_delta",
            text="Tomorrow is Friday.",
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
        self.closed.set()


class _AutoResponseProvider:
    name = "auto-response"
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, brain: Any, *, script: str) -> None:
        self.session = _AutoResponseWire(brain, script=script)

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, _config: Any) -> _AutoResponseWire:
        return self.session


def _shorten_delegate_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.realtime.session as session_module

    monkeypatch.setattr(session_module, "_DELEGATE_INPUT_BOUNDARY_WAIT_S", 0.05)
    monkeypatch.setattr(session_module, "_DELEGATE_NATIVE_BOUNDARY_WAIT_S", 0.05)
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_WAIT_S", 0.1)
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_POLL_S", 0.02)


async def _run_auto_response_session(
    brain: Any,
    provider: _AutoResponseProvider,
    messages: list[dict[str, Any]],
    *,
    wait_finished: bool,
) -> RealtimeVoiceSession:
    session = RealtimeVoiceSession(
        session_id="delegate-boundary",
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
        await asyncio.wait_for(provider.session.text_sent.wait(), timeout=2.0)
        if wait_finished:
            await asyncio.wait_for(session.wait_finished(), timeout=2.0)
    finally:
        await session.end(reason="test")
    return session


@pytest.mark.asyncio
async def test_silent_provider_boundary_timeout_dispatches_instead_of_refusing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that never confirms the input must not veto the action.

    Live forensic 2026-07-16 10:26: Gemini produced neither a response nor a
    boundary for a complete question; the old timeout veto skipped the brain
    entirely and answered with the canned failure phrase.
    """
    _shorten_delegate_waits(monkeypatch)
    brain = _InstantBrain()
    provider = _AutoResponseProvider(brain, script="silent")
    messages: list[dict[str, Any]] = []

    await _run_auto_response_session(
        brain, provider, messages, wait_finished=False
    )

    assert brain.calls == ["What day is tomorrow?"]
    assert len(provider.session.text_inputs) == 1
    assert "Tomorrow is Friday." in provider.session.text_inputs[0]
    assert "didn't work" not in provider.session.text_inputs[0]


@pytest.mark.asyncio
async def test_undelivered_readback_falls_back_to_surface_tts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delivered result the provider never renders must still be heard.

    Gemini's realtime text stream carries no turn-end signal, so an injected
    result prompt may never start a response generation; a transport that
    died mid-turn renders nothing either. The readback watchdog speaks the
    trusted reply through the surface TTS instead of letting it vanish.
    """
    _shorten_delegate_waits(monkeypatch)
    brain = _InstantBrain()
    provider = _AutoResponseProvider(brain, script="silent")
    messages: list[dict[str, Any]] = []

    session = RealtimeVoiceSession(
        session_id="delegate-readback-watchdog",
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
        await asyncio.wait_for(provider.session.text_sent.wait(), timeout=2.0)
        for _ in range(100):
            if any(m.get("type") == "error_spoken" for m in messages):
                break
            await asyncio.sleep(0.02)
    finally:
        await session.end(reason="test")

    fallbacks = [m for m in messages if m.get("type") == "error_spoken"]
    assert fallbacks == [
        {
            "type": "error_spoken",
            "text": "Tomorrow is Friday.",
            "language": "en",
        }
    ]


@pytest.mark.asyncio
async def test_phantom_interrupted_edge_defers_while_delegate_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfirmed ``interrupted`` edge must not abandon a running action.

    Gemini reports noise blips and real barge-ins alike as ``interrupted``;
    during the silent thinking span there is no output to cut, so the edge is
    deferred exactly like an unconfirmed OpenAI speech start.
    """
    _shorten_delegate_waits(monkeypatch)
    brain = _DelayedBrain()
    provider = _AutoResponseProvider(brain, script="interrupted-while-pending")
    messages: list[dict[str, Any]] = []

    await _run_auto_response_session(
        brain, provider, messages, wait_finished=True
    )

    assert len(provider.session.text_inputs) == 1
    assert "three folders" in provider.session.text_inputs[0]
    assert {
        "type": "transcript",
        "role": "assistant",
        "text": "Tomorrow is Friday.",
        "is_final": False,
    } in messages
    assert {"type": "turn_complete"} in messages


@pytest.mark.asyncio
async def test_phantom_interrupted_after_delivery_keeps_the_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A VAD edge between result injection and first audible PCM must defer.

    Closing the turn in this window records a reply the user never heard and
    arms the barge-in drop flag against the very response that would have
    spoken it (live forensic 2026-07-16 10:26).
    """
    _shorten_delegate_waits(monkeypatch)
    brain = _InstantBrain()
    provider = _AutoResponseProvider(brain, script="interrupted-after-delivery")
    messages: list[dict[str, Any]] = []

    await _run_auto_response_session(
        brain, provider, messages, wait_finished=True
    )

    assert len(provider.session.text_inputs) == 1
    assert {
        "type": "transcript",
        "role": "assistant",
        "text": "Tomorrow is Friday.",
        "is_final": False,
    } in messages
    assert {"type": "turn_complete"} in messages
