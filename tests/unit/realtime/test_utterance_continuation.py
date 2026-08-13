"""One spoken order stays ONE turn when the provider cuts it mid-sentence.

Live forensic 2026-08-13 11:19:08 and 11:19:48: two consecutive Gemini Live
calls each chopped a single spoken request into three turns at ordinary
hesitation pauses, and every fragment dispatched an executor of its own. The
guards here pin the microphone evidence that the session was missing.
"""

from __future__ import annotations

import array
import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.turn_planner import TurnPath, TurnPlan, TurnReason
from jarvis.realtime import session as session_module
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession

_FRAGMENT_ONE = "Prompt terminal five to do a deep dive"
_FRAGMENT_TWO = "into the prompting latency"


def _voiced_frame(peak: int = 6_000, samples: int = 320) -> bytes:
    """One 20 ms 16 kHz frame loud enough to count as the user's voice."""
    return array.array("h", [peak, -peak] * (samples // 2)).tobytes()


def _silent_frame(samples: int = 320) -> bytes:
    return array.array("h", [0] * samples).tobytes()


class _OrderBrain:
    """Orchestrator-path brain that records exactly what it was asked."""

    conversation_language = "en"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.called = asyncio.Event()

    def plan_turn(self, _text: str) -> TurnPlan:
        return TurnPlan(
            path=TurnPath.ORCHESTRATOR,
            reasons=frozenset({TurnReason.LOCAL_STATE}),
            requires_evidence=True,
        )

    async def __call__(self, text: str) -> str:
        self.calls.append(text)
        self.called.set()
        return "Sent it to the pane."


class _ChoppedUtteranceWire:
    """Gemini-shaped wire that commits its input in the middle of a sentence."""

    session_id = "chopped-utterance-wire"
    supports_tool_updates = False
    creates_responses_automatically = True
    isolates_response_generations = True

    def __init__(self) -> None:
        self.text_inputs: list[str] = []
        self.interrupts = 0
        self.first_final = asyncio.Event()
        self.release_tail = asyncio.Event()
        self.closed = asyncio.Event()

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text=_FRAGMENT_ONE,
            is_final=True,
            item_id="fragment-1",
        )
        self.first_final.set()
        await self.release_tail.wait()
        # The server VAD's own "the user started again" edge, exactly as the
        # live calls produced it while the delegate was still running.
        yield RealtimeEvent(type="interrupted")
        yield RealtimeEvent(
            type="input_transcript",
            text=_FRAGMENT_TWO,
            is_final=True,
            item_id="fragment-2",
        )
        await self.closed.wait()

    async def send_audio(self, _chunk: Any) -> None:
        return None

    async def update_session(self, **_kwargs: Any) -> None:
        return None

    async def request_response(self, **_kwargs: Any) -> None:
        return None

    async def send_text(self, text: str) -> None:
        self.text_inputs.append(text)

    async def truncate(self, _audio_end_ms: int) -> None:
        return None

    async def interrupt(self, **_kwargs: Any) -> None:
        self.interrupts += 1

    async def send_tool_result(self, *_args: Any) -> None:
        return None

    async def close(self) -> None:
        self.closed.set()


class _ChoppedUtteranceProvider:
    name = "chopped-utterance"
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self) -> None:
        self.session = _ChoppedUtteranceWire()

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, _config: Any) -> _ChoppedUtteranceWire:
        return self.session


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


def _build_session(
    provider: Any, brain: Any, *, session_id: str
) -> RealtimeVoiceSession:
    return RealtimeVoiceSession(
        session_id=session_id,
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_config(),
        bus=None,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=brain,
    )


