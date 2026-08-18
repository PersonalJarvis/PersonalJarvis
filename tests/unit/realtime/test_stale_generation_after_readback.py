"""Stale-generation guard after a delivered readback (BUG-143).

Live forensic 2026-08-18 14:25 (session 7b20e182, turns 4/5 and 6/7): on a
server-VAD transport one Jarvis turn carried TWO turn-ending inputs on the
server — the injected trusted result and the end of the user's trailing
speech — and the provider answered both, back to back. Every answer after
the injected result re-rendered that same result, so the user heard "Ich
habe work geöffnet: T eins." and then "Ich habe work geöffnet: T1.", and
the recorder wrote a phantom turn with no user text for the repeat.

The session cannot cancel that second generation (Gemini has no response
cancel). It refuses to play it instead: a generation that begins right after
a provider-rendered readback with NO new user input is discarded whole,
never opened as a turn. Fresh user input, local microphone voice, a
deliberate injection, or the bounded window each let the provider speak
again — the guard must never eat a genuine later answer.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.realtime.session as session_module
from jarvis.brain.turn_planner import TurnPath, TurnPlan, TurnReason
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession

REPLY = "I opened work: T1."
FOLLOW_UP_QUESTION = "And what time is it?"
FOLLOW_UP_ANSWER = "It is half past two."
PCM = AudioChunk(pcm=b"\x11\x00" * 480, sample_rate=24_000, timestamp_ns=0)


class _DelegatingBrain:
    """Routes the opening request to the orchestrator; a follow-up is native."""

    conversation_language = "en"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def plan_turn(self, text: str) -> TurnPlan:
        if text.strip().lower().startswith("open"):
            return TurnPlan(
                path=TurnPath.ORCHESTRATOR,
                reasons=frozenset({TurnReason.LOCAL_STATE}),
                requires_evidence=True,
            )
        return TurnPlan(path=TurnPath.NATIVE_REALTIME)

    async def __call__(self, text: str) -> str:
        self.calls.append(text)
        return REPLY


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)

    def subscribe(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def unsubscribe(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _ServerVadWire:
    """Gemini-shaped wire: automatic responses, no response cancel.

    ``script`` decides what follows the rendered readback of the delegate
    result:

    * ``second-generation`` — a second generation of the same reply starts
      immediately after the readback boundary, with no user input at all
      (the live failure).
    * ``after-user-input`` — the user asks a follow-up first; the provider's
      answer to it must play.
    * ``after-local-voice`` — no transcript yet, but the microphone carried
      the user's voice after the readback ended (a slow input transcription);
      the answer must play.
    * ``after-window`` — the second generation starts only after the guard's
      window ran out; it plays (fail-open bound).
    * ``late-result`` — Jarvis itself injects a late action result after the
      readback; the provider's rendering of THAT text must play.
    * ``stale-tool-call`` — the second generation asks for a tool (here the
      hang-up) instead of speaking; it must be refused, not executed.
    * ``user-input-mid-drop`` — the user's follow-up transcript lands while
      the stale generation is still streaming, BEFORE its boundary; that
      boundary must not close the follow-up's turn.
    """

    session_id = "server-vad-wire"
    supports_tool_updates = False
    creates_responses_automatically = True
    isolates_response_generations = True

    def __init__(self, *, script: str) -> None:
        self._script = script
        self.text_inputs: list[str] = []
        self.text_sent = asyncio.Event()
        self.second_text_sent = asyncio.Event()
        self.readback_done = asyncio.Event()
        self.before_second_generation: Any = None
        self.tool_results: list[tuple[Any, ...]] = []
        self.tool_result_sent = asyncio.Event()
        self.interrupts = 0
        self.closed = asyncio.Event()

    async def _readback(self, text: str):
        yield RealtimeEvent(type="output_transcript_delta", text=text)
        yield RealtimeEvent(type="audio_delta", audio=PCM)
        yield RealtimeEvent(type="turn_complete")

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="Open a terminal for me.",
            is_final=True,
            item_id="request",
        )
        await self.text_sent.wait()
        async for event in self._readback(REPLY):
            yield event
        self.readback_done.set()
        # Let the session finish the readback turn before the next generation.
        await asyncio.sleep(0.02)
        if self.before_second_generation is not None:
            await self.before_second_generation()
        if self._script == "second-generation":
            async for event in self._readback(REPLY):
                yield event
        elif self._script == "after-user-input":
            yield RealtimeEvent(
                type="input_transcript",
                text=FOLLOW_UP_QUESTION,
                is_final=True,
                item_id="follow-up",
            )
            async for event in self._readback(FOLLOW_UP_ANSWER):
                yield event
        elif self._script in {"after-local-voice", "after-window"}:
            async for event in self._readback(FOLLOW_UP_ANSWER):
                yield event
        elif self._script == "late-result":
            # The injected late result prompt arrives as a second text input;
            # render it like Gemini would.
            await self.second_text_sent.wait()
            async for event in self._readback("Also, the download finished."):
                yield event
        elif self._script == "stale-tool-call":
            # The stale generation calls a function instead of speaking. A
            # function call ends the generation without a boundary; the model
            # renders whatever result it gets in a follow-up generation.
            yield RealtimeEvent(type="tool_call", tool_name="end_call", call_id="c1")
            await self.tool_result_sent.wait()
            async for event in self._readback("Alright, goodbye."):
                yield event
        elif self._script == "user-input-mid-drop":
            yield RealtimeEvent(type="output_transcript_delta", text=REPLY)
            yield RealtimeEvent(type="audio_delta", audio=PCM)
            yield RealtimeEvent(
                type="input_transcript",
                text=FOLLOW_UP_QUESTION,
                is_final=True,
                item_id="follow-up",
            )
            # The stale generation's own boundary arrives only now.
            yield RealtimeEvent(type="turn_complete")
            async for event in self._readback(FOLLOW_UP_ANSWER):
                yield event
        await self.closed.wait()

    async def send_audio(self, _chunk: Any) -> None:
        return None

    async def update_session(self, **_kwargs: Any) -> None:
        return None

    async def request_response(self, **_kwargs: Any) -> None:
        return None

    async def send_text(self, text: str) -> None:
        self.text_inputs.append(text)
        self.text_sent.set()
        if len(self.text_inputs) >= 2:
            self.second_text_sent.set()

    async def truncate(self, _audio_end_ms: int) -> None:
        return None

    async def interrupt(self) -> None:
        # Gemini has no response cancel: this is a no-op on the live wire too.
        self.interrupts += 1

    async def send_tool_result(self, *args: Any) -> None:
        self.tool_results.append(args)
        self.tool_result_sent.set()

    async def close(self) -> None:
        self.closed.set()


class _ServerVadProvider:
    name = "server-vad"
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, *, script: str) -> None:
        self.session = _ServerVadWire(script=script)

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, _config: Any) -> _ServerVadWire:
        return self.session


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


def _shorten_delegate_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "_DELEGATE_INPUT_BOUNDARY_WAIT_S", 0.05)
    monkeypatch.setattr(session_module, "_DELEGATE_INPUT_BOUNDARY_POLL_S", 0.02)
    monkeypatch.setattr(session_module, "_DELEGATE_NATIVE_BOUNDARY_WAIT_S", 0.05)
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_WAIT_S", 0.5)
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_POLL_S", 0.02)


async def _run(
    provider: _ServerVadProvider,
    *,
    settle_s: float = 0.4,
) -> tuple[RealtimeVoiceSession, list[dict[str, Any]], list[bytes], _FakeBus]:
    brain = _DelegatingBrain()
    messages: list[dict[str, Any]] = []
    binaries: list[bytes] = []
    bus = _FakeBus()
    session = RealtimeVoiceSession(
        session_id="stale-generation-guard",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_config(),
        bus=bus,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=brain,
    )
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        await asyncio.wait_for(provider.session.text_sent.wait(), timeout=10.0)
        await asyncio.wait_for(provider.session.readback_done.wait(), timeout=10.0)
        # The scripted continuation streams while we wait here.
        await asyncio.sleep(settle_s)
    finally:
        await session.end(reason="test")
    return session, messages, binaries, bus


def _assistant_texts(messages: list[dict[str, Any]]) -> list[str]:
    return [
        str(m.get("text", ""))
        for m in messages
        if m.get("type") == "transcript" and m.get("role") == "assistant"
    ]


def _turn_starts(bus: _FakeBus) -> int:
    return sum(1 for e in bus.events if type(e).__name__ == "VoiceTurnStarted")


# ---------------------------------------------------------------------------
# The live failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_generation_after_readback_is_discarded_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readback plays once; the provider's unprompted repeat never reaches
    the surface, the recorder, or the turn record."""
    _shorten_delegate_waits(monkeypatch)
    provider = _ServerVadProvider(script="second-generation")

    session, messages, binaries, bus = await _run(provider)

    assert provider.session.text_inputs, "the trusted result was never injected"
    assert REPLY in provider.session.text_inputs[0]
    # Exactly one rendering reached the speaker and the caption.
    assert len(binaries) == 1
    assert _assistant_texts(messages) == [REPLY]
    # One surface turn boundary, one recorded turn — no phantom turn.
    assert sum(1 for m in messages if m.get("type") == "turn_complete") == 1
    assert _turn_starts(bus) == 1
    assert session._stale_generations_dropped == 1
    # The guard stood down with the discarded generation's own boundary.
    assert session._stale_generation_dropping is False
    assert session._stale_generation_guard_armed_at == 0.0


