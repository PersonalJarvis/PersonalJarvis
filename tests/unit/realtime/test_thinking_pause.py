"""The Thinking pause is ONE setting for both voice engines (2026-08-18).

``SpeechConfig.vad_silence_ms`` — the Settings → Voice slider — used to
endpoint the classic pipeline only (directive 2026-07-21: a fixed window on
every realtime transport read as "done speaking but still listening"). On
2026-08-18 the maintainer asked for the opposite: wait for a clear pause
before the turn is taken, and when the user keeps talking, append instead of
submitting twice. So the same value now reaches every realtime session, and
which lever applies is a transport CAPABILITY:

* a transport that answers on its own boundary folds the pause into its
  native turn detection (``RealtimeSessionConfig.turn_pause_ms``);
* a transport whose responses Jarvis requests itself is held by the SESSION:
  a final input transcript does not request the response until the
  microphone has been quiet for the whole pause, and every final that lands
  meanwhile appends to the open turn — one response for the whole request.
"""

from __future__ import annotations

import array
import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.realtime import session as session_module
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession

_FIRST = "What is the capital"
_SECOND = "of Peru, and how many people live there"


def _voiced_frame(peak: int = 6_000, samples: int = 320) -> bytes:
    """One 20 ms 16 kHz frame loud enough to count as the user's voice."""
    return array.array("h", [peak, -peak] * (samples // 2)).tobytes()


def _silent_frame(samples: int = 320) -> bytes:
    return array.array("h", [0] * samples).tobytes()


class _ManualWire:
    """A manual-response transport (OpenAI-shaped) driven event by event."""

    session_id = "manual-wire"
    creates_responses_automatically = False
    isolates_response_generations = True

    def __init__(self) -> None:
        self.events: asyncio.Queue[RealtimeEvent | None] = asyncio.Queue()
        self.requests: list[float] = []
        self.requested = asyncio.Event()
        self.interrupts = 0

    def emit(self, event: RealtimeEvent) -> None:
        self.events.put_nowait(event)

    async def receive(self):
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    async def send_audio(self, _chunk: Any) -> None:
        return None

    async def update_session(self, **_kwargs: Any) -> None:
        return None

    async def request_response(self, **_kwargs: Any) -> None:
        self.requests.append(time.monotonic())
        self.requested.set()

    async def send_text(self, _text: str) -> None:
        return None

    async def truncate(self, _audio_end_ms: int) -> None:
        return None

    async def interrupt(self, **_kwargs: Any) -> None:
        self.interrupts += 1

    async def send_tool_result(self, *_args: Any) -> None:
        return None

    async def close(self) -> None:
        self.events.put_nowait(None)


class _AutoWire(_ManualWire):
    """A self-answering transport (Gemini-shaped): no request to hold."""

    session_id = "auto-wire"
    creates_responses_automatically = True


class _Provider:
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, name: str, *, wire: _ManualWire | None = None, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.opened_with = None
        self.wire = wire or _ManualWire()

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, config):
        self.opened_with = config
        if self.fail:
            raise RuntimeError("simulated provider outage")
        return self.wire


def _config(silence_ms: int):
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        speech=SimpleNamespace(vad_silence_ms=silence_ms),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


def _build(provider: _Provider, *, pause_ms: int, session_id: str) -> RealtimeVoiceSession:
    return RealtimeVoiceSession(
        session_id=session_id,
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[provider],
        config=_config(pause_ms),
        bus=None,
        browser_sample_rate=16_000,
        surface="desktop",
    )


def _final(text: str, item_id: str) -> RealtimeEvent:
    return RealtimeEvent(type="input_transcript", text=text, is_final=True, item_id=item_id)


@pytest.fixture()
def _fast_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scale the pause and its safety caps down so the tests stay sub-second."""
    monkeypatch.setattr(session_module, "_TURN_PAUSE_MIN_MS", 50)
    monkeypatch.setattr(session_module, "_USER_SPEAKING_HOLD_S", 0.12)
    monkeypatch.setattr(session_module, "_MIC_HOLD_STALE_TRANSCRIPT_S", 0.6)
    monkeypatch.setattr(session_module, "_MIC_HOLD_ABSOLUTE_CAP_S", 10.0)


@pytest.mark.asyncio
async def test_pause_reaches_every_provider_family_as_turn_pause_ms():
    """Primary and cross-family fallback both open with the user's pause.

    ``silence_duration_ms`` stays None — that is the raw override, not the
    setting. The pause travels as ``turn_pause_ms`` and each transport applies
    the lever it has (native window, or the session-side hold).
    """
    primary = _Provider("first-family", fail=True)
    fallback = _Provider("second-family")
    session = _build(primary, pause_ms=2_700, session_id="thinking-pause")
    session._providers = [primary, fallback]

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        assert primary.opened_with.turn_pause_ms == 2_700
        assert fallback.opened_with.turn_pause_ms == 2_700
        assert primary.opened_with.silence_duration_ms is None
        assert fallback.opened_with.silence_duration_ms is None
    finally:
        await session.end(reason="test")


def test_pause_is_clamped_to_the_settings_bounds():
    session = _build(_Provider("p"), pause_ms=99_999, session_id="clamp-high")
    assert session._turn_pause_ms() == session_module._TURN_PAUSE_MAX_MS
    session = _build(_Provider("p"), pause_ms=1, session_id="clamp-low")
    assert session._turn_pause_ms() == session_module._TURN_PAUSE_MIN_MS
    session = _build(_Provider("p"), pause_ms=1_500, session_id="clamp-none")
    session._config = SimpleNamespace(brain=SimpleNamespace(reply_language="en"))
    assert session._turn_pause_ms() == session_module._TURN_PAUSE_DEFAULT_MS


@pytest.mark.asyncio
async def test_a_final_while_the_user_talks_on_waits_and_answers_once(
    _fast_pause: None,
) -> None:
    """The provider committed mid-sentence; the response waits for the pause.

    The user keeps the floor, the provider's second commit lands, and only
    once the microphone has been quiet for the whole pause is ONE response
    requested — for both halves, on the same turn.
    """
    provider = _Provider("manual")
    wire = provider.wire
    session = _build(provider, pause_ms=200, session_id="held-turn")

    speaking = asyncio.Event()
    speaking.set()
    stop = asyncio.Event()

    async def _microphone() -> None:
        # A real microphone never stops reporting: voice while the user
        # talks, silent frames afterwards.
        while not stop.is_set():
            frame = _voiced_frame() if speaking.is_set() else _silent_frame()
            await session.handle_audio_frame(frame)
            await asyncio.sleep(0.02)

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    mic = asyncio.create_task(_microphone())
    try:
        await asyncio.sleep(0.05)
        wire.emit(_final(_FIRST, "item-1"))
        # Generous: the first final of a session pays its lazy imports.
        await asyncio.wait_for(_until(session._turn_held_for_pause), timeout=3.0)
        assert wire.requests == [], "requested while the user was still talking"
        first_turn = session._turn_id
        assert first_turn

        # The server VAD's own "the user started again" edge inside the pause
        # must NOT split the turn — nothing is playing, there is nothing to cut.
        wire.emit(RealtimeEvent(type="speech_started"))
        await asyncio.sleep(0.1)
        assert session._turn_id == first_turn
        assert wire.interrupts == 0

        wire.emit(_final(_SECOND, "item-2"))
        await asyncio.sleep(0.3)
        assert wire.requests == [], "requested before the pause settled"
        assert session._last_user_text == f"{_FIRST} {_SECOND}"
        assert session._turn_held_for_pause() is True

        speaking.clear()
        quiet_at = time.monotonic()
        await asyncio.wait_for(wire.requested.wait(), timeout=2.0)
        waited = wire.requests[0] - quiet_at
        # Requested only after the pause (0.2 s), never before it — and not
        # long after it either (no stream-gone bound was needed: the mic kept
        # reporting silence).
        assert 0.15 <= waited < 1.0, f"answered {waited:.3f}s after the mic went quiet"
        await asyncio.sleep(0.1)
        assert len(wire.requests) == 1, "one response for the whole request"
        assert session._response_requested_for_turn is True
        assert {"item-1", "item-2"} <= session._response_requested_input_ids
        assert session._turn_id == first_turn
        assert session._turn_held_for_pause() is False
    finally:
        stop.set()
        if not mic.done():
            mic.cancel()
        await session.end(reason="test")


async def _until(predicate) -> None:  # noqa: ANN001 - test helper
    while not predicate():  # noqa: ASYNC110 - polling a plain predicate in a test
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_a_quiet_microphone_answers_at_once(_fast_pause: None) -> None:
    """No voice on the mic = no evidence to hold on: the request is immediate.

    This is the degradation for a quiet talker below the peak gate and for
    text-only surfaces — the pause can delay a request while the user audibly
    talks on, it can never deafen the assistant.
    """
    provider = _Provider("manual")
    wire = provider.wire
    session = _build(provider, pause_ms=5_000, session_id="quiet-mic")
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        emitted_at = time.monotonic()
        wire.emit(_final(_FIRST, "item-1"))
        await asyncio.wait_for(wire.requested.wait(), timeout=1.0)
        assert wire.requests[0] - emitted_at < 0.5
        assert session._turn_held_for_pause() is False
    finally:
        await session.end(reason="test")


@pytest.mark.asyncio
async def test_a_settled_pause_answers_at_once(_fast_pause: None) -> None:
    """The user spoke, then was quiet longer than the pause: no extra wait.

    The microphone keeps reporting SILENT frames after the words — that is
    what makes the old voiced stamp trustworthy (a stream that merely stalled
    proves nothing, see ``_MIC_FRAME_STALL_S``).
    """
    provider = _Provider("manual")
    wire = provider.wire
    session = _build(provider, pause_ms=100, session_id="settled")

    stop = asyncio.Event()

    async def _quiet_room() -> None:
        while not stop.is_set():
            await session.handle_audio_frame(_silent_frame())
            await asyncio.sleep(0.02)

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    room = asyncio.create_task(_quiet_room())
    try:
        await session.handle_audio_frame(_voiced_frame())
        await asyncio.sleep(0.3)
        emitted_at = time.monotonic()
        wire.emit(_final(_FIRST, "item-1"))
        await asyncio.wait_for(wire.requested.wait(), timeout=2.0)
        # The first final of a session pays its lazy imports on the loop; the
        # bound only has to prove there was no pause-shaped wait on top.
        assert wire.requests[0] - emitted_at < 1.0
        assert session._turn_held_for_pause() is False
    finally:
        stop.set()
        if not room.done():
            room.cancel()
        await session.end(reason="test")


@pytest.mark.asyncio
async def test_a_stalled_frame_stream_does_not_pass_for_silence(
    _fast_pause: None,
) -> None:
    """Loop hiccup: the stamp is old because nothing was PROCESSED, not heard.

    Frames flowed, then stopped arriving for longer than a frame interval —
    the user may be mid-word with the frames still queued. The pause holds
    until the stream reports again; once it does with silence, it settles.
    """
    provider = _Provider("manual")
    session = _build(provider, pause_ms=100, session_id="stalled-stream")
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        await session.handle_audio_frame(_voiced_frame())
        await asyncio.sleep(0.4)  # older than the pause AND the stall margin
        assert session._turn_pause_settled() is False
        # The stream catches up with silence: the old voiced stamp is real.
        await session.handle_audio_frame(_silent_frame())
        assert session._turn_pause_settled() is True
    finally:
        await session.end(reason="test")


@pytest.mark.asyncio
async def test_a_loud_but_wordless_floor_still_gets_its_answer(
    _fast_pause: None,
) -> None:
    """A stuck floor costs a bounded delay, never the answer (AP-30)."""
    provider = _Provider("manual")
    wire = provider.wire
    session = _build(provider, pause_ms=200, session_id="stuck-floor")

    stop_speaking = asyncio.Event()

    async def _hold_the_floor() -> None:
        while not stop_speaking.is_set():
            await session.handle_audio_frame(_voiced_frame())
            await asyncio.sleep(0.02)

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    floor = asyncio.create_task(_hold_the_floor())
    try:
        wire.emit(_final(_FIRST, "item-1"))
        # The mic stays loud, no new words ever arrive: the stale window
        # (0.6 s here) releases the request while the floor is still "held".
        await asyncio.wait_for(wire.requested.wait(), timeout=3.0)
        assert len(wire.requests) == 1
    finally:
        stop_speaking.set()
        if not floor.done():
            floor.cancel()
        await session.end(reason="test")


@pytest.mark.asyncio
async def test_a_self_answering_transport_is_never_held(_fast_pause: None) -> None:
    """Gemini-shaped wires answer on their own boundary; the hold is not theirs.

    Their pause lives in ``turn_pause_ms`` → native silence window; the
    session must not park a request it could never make.
    """
    provider = _Provider("auto", wire=_AutoWire())
    wire = provider.wire
    session = _build(provider, pause_ms=5_000, session_id="auto-answer")

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        for _ in range(5):
            await session.handle_audio_frame(_voiced_frame())
        wire.emit(_final(_FIRST, "item-1"))
        await asyncio.sleep(0.15)
        assert session._turn_held_for_pause() is False
        assert session._response_requested_for_turn is True
        assert "item-1" in session._response_requested_input_ids
    finally:
        await session.end(reason="test")
