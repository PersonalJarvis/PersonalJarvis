"""Delegate-by-default on ambiguous action turns — tool-less transports only.

A provider that declares ``supports_direct_tools=False`` (capability read,
never a provider-id check) reaches actions ONLY through the session-side
planner: there is no native tool the model could call, and the
model-initiated handoff item has never been observed on the live wire. A
final the planner routes natively is answered unaided by the far end and any
action in it is silently lost — counted by ``handoff_obligation_misses``
but never executed. Pinned here: on such a transport an action-shaped but
ambiguous final (a tasking phrase outside the planner's action vocabulary)
dispatches the deterministic delegate anyway, the new
``handoff_ambiguous_delegations`` counter keeps that path distinguishable
from planner-confirmed action turns and model-initiated handoffs, and a
provider WITH native tools keeps today's routing untouched.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from jarvis.brain.turn_planner import TurnPath, TurnPlan
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession

TIMEOUT_S = 5.0
RATE = 24_000
# A tasking phrase whose verb sits OUTSIDE the planner's action vocabulary —
# the planner routes it natively, which on a tool-less transport used to
# lose the action.
AMBIGUOUS_ACTION = "Can you take care of that for me"
SMALLTALK = "I had a really nice day today"


class _NativePlanBrain:
    """Brain whose planner finds nothing — the ambiguity tie-break decides."""

    conversation_language = "en"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()

    def plan_turn(self, _text: str) -> TurnPlan:
        return TurnPlan(path=TurnPath.NATIVE_REALTIME)

    async def __call__(self, text: str) -> str:
        self.calls.append(text)
        self.started.set()
        return "Done."


class _Wire:
    session_id = "toolless-delegate-wire"
    supports_tool_updates = False
    creates_responses_automatically = True
    isolates_response_generations = True
    direct_speech_is_authoritative = True

    def __init__(self, text: str) -> None:
        self._text = text
        self._closed = asyncio.Event()
        self.speech_sent: list[str] = []
        self.interrupts = 0

    async def receive(self):  # noqa: ANN201 - async generator, protocol shape
        yield RealtimeEvent(
            type="input_transcript",
            text=self._text,
            is_final=True,
            item_id="turn-1",
        )
        await self._closed.wait()

    async def send_audio(self, chunk: Any) -> None:
        del chunk

    async def update_session(self, **kwargs: Any) -> None:
        del kwargs

    async def request_response(self, **kwargs: Any) -> None:
        del kwargs

    async def send_text(self, text: str) -> None:
        del text

    async def send_speech(self, text: str) -> None:
        self.speech_sent.append(text)

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms

    async def interrupt(self, **kwargs: Any) -> None:
        self.interrupts += 1

    async def send_tool_result(self, *args: Any) -> None:
        del args

    async def close(self) -> None:
        self._closed.set()


class _ToollessProvider:
    """Codex-shaped capability card: no native tools on the wire."""

    name = "toolless"
    supports_realtime = True
    supports_direct_tools = False
    input_sample_rate = RATE
    output_sample_rate = RATE

    def __init__(self, wire: _Wire) -> None:
        self._wire = wire

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _Wire:
        del config
        return self._wire


class _NativeToolsProvider(_ToollessProvider):
    name = "native-tools"
    supports_direct_tools = True


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


def _session(
    provider: _ToollessProvider, brain: _NativePlanBrain
) -> RealtimeVoiceSession:
    return RealtimeVoiceSession(
        session_id="toolless-delegate-default",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[provider],
        config=_config(),
        bus=None,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=RATE,
        brain=brain,
    )


async def test_ambiguous_action_delegates_by_default_on_a_toolless_wire() -> None:
    brain = _NativePlanBrain()
    wire = _Wire(AMBIGUOUS_ACTION)
    session = _session(_ToollessProvider(wire), brain)
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": RATE}),
        TIMEOUT_S,
    )
    try:
        await asyncio.wait_for(brain.started.wait(), TIMEOUT_S)
    finally:
        await asyncio.wait_for(session.end(reason="test"), TIMEOUT_S)

    assert brain.calls == [AMBIGUOUS_ACTION], (
        "the ambiguous action final must reach the deterministic delegate "
        "instead of being answered unaided by the far end"
    )
    assert session._handoff_ambiguous_delegations == 1
    assert session._handoff_delegate_dispatches == 1
    assert session._handoff_action_turns == 0, (
        "an ambiguity dispatch is not a planner-confirmed action turn — the "
        "counters must keep the origins distinguishable"
    )

    postmortem = session._build_postmortem("test")
    assert postmortem.handoff_ambiguous_delegations == 1
    assert postmortem.handoff_obligation_misses == 0


async def test_a_native_tools_provider_keeps_todays_routing() -> None:
    brain = _NativePlanBrain()
    wire = _Wire(AMBIGUOUS_ACTION)
    session = _session(_NativeToolsProvider(wire), brain)
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": RATE}),
        TIMEOUT_S,
    )
    try:
        await asyncio.sleep(0.2)
    finally:
        await asyncio.wait_for(session.end(reason="test"), TIMEOUT_S)

    assert brain.calls == [], (
        "with native tools declared, the model itself can act — the "
        "ambiguity tie-break must not touch this provider"
    )
    assert session._handoff_ambiguous_delegations == 0
    assert session._handoff_delegate_dispatches == 0


async def test_plain_smalltalk_stays_native_on_a_toolless_wire() -> None:
    brain = _NativePlanBrain()
    wire = _Wire(SMALLTALK)
    session = _session(_ToollessProvider(wire), brain)
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": RATE}),
        TIMEOUT_S,
    )
    try:
        await asyncio.sleep(0.2)
    finally:
        await asyncio.wait_for(session.end(reason="test"), TIMEOUT_S)

    assert brain.calls == []
    assert session._handoff_ambiguous_delegations == 0