# ---------------------------------------------------------------------------
# Everything that must keep playing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answer_after_new_user_input_plays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _shorten_delegate_waits(monkeypatch)
    provider = _ServerVadProvider(script="after-user-input")

    session, messages, binaries, bus = await _run(provider)

    assert len(binaries) == 2
    assert _assistant_texts(messages) == [REPLY, FOLLOW_UP_ANSWER]
    assert _turn_starts(bus) == 2
    assert session._stale_generations_dropped == 0


@pytest.mark.asyncio
async def test_answer_after_local_microphone_voice_plays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow input transcription must not cost the user the answer: the
    microphone already carried their voice after the readback ended."""
    _shorten_delegate_waits(monkeypatch)
    provider = _ServerVadProvider(script="after-local-voice")
    holder: dict[str, RealtimeVoiceSession] = {}

    async def _user_speaks() -> None:
        # What handle_audio_frame stamps for a loud microphone frame.
        holder["session"]._last_voiced_input_monotonic = time.monotonic()

    provider.session.before_second_generation = _user_speaks

    brain = _DelegatingBrain()
    messages: list[dict[str, Any]] = []
    binaries: list[bytes] = []
    session = RealtimeVoiceSession(
        session_id="stale-generation-guard-voice",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_config(),
        bus=None,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=brain,
    )
    holder["session"] = session
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        await asyncio.wait_for(provider.session.readback_done.wait(), timeout=10.0)
        await asyncio.sleep(0.4)
    finally:
        await session.end(reason="test")

    assert len(binaries) == 2
    assert _assistant_texts(messages) == [REPLY, FOLLOW_UP_ANSWER]
    assert session._stale_generations_dropped == 0


@pytest.mark.asyncio
async def test_generation_after_the_window_plays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is bounded: past the window the provider speaks freely."""
    _shorten_delegate_waits(monkeypatch)
    monkeypatch.setattr(session_module, "_STALE_GENERATION_WINDOW_S", 0.05)
    provider = _ServerVadProvider(script="after-window")

    async def _wait_past_window() -> None:
        await asyncio.sleep(0.15)

    provider.session.before_second_generation = _wait_past_window

    session, messages, binaries, _bus = await _run(provider, settle_s=0.6)

    assert len(binaries) == 2
    assert _assistant_texts(messages) == [REPLY, FOLLOW_UP_ANSWER]
    assert session._stale_generations_dropped == 0