@pytest.fixture()
def _fast_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scale the real-time windows down so the test stays sub-second."""
    monkeypatch.setattr(session_module, "_USER_SPEAKING_HOLD_S", 0.12)
    monkeypatch.setattr(session_module, "_MIC_HOLD_STALE_TRANSCRIPT_S", 0.6)
    monkeypatch.setattr(session_module, "_MIC_HOLD_ABSOLUTE_CAP_S", 10.0)
    monkeypatch.setattr(session_module, "_UTTERANCE_TAIL_SETTLE_S", 0.4)
    monkeypatch.setattr(session_module, "_DELEGATE_INPUT_BOUNDARY_WAIT_S", 0.2)
    monkeypatch.setattr(session_module, "_DELEGATE_INPUT_BOUNDARY_POLL_S", 0.02)


@pytest.mark.asyncio
async def test_voiced_frames_mark_the_user_as_speaking() -> None:
    provider = _ChoppedUtteranceProvider()
    session = _build_session(provider, _OrderBrain(), session_id="mic-probe")
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        assert session._user_is_speaking() is False
        await session.handle_audio_frame(_silent_frame())
        assert session._user_is_speaking() is False
        await session.handle_audio_frame(_voiced_frame())
        assert session._user_is_speaking() is True
        # Speaker echo must never read as the user holding the floor.
        session._output_active = True
        assert session._user_is_speaking() is False
        session._output_active = False
        await asyncio.sleep(session_module._USER_SPEAKING_HOLD_S + 0.05)
        assert session._user_is_speaking() is False
    finally:
        await session.end(reason="test")


@pytest.mark.asyncio
async def test_announcement_is_refused_while_the_user_speaks() -> None:
    """The 11:20:03 injection that closed a sentence it had no business ending."""
    provider = _ChoppedUtteranceProvider()
    session = _build_session(provider, _OrderBrain(), session_id="announce-guard")
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        await session.handle_audio_frame(_voiced_frame())
        assert session._user_is_speaking() is True
        delivered = await session.deliver_announcement(
            text="Sent to T5, with eight files attached.",
            language="en",
            spoken_kind="completion",
            detail="delivery_id=previous-session:turn-0",
        )
        assert delivered is False
        assert provider.session.text_inputs == []
        assert session._session_is_at_rest() is False
    finally:
        await session.end(reason="test")


@pytest.mark.asyncio
async def test_mid_sentence_commit_keeps_one_turn_and_one_executor(
    _fast_windows: None,
) -> None:
    provider = _ChoppedUtteranceProvider()
    brain = _OrderBrain()
    session = _build_session(provider, brain, session_id="chopped-order")

    async def _hold_the_floor(stop: asyncio.Event) -> None:
        while not stop.is_set():
            await session.handle_audio_frame(_voiced_frame())
            await asyncio.sleep(0.02)

    stop_speaking = asyncio.Event()
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    floor = asyncio.create_task(_hold_the_floor(stop_speaking))
    try:
        await asyncio.wait_for(provider.session.first_final.wait(), timeout=1.0)
        # The provider closed its input turn, but the user talks on: the
        # delegate must not have run on the fragment.
        await asyncio.sleep(0.25)
        assert brain.calls == []

        provider.session.release_tail.set()
        await asyncio.sleep(0.15)
        stop_speaking.set()
        await floor

        await asyncio.wait_for(brain.called.wait(), timeout=3.0)
    finally:
        stop_speaking.set()
        if not floor.done():
            floor.cancel()
        await session.end(reason="test")

    assert len(brain.calls) == 1, f"one order, one executor: {brain.calls}"
    assert _FRAGMENT_ONE in brain.calls[0]
    assert _FRAGMENT_TWO in brain.calls[0]


_LONG_ORDER = (
    "Can you please prompt terminal five",
    "to do a deep dive into the spawning",
    "it should work with every IDE we connected",
    "and tell me what you find",
    "before the end of the day",
)


class _LongOrderWire(_ChoppedUtteranceWire):
    """Commits after the opening clause, then transcribes the rest slowly."""

    session_id = "long-order-wire"

    def __init__(self, *, gap_s: float) -> None:
        super().__init__()
        self._gap_s = gap_s
        self.all_sent = asyncio.Event()

    async def receive(self):
        for index, fragment in enumerate(_LONG_ORDER):
            yield RealtimeEvent(
                type="input_transcript",
                text=fragment,
                is_final=True,
                item_id=f"long-{index}",
            )
            if index == 0:
                self.first_final.set()
            await asyncio.sleep(self._gap_s)
        self.all_sent.set()
        await self.closed.wait()


class _LongOrderProvider(_ChoppedUtteranceProvider):
    name = "long-order"

    def __init__(self, *, gap_s: float) -> None:
        self.session = _LongOrderWire(gap_s=gap_s)


@pytest.mark.asyncio
async def test_a_long_order_survives_past_the_hold_ceiling(
    _fast_windows: None,
) -> None:
    """The 2026-08-13 16:46:26.939 truncation: the CEILING expired, not the user.

    A fixed budget measured from the provider's commit cut a long spoken order
    at its own ceiling and briefed a coding pane with the opening clause. Here
    the user talks for twice a full window after the commit; every new word
    must renew the microphone's authority so the whole order survives.
    """
    provider = _LongOrderProvider(gap_s=0.25)
    brain = _OrderBrain()
    session = _build_session(provider, brain, session_id="long-order")

    stop_speaking = asyncio.Event()

    async def _hold_the_floor() -> None:
        while not stop_speaking.is_set():
            await session.handle_audio_frame(_voiced_frame())
            await asyncio.sleep(0.02)

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    floor = asyncio.create_task(_hold_the_floor())
    try:
        await asyncio.wait_for(provider.session.first_final.wait(), timeout=1.0)
        # Well past the point a fixed budget would have given up at.
        await asyncio.sleep(session_module._MIC_HOLD_STALE_TRANSCRIPT_S * 2)
        assert brain.calls == [], "dispatched while the user was still talking"

        await asyncio.wait_for(provider.session.all_sent.wait(), timeout=5.0)
        stop_speaking.set()
        await floor
        await asyncio.wait_for(brain.called.wait(), timeout=5.0)
    finally:
        stop_speaking.set()
        if not floor.done():
            floor.cancel()
        await session.end(reason="test")

    assert len(brain.calls) == 1, f"one order, one executor: {brain.calls}"
    for fragment in _LONG_ORDER:
        assert fragment in brain.calls[0], f"lost {fragment!r}: {brain.calls[0]!r}"


@pytest.mark.asyncio
async def test_a_loud_but_wordless_floor_still_releases_the_order(
    _fast_windows: None,
) -> None:
    """The bounded half: a hot mic may DELAY an order, never swallow it.

    Room noise keeps the voice floor above the threshold forever but produces
    no words, so the rolling window must expire and the turn must still run.
    """
    provider = _ChoppedUtteranceProvider()
    brain = _OrderBrain()
    session = _build_session(provider, brain, session_id="stuck-floor")

    stop = asyncio.Event()

    async def _hot_mic() -> None:
        while not stop.is_set():
            await session.handle_audio_frame(_voiced_frame())
            await asyncio.sleep(0.02)

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    mic = asyncio.create_task(_hot_mic())
    try:
        await asyncio.wait_for(provider.session.first_final.wait(), timeout=1.0)
        # The tail is never released and the floor never goes quiet.
        await asyncio.wait_for(brain.called.wait(), timeout=5.0)
    finally:
        stop.set()
        if not mic.done():
            mic.cancel()
        await session.end(reason="test")

    assert brain.calls, "a stuck floor swallowed the order entirely"
    assert _FRAGMENT_ONE in brain.calls[0]