@pytest.mark.asyncio
async def test_late_result_injected_by_jarvis_plays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deliberate injection opens its own turn first, so its rendering is
    never mistaken for a stale generation."""
    _shorten_delegate_waits(monkeypatch)
    provider = _ServerVadProvider(script="late-result")
    holder: dict[str, RealtimeVoiceSession] = {}

    async def _inject_late_result() -> None:
        session = holder["session"]
        session._late_delegate_results.append(
            session_module._LateDelegateResult(
                text="Also, the download finished.",
                success=True,
                language="en",
                delivery_id="late-1",
            )
        )
        await session._flush_late_delegate_results()

    provider.session.before_second_generation = _inject_late_result

    brain = _DelegatingBrain()
    messages: list[dict[str, Any]] = []
    binaries: list[bytes] = []
    session = RealtimeVoiceSession(
        session_id="stale-generation-guard-late",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_config(),
        bus=None,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=brain,
    )
    holder["session"] = session
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        await asyncio.wait_for(provider.session.readback_done.wait(), timeout=10.0)
        await asyncio.sleep(0.6)
    finally:
        await session.end(reason="test")

    assert len(provider.session.text_inputs) == 2
    assert len(binaries) == 2
    assert _assistant_texts(messages) == [REPLY, "Also, the download finished."]
    assert session._stale_generations_dropped == 0


@pytest.mark.asyncio
async def test_tool_call_from_stale_generation_is_refused_not_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A phantom generation asking for an action — here the hang-up — gets an
    honest refusal result (so the model can close its generation) and nothing
    else: no turn, no execution, no audible rendering of the refusal."""
    _shorten_delegate_waits(monkeypatch)
    provider = _ServerVadProvider(script="stale-tool-call")

    session, messages, binaries, bus = await _run(provider, settle_s=0.6)

    assert len(provider.session.tool_results) == 1
    call_id, name, result = provider.session.tool_results[0]
    assert (call_id, name) == ("c1", "end_call")
    assert result["success"] is False
    # The hang-up never fired and no phantom turn exists.
    assert session._end_after_turn is False
    assert _turn_starts(bus) == 1
    # The refusal's rendering stayed inside the discarded generation.
    assert len(binaries) == 1
    assert _assistant_texts(messages) == [REPLY]
    assert session._stale_generations_dropped == 1
    assert session._stale_generation_dropping is False


@pytest.mark.asyncio
async def test_follow_up_landing_mid_drop_keeps_its_own_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stale generation's late boundary must not close the follow-up turn
    a real transcript opened during the drop: that turn waits for its own
    answer, which then plays; the request is never re-routed as an empty
    turn."""
    _shorten_delegate_waits(monkeypatch)
    provider = _ServerVadProvider(script="user-input-mid-drop")

    brain = _DelegatingBrain()
    messages: list[dict[str, Any]] = []
    binaries: list[bytes] = []
    bus = _FakeBus()
    session = RealtimeVoiceSession(
        session_id="stale-generation-guard-mid-drop",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_config(),
        bus=bus,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=brain,
    )
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        await asyncio.wait_for(provider.session.readback_done.wait(), timeout=10.0)
        await asyncio.sleep(0.6)
    finally:
        await session.end(reason="test")

    # The opening request was the only Brain turn: the follow-up was answered
    # natively, never recovered as an "empty" turn.
    assert brain.calls == ["Open a terminal for me."]
    assert _assistant_texts(messages) == [REPLY, FOLLOW_UP_ANSWER]
    assert len(binaries) == 2
    assert _turn_starts(bus) == 2
    assert session._stale_generations_dropped == 1


# ---------------------------------------------------------------------------
# Capability gate and predicate
# ---------------------------------------------------------------------------


def test_guard_never_arms_on_a_manual_response_transport() -> None:
    session = RealtimeVoiceSession(
        session_id="manual",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=_ServerVadProvider(script="second-generation"),
        config=_config(),
        bus=None,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=_DelegatingBrain(),
    )
    session._session = SimpleNamespace(creates_responses_automatically=False)
    session._arm_stale_generation_guard(REPLY)
    assert session._stale_generation_guard_armed_at == 0.0
    session._session = SimpleNamespace(creates_responses_automatically=True)
    session._arm_stale_generation_guard(REPLY)
    assert session._stale_generation_guard_armed_at > 0.0
    assert session._stale_generation_guard_reply == REPLY


def test_guard_reason_disarms_on_every_kind_of_fresh_evidence() -> None:
    session = RealtimeVoiceSession(
        session_id="predicate",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=_ServerVadProvider(script="second-generation"),
        config=_config(),
        bus=None,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=_DelegatingBrain(),
    )
    session._session = SimpleNamespace(creates_responses_automatically=True)

    def _reason_and_armed() -> tuple[str, float]:
        return (
            session._stale_generation_guard_reason(),
            session._stale_generation_guard_armed_at,
        )

    session._arm_stale_generation_guard(REPLY)
    assert session._stale_generation_guard_reason()
    assert session._stale_generation_guard_armed_at > 0.0

    # An open turn (transcript or deliberate injection) owns the output.
    session._turn_id = "turn"
    assert _reason_and_armed() == ("", 0.0)
    session._turn_id = ""

    # A confirmed server speech edge.
    session._arm_stale_generation_guard(REPLY)
    session._user_speech_active = True
    assert _reason_and_armed() == ("", 0.0)
    session._user_speech_active = False

    # Local microphone voice after the readback ended.
    session._arm_stale_generation_guard(REPLY)
    session._last_voiced_input_monotonic = time.monotonic() + 1.0
    assert _reason_and_armed() == ("", 0.0)
    session._last_voiced_input_monotonic = 0.0

    # The bounded window.
    session._arm_stale_generation_guard(REPLY)
    session._stale_generation_guard_armed_at -= session_module._STALE_GENERATION_WINDOW_S + 1.0
    assert _reason_and_armed() == ("", 0.0)


def test_drop_without_a_boundary_is_released_at_the_ceiling() -> None:
    session = RealtimeVoiceSession(
        session_id="ceiling",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=_ServerVadProvider(script="second-generation"),
        config=_config(),
        bus=None,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=_DelegatingBrain(),
    )
    session._stale_generation_dropping = True
    session._stale_generation_dropping_since = time.monotonic()
    assert session._stale_generation_drop_active() is True
    assert session._must_withhold_provider_output() is True
    session._stale_generation_dropping_since -= session_module._STALE_GENERATION_DROP_MAX_S + 1.0
    assert session._stale_generation_drop_active() is False
    assert session._stale_generation_dropping is False
    assert session._must_withhold_provider_output() is False
