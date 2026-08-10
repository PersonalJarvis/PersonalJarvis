"""Multi-turn end-to-end guards for a codex-shaped realtime transport.

The gap these close: ``tests/unit/realtime/test_codex_subscription.py`` drives
the adapter's ``receive()`` in isolation, and ``test_session.py`` drives a fake
whose capability tuple is not the codex one — so NO test anywhere ran a
codex-shaped provider through ``RealtimeVoiceSession`` for more than one turn.
"Works once, then only listens" is by definition a turn-2 failure, so it could
reach the maintainer with a green suite.

Everything here is hermetic and deterministic: no network, no aiortc, no
app-server, no real clock. Provider events are pushed through an in-memory
queue and every wait is bounded, so a wedge fails the test instead of hanging
the run.

Scope boundary, stated honestly: this drives the transport contract at the
SESSION level. The adapter's own grounding gate (``_begin_response``) and its
quiescence backstop live below the events pushed here; the fake emits the
normalized stream that gate produces, so what these tests pin is the session
half — that a codex-shaped stream keeps the call answering, turn after turn.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk
from jarvis.realtime import session as session_mod
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession

# Every await in this module is bounded by this. A regression must report as a
# failure, never as a stalled CI job.
TIMEOUT_S = 5.0

PROVIDER_NAME = "codex-subscription"
INPUT_RATE = 24_000
OUTPUT_RATE = 24_000

#: The real adapter's capability tuple. Pinned against the production class by
#: ``test_the_fake_mirrors_the_real_codex_capability_tuple`` so this harness
#: cannot quietly drift into testing a provider that does not exist.
CODEX_CAPABILITIES: dict[str, bool] = {
    "creates_responses_automatically": True,
    "supports_prompted_response_retry": True,
    "isolates_response_generations": True,
    "supports_direct_tools": False,
    "supports_tool_updates": False,
    "direct_speech_is_authoritative": True,
    "rebuild_on_transport_death": True,
}


def _pcm(sample_count: int = 240, value: int = 1200) -> bytes:
    return value.to_bytes(2, "little", signed=True) * sample_count


def _audio(sample_count: int = 240) -> AudioChunk:
    return AudioChunk(
        pcm=_pcm(sample_count),
        sample_rate=OUTPUT_RATE,
        timestamp_ns=0,
    )


class _CodexShapedWire:
    """A provider session with the codex capability tuple and a pushable stream.

    ChatGPT-Live carries audio on a WebRTC media track and supplies no
    server-side user transcription, so the user transcripts below stand for the
    adapter's LOCAL recognizer — which is the only source of them on that
    transport, and therefore the only thing that can open a turn.
    """

    session_id = "codex-shaped-wire"
    creates_responses_automatically = CODEX_CAPABILITIES[
        "creates_responses_automatically"
    ]
    supports_prompted_response_retry = CODEX_CAPABILITIES[
        "supports_prompted_response_retry"
    ]
    isolates_response_generations = CODEX_CAPABILITIES[
        "isolates_response_generations"
    ]
    supports_direct_tools = CODEX_CAPABILITIES["supports_direct_tools"]
    supports_tool_updates = CODEX_CAPABILITIES["supports_tool_updates"]
    direct_speech_is_authoritative = CODEX_CAPABILITIES[
        "direct_speech_is_authoritative"
    ]
    rebuild_on_transport_death = CODEX_CAPABILITIES["rebuild_on_transport_death"]

    def __init__(self) -> None:
        self._events: asyncio.Queue[Any] = asyncio.Queue()
        self.sent_audio: list[AudioChunk] = []
        self.spoken: list[str] = []
        self.texts: list[str] = []
        self.text_appended = asyncio.Event()
        self.session_updates: list[dict[str, Any]] = []
        self.response_requests = 0
        self.interrupts = 0
        self.closed = False

    # -- test-side driving ------------------------------------------------
    def push(self, *events: RealtimeEvent) -> None:
        for event in events:
            self._events.put_nowait(event)

    # -- provider contract ------------------------------------------------
    async def send_audio(self, chunk: AudioChunk) -> None:
        self.sent_audio.append(chunk)

    async def receive(self):  # noqa: ANN201 - async generator, protocol shape
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> None:
        self.session_updates.append(
            {"instructions": instructions, "language": language, "tools": tools}
        )

    async def request_response(self, *, required_tool: str | None = None) -> None:
        # ChatGPT-Live's server VAD creates responses on its own; the real
        # adapter's request_response is a no-op for exactly this reason.
        del required_tool
        self.response_requests += 1

    async def send_text(self, text: str) -> None:
        self.texts.append(text)
        self.text_appended.set()

    async def send_speech(self, text: str) -> None:
        self.spoken.append(text)

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        del call_id, name, result
        raise RuntimeError("codex subscription realtime does not execute tools")

    async def close(self) -> None:
        self.closed = True
        self._events.put_nowait(None)


class _CodexShapedProvider:
    name = PROVIDER_NAME
    supports_realtime = True
    input_sample_rate = INPUT_RATE
    output_sample_rate = OUTPUT_RATE

    def __init__(self) -> None:
        self.sessions: list[_CodexShapedWire] = []
        self.opened_with: Any = None

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _CodexShapedWire:
        self.opened_with = config
        wire = _CodexShapedWire()
        self.sessions.append(wire)
        return wire


class _Surface:
    """Records what the desktop surface would render, with bounded waiting."""

    def __init__(self) -> None:
        self.json: list[dict[str, Any]] = []
        self.binary: list[bytes] = []
        self._tick = asyncio.Event()

    async def send_json(self, message: dict[str, Any]) -> None:
        self.json.append(dict(message))
        self._tick.set()

    async def send_binary(self, data: bytes) -> None:
        self.binary.append(bytes(data))
        self._tick.set()

    def mark(self) -> int:
        return len(self.json)

    async def wait_json(
        self, predicate, *, since: int = 0, timeout_s: float = TIMEOUT_S
    ) -> dict[str, Any]:
        async def _loop() -> dict[str, Any]:
            while True:
                # Clear BEFORE scanning, so a message that arrives during the
                # scan still wakes the next iteration.
                self._tick.clear()
                for message in self.json[since:]:
                    if predicate(message):
                        return message
                await self._tick.wait()

        try:
            return await asyncio.wait_for(_loop(), timeout_s)
        except TimeoutError:  # pragma: no cover - only on a real regression
            raise AssertionError(
                f"no matching surface message within {timeout_s:.1f}s; "
                f"saw {[m.get('type') for m in self.json[since:]]}"
            ) from None

    async def wait_binary(
        self, *, at_least: int, timeout_s: float = TIMEOUT_S
    ) -> None:
        async def _loop() -> None:
            while len(self.binary) < at_least:
                self._tick.clear()
                if len(self.binary) >= at_least:
                    return
                await self._tick.wait()

        try:
            await asyncio.wait_for(_loop(), timeout_s)
        except TimeoutError:  # pragma: no cover - only on a real regression
            raise AssertionError(
                f"expected at least {at_least} audio frames within "
                f"{timeout_s:.1f}s; got {len(self.binary)}"
            ) from None


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(
            reply_language="en",
            providers={PROVIDER_NAME: SimpleNamespace(model="", voice="cove")},
        ),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
    )


async def _open_call() -> tuple[
    RealtimeVoiceSession, _CodexShapedProvider, _CodexShapedWire, _Surface
]:
    """Open one desktop-shaped half-duplex call and return its parts."""
    surface = _Surface()
    provider = _CodexShapedProvider()
    session = RealtimeVoiceSession(
        session_id="codex-multi-turn",
        send_binary=surface.send_binary,
        send_json=surface.send_json,
        providers=[provider],
        config=_config(),
        bus=EventBus(),
        surface="desktop",
        # The desktop configuration: the microphone is muted while the
        # assistant speaks, which is exactly what turns a stuck output state
        # into "it stopped listening".
        half_duplex=True,
        browser_sample_rate=INPUT_RATE,
    )
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": INPUT_RATE}),
        TIMEOUT_S,
    )
    # The real desktop surface supplies this probe. This in-memory surface has
    # no device queue, so every forwarded frame is drained immediately.
    session.set_playback_probe(lambda: False)
    assert provider.sessions, "the provider was never opened"
    return session, provider, provider.sessions[0], surface


async def _assert_at_rest(
    session: RealtimeVoiceSession, wire: _CodexShapedWire, *, note: str
) -> None:
    """The call must be genuinely idle AND still listening.

    "Still listening" is the load-bearing half: under half duplex a stuck
    output state silently swallows every microphone frame, so the call looks
    alive while nothing the user says can reach the provider again.
    """
    assert session._output_active is False, f"{note}: output still active"  # noqa: SLF001
    assert session._response_requested_for_turn is False, (  # noqa: SLF001
        f"{note}: a response is still marked as requested"
    )
    assert session._transport_rebuild_pending is None, (  # noqa: SLF001
        f"{note}: a transport rebuild is still pending"
    )
    assert session._advised_reconnect_detail is None, (  # noqa: SLF001
        f"{note}: a reconnect is still advised"
    )
    assert session._external_update is None, (  # noqa: SLF001
        f"{note}: an external update is still owned by the turn"
    )

    before = len(wire.sent_audio)
    await asyncio.wait_for(session.handle_audio_frame(_pcm()), TIMEOUT_S)
    assert len(wire.sent_audio) == before + 1, (
        f"{note}: the microphone no longer reaches the provider — the call is "
        "wedged even though it looks alive"
    )


#: Ordinal words, not digits: the voice scrubber spells numbers out for TTS
#: ("answer 1." -> "answer one."), so a digit in fixture text would assert
#: against the scrubber rather than against the turn machinery.
_ORDINALS = {1: "first", 2: "second", 3: "third"}


def _question(index: int) -> str:
    return f"the {_ORDINALS[index]} question"


def _answer(index: int) -> str:
    return f"The {_ORDINALS[index]} reply."


def _turn_events(index: int, *, audio_frames: int = 2) -> list[RealtimeEvent]:
    """One ordinary turn as the codex adapter normalizes it."""
    events = [
        RealtimeEvent(type="speech_started"),
        RealtimeEvent(
            type="input_transcript", text=_question(index), is_final=True
        ),
        RealtimeEvent(type="output_transcript_delta", text=_answer(index)),
    ]
    events.extend(RealtimeEvent(type="audio_delta", audio=_audio()) for _ in range(audio_frames))
    events.append(RealtimeEvent(type="turn_complete"))
    return events


async def _run_turn(
    session: RealtimeVoiceSession,
    wire: _CodexShapedWire,
    surface: _Surface,
    index: int,
) -> None:
    since = surface.mark()
    wire.push(*_turn_events(index))
    await surface.wait_json(lambda m: m.get("type") == "turn_complete", since=since)


async def _end(session: RealtimeVoiceSession) -> None:
    await asyncio.wait_for(session.end(reason="test"), TIMEOUT_S)


# -- the fake must not drift from the adapter it stands in for --------------


def test_the_fake_mirrors_the_real_codex_capability_tuple() -> None:
    """A harness that tests a provider shape nobody ships proves nothing."""
    from jarvis.plugins.realtime.codex_subscription import (
        _CodexSubscriptionRealtimeSession,
    )

    for attribute, expected in CODEX_CAPABILITIES.items():
        assert getattr(_CodexSubscriptionRealtimeSession, attribute) == expected, (
            f"the real adapter changed {attribute}; this harness is now testing "
            "a provider shape that does not exist"
        )
        assert getattr(_CodexShapedWire, attribute) == expected

    # Trusted verbatim speech is the channel delegate readbacks ride on; a fake
    # without it cannot reproduce the turn shapes below.
    assert callable(getattr(_CodexSubscriptionRealtimeSession, "send_speech", None))
    assert callable(getattr(_CodexShapedWire, "send_speech", None))


@pytest.mark.asyncio
async def test_wrong_language_uses_prompted_subscription_retry() -> None:
    """The subscription's ordinary response request is intentionally a no-op.

    Once the scrub gate blocks a wrong-language answer, the replacement must
    therefore travel through the adapter's trusted prompt capability; otherwise
    the turn waits forever and the next utterance inherits the interrupt state.
    """
    session, _provider, wire, surface = await _open_call()
    wrong_pcm = _pcm(value=700)
    correct_pcm = _pcm(value=1300)
    try:
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(
                type="input_transcript",
                text="What is kindness?",
                is_final=True,
            ),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=wrong_pcm,
                    sample_rate=OUTPUT_RATE,
                    timestamp_ns=0,
                ),
            ),
            RealtimeEvent(
                type="output_transcript_delta",
                text=(
                    "Esta es una respuesta completa en español con suficientes "
                    "palabras para identificar claramente el idioma."
                ),
            ),
            RealtimeEvent(type="turn_complete"),
        )

        await asyncio.wait_for(wire.text_appended.wait(), TIMEOUT_S)
        assert "English" in wire.texts[-1]
        assert wire.response_requests == 1, (
            "the dead automatic-response request was used for the retry"
        )
        assert surface.binary == [], "blocked wrong-language PCM reached playback"

        wire.push(
            RealtimeEvent(
                type="output_transcript_delta",
                text=(
                    "Kindness means treating other people with consistent care "
                    "and respect."
                ),
            ),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=correct_pcm,
                    sample_rate=OUTPUT_RATE,
                    timestamp_ns=0,
                ),
            ),
            RealtimeEvent(type="turn_complete"),
        )
        await surface.wait_json(
            lambda message: message.get("type") == "turn_complete",
            since=since,
        )

        assert surface.binary == [correct_pcm]
        await _assert_at_rest(session, wire, note="after a prompted language retry")
    finally:
        await _end(session)


# -- the headline case ------------------------------------------------------


@pytest.mark.asyncio
async def test_three_consecutive_turns_each_answer_and_leave_the_call_listening() -> (
    None
):
    """The maintainer's report, as a test: works once, then only listens.

    Three turns, and after each one the call must be genuinely at rest AND
    still able to hear the microphone.
    """
    session, _provider, wire, surface = await _open_call()
    try:
        for index in (1, 2, 3):
            await _run_turn(session, wire, surface, index)
            await _assert_at_rest(session, wire, note=f"after turn {index}")

        spoken = [
            message
            for message in surface.json
            if message.get("type") == "transcript"
            and message.get("role") == "assistant"
        ]
        assert [m["text"] for m in spoken] == [_answer(1), _answer(2), _answer(3)]
        heard = [
            message
            for message in surface.json
            if message.get("type") == "transcript"
            and message.get("role") == "user"
            and message.get("is_final")
        ]
        assert [m["text"] for m in heard] == [
            _question(1),
            _question(2),
            _question(3),
        ]
        assert len(surface.binary) == 6  # two audio frames per turn, all played
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_every_turn_after_the_first_is_still_transcribed() -> None:
    """Turn 2 and 3 must reach the surface, not just turn 1.

    A latched flag, a consumed generator, or a duplicate-item guard that never
    resets all present the same way: turn 1 is perfect and nothing else is.
    """
    session, _provider, wire, surface = await _open_call()
    try:
        for index in (1, 2, 3):
            since = surface.mark()
            wire.push(*_turn_events(index))
            heard = await surface.wait_json(
                lambda m: m.get("type") == "transcript"
                and m.get("role") == "user"
                and m.get("is_final"),
                since=since,
            )
            assert heard["text"] == _question(index)
            await surface.wait_json(
                lambda m: m.get("type") == "turn_complete", since=since
            )
    finally:
        await _end(session)


# -- adversarial turns ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reply_split_by_a_long_internal_pause_stays_one_answer() -> None:
    """A pause longer than the adapter's quiescence window, as the session sees it.

    The backstop that ends a turn on silence lives in the adapter, so what
    reaches the session is a turn boundary mid-reply followed by the REST of
    the same answer. The call must absorb that and stay listening rather than
    leaving the second half stranded in a turn that never closes.
    """
    session, _provider, wire, surface = await _open_call()
    try:
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(type="input_transcript", text="tell me a long one", is_final=True),
            RealtimeEvent(type="output_transcript_delta", text="First half."),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            # The quiescence backstop fires inside the reply.
            RealtimeEvent(type="turn_complete"),
        )
        await surface.wait_json(lambda m: m.get("type") == "turn_complete", since=since)

        # ...and the rest of the same answer arrives afterwards.
        second = surface.mark()
        wire.push(
            RealtimeEvent(type="output_transcript_delta", text="Second half."),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            RealtimeEvent(type="turn_complete"),
        )
        await surface.wait_json(lambda m: m.get("type") == "turn_complete", since=second)
        await _assert_at_rest(session, wire, note="after a split reply")

        # And a genuinely new turn still works.
        await _run_turn(session, wire, surface, 2)
        await _assert_at_rest(session, wire, note="after the turn following a split reply")
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_a_turn_without_any_terminal_item_does_not_wedge_the_next_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatGPT-Live's terminal response item is not guaranteed to arrive.

    When it does not, the turn never closes - and under half duplex the
    microphone stays muted, so the user is talking into a session that cannot
    hear them. The next genuine utterance must still land, which means the
    session has to own an unmute path instead of trusting the provider to send
    a well-formed boundary.

    The release is deliberately gated on a mute that is BOTH overdue and no
    longer producing audio, so this shortens that window rather than waiting
    out the production value - and a reply still streaming audio keeps its
    microphone shut, which the sibling tests cover.
    """
    monkeypatch.setattr(session_mod, "_HALF_DUPLEX_SILENT_RELEASE_S", 0.05)
    session, _provider, wire, surface = await _open_call()
    try:
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(type="input_transcript", text="no boundary please", is_final=True),
            RealtimeEvent(
                type="output_transcript_delta",
                text=(
                    "This is a complete English answer with enough ordinary "
                    "words to establish its language, but no terminal boundary."
                ),
            ),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            # No turn_complete, deliberately.
        )
        await surface.wait_binary(at_least=1)

        # One frame arms the mute clock; the window then passes with no further
        # provider audio, which is what marks the turn as over.
        await session.handle_audio_frame(_pcm())
        await asyncio.sleep(0.08)

        # The next utterance must reach the provider and open its own turn.
        before = len(wire.sent_audio)
        await asyncio.wait_for(session.handle_audio_frame(_pcm()), TIMEOUT_S)
        assert len(wire.sent_audio) > before, (
            "the microphone was still muted by an unterminated turn"
        )

        second = surface.mark()
        wire.push(*_turn_events(2))
        await surface.wait_json(lambda m: m.get("type") == "turn_complete", since=second)
        await _assert_at_rest(session, wire, note="after an unterminated turn")
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_a_provider_error_closing_a_turn_still_releases_the_microphone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scopes MT-1: which non-``turn_complete`` endings DO release the mute.

    The adapter ends a turn on an error without a terminal boundary in several
    places (a transport error, a stream that ends). If the session recovers the
    microphone there, MT-1 is narrow; if it does not, half duplex has no
    unmute path at all except a well-behaved provider.
    """
    monkeypatch.setattr(session_mod, "_HALF_DUPLEX_SILENT_RELEASE_S", 0.05)
    session, _provider, wire, surface = await _open_call()
    try:
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(type="input_transcript", text=_question(1), is_final=True),
            RealtimeEvent(type="output_transcript_delta", text=_answer(1)),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            RealtimeEvent(
                type="error",
                error="Codex subscription realtime transport failed",
                recoverable=True,
            ),
        )
        # A recoverable provider error surfaces as a warning, not a hangup.
        await surface.wait_json(
            lambda m: m.get("type") in {"provider_warning", "error", "turn_complete"},
            since=since,
        )

        # One frame arms the mute clock; the window then passes with no further
        # provider audio, which is what marks the turn as over.
        await session.handle_audio_frame(_pcm())
        await asyncio.sleep(0.08)

        before = len(wire.sent_audio)
        await asyncio.wait_for(session.handle_audio_frame(_pcm()), TIMEOUT_S)
        assert len(wire.sent_audio) > before, (
            "an error boundary left the microphone muted too - half duplex has "
            "no unmute path that the session itself owns"
        )
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_a_provider_that_goes_silent_after_turn_one_keeps_the_call_listening() -> (
    None
):
    """Turn 1 answers, then the provider says nothing at all, forever.

    The call is allowed to be silent. It is NOT allowed to stop accepting the
    microphone: that is the difference between "the model has nothing to say"
    and "the product is wedged".
    """
    session, _provider, wire, surface = await _open_call()
    try:
        await _run_turn(session, wire, surface, 1)
        await _assert_at_rest(session, wire, note="after turn 1")

        # Turn 2: the user speaks, the provider never responds.
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(type="input_transcript", text="anyone there", is_final=True),
        )
        await surface.wait_json(
            lambda m: m.get("type") == "transcript"
            and m.get("role") == "user"
            and m.get("text") == "anyone there",
            since=since,
        )

        for _ in range(3):
            before = len(wire.sent_audio)
            await asyncio.wait_for(session.handle_audio_frame(_pcm()), TIMEOUT_S)
            assert len(wire.sent_audio) == before + 1, (
                "a silent provider must not cost the session its microphone"
            )
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_a_late_user_transcript_does_not_break_the_turn_in_flight() -> None:
    """The provider's own user transcript trails its audio by seconds.

    Arriving after the assistant already started answering, it is a duplicate
    of an utterance that was already grounded — never evidence of a new one. It
    must not strand the reply or the turns after it.
    """
    session, _provider, wire, surface = await _open_call()
    try:
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(type="input_transcript", text="how are you", is_final=True),
            RealtimeEvent(type="output_transcript_delta", text="I am doing well."),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            # The far end's own copy of the SAME utterance, arriving late.
            RealtimeEvent(type="input_transcript", text="how are you", is_final=True),
            RealtimeEvent(type="turn_complete"),
        )
        await surface.wait_json(lambda m: m.get("type") == "turn_complete", since=since)
        await _assert_at_rest(session, wire, note="after a late duplicate transcript")

        await _run_turn(session, wire, surface, 2)
        await _assert_at_rest(session, wire, note="after the turn following a late transcript")
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_a_dead_local_recognizer_still_lets_the_assistant_answer() -> None:
    """The grounding evidence source failed; the answer must survive it.

    On this transport the local recognizer is the only source of a user
    transcript, so its failure is reported as an empty final transcript with an
    error — exactly what the adapter's missing-input boundary emits. The model
    already heard the audio, so its reply must still reach the user, and the
    call must remain usable.
    """
    session, _provider, wire, surface = await _open_call()
    try:
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(
                type="input_transcript",
                text="",
                is_final=True,
                error="Local and provider input transcription were unavailable.",
            ),
            RealtimeEvent(type="output_transcript_delta", text="I heard you anyway."),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            RealtimeEvent(type="turn_complete"),
        )
        await surface.wait_json(lambda m: m.get("type") == "turn_complete", since=since)

        spoken = [
            message
            for message in surface.json[since:]
            if message.get("type") == "transcript"
            and message.get("role") == "assistant"
        ]
        assert spoken, "the reply was dropped because the recognizer failed"
        assert surface.binary, "the reply's audio never reached the surface"
        assert wire.interrupts == 0, (
            "a dead evidence source must not interrupt the model"
        )
        await _assert_at_rest(session, wire, note="after a failed local recognizer")

        await _run_turn(session, wire, surface, 2)
        await _assert_at_rest(session, wire, note="after the turn following a failure")
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_a_hanging_close_does_not_stall_the_transport_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dead transport's socket close took its full window live
    (2026-08-06 17:42) - and the rebuild the call was waiting on stalled
    behind it. The close is a courtesy to a corpse: bounded, then abandoned.
    """
    monkeypatch.setattr(session_mod, "_PROVIDER_CLOSE_BOUND_S", 0.2)

    class _HangingCloseWire(_CodexShapedWire):
        async def close(self) -> None:
            await asyncio.sleep(30.0)

    class _RebuildingProvider(_CodexShapedProvider):
        def __init__(self) -> None:
            super().__init__()
            self.open_count = 0

        async def open_session(self, config: Any) -> _CodexShapedWire:
            self.opened_with = config
            self.open_count += 1
            wire = (
                _HangingCloseWire() if self.open_count == 1 else _CodexShapedWire()
            )
            self.sessions.append(wire)
            return wire

    surface = _Surface()
    provider = _RebuildingProvider()
    session = RealtimeVoiceSession(
        session_id="rebuild-close-bound",
        send_binary=surface.send_binary,
        send_json=surface.send_json,
        providers=[provider],
        config=_config(),
        bus=EventBus(),
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=INPUT_RATE,
    )
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": INPUT_RATE}),
        TIMEOUT_S,
    )
    try:
        first_wire = provider.sessions[0]
        since = surface.mark()
        first_wire.push(
            RealtimeEvent(
                type="error",
                error="transport died",
                recoverable=True,
                reconnect_advised=True,
            ),
            RealtimeEvent(type="turn_complete"),
        )
        # The rebuild must complete despite the corpse's 30 s close: a fresh
        # audio_ready proves the new transport opened.
        ready = await surface.wait_json(
            lambda m: m.get("type") == "audio_ready",
            since=since,
            timeout_s=TIMEOUT_S,
        )
        assert ready is not None
        assert provider.open_count == 2, "a second transport must have opened"
    finally:
        await _end(session)


def _session_with_bus(
    name: str,
) -> tuple[RealtimeVoiceSession, _CodexShapedProvider, _Surface, list[Any]]:
    """A desktop-shaped session whose VoiceTurnCompleted events are captured."""
    from jarvis.core.events import VoiceTurnCompleted

    surface = _Surface()
    provider = _CodexShapedProvider()
    bus = EventBus()
    completed: list[Any] = []

    async def _collect(event: VoiceTurnCompleted) -> None:
        completed.append(event)

    bus.subscribe(VoiceTurnCompleted, _collect)
    session = RealtimeVoiceSession(
        session_id=name,
        send_binary=surface.send_binary,
        send_json=surface.send_json,
        providers=[provider],
        config=_config(),
        bus=bus,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=INPUT_RATE,
    )
    return session, provider, surface, completed


async def _wait_completed(completed: list[Any]) -> None:
    for _ in range(50):
        if completed:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the turn never published VoiceTurnCompleted")


@pytest.mark.asyncio
async def test_a_partial_only_turn_promotes_its_preview_explicitly() -> None:
    """A turn whose FINAL never arrives must not silently record a mid-word
    partial as the user's utterance (the recorded "illst.") - the retained
    live caption is promoted, with its own log line, instead."""
    session, provider, surface, completed = _session_with_bus("preview-promote")
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": INPUT_RATE}),
        TIMEOUT_S,
    )
    try:
        wire = provider.sessions[0]
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(
                type="input_transcript",
                text="what color is the vio",
                is_final=False,
            ),
            RealtimeEvent(type="output_transcript_delta", text="Violet."),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            RealtimeEvent(type="turn_complete"),
        )
        await surface.wait_json(
            lambda m: m.get("type") == "turn_complete", since=since
        )
        await _wait_completed(completed)
        assert completed[0].user_text == "what color is the vio", (
            "the preview is promoted - explicitly - when no final arrived"
        )
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_a_refinalized_item_replaces_instead_of_doubling() -> None:
    """A provider re-finalizing the same input item (a correction) must not
    concatenate the utterance into itself."""
    session, provider, surface, completed = _session_with_bus("refinal-replace")
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": INPUT_RATE}),
        TIMEOUT_S,
    )
    try:
        wire = provider.sessions[0]
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(
                type="input_transcript",
                text="what color is the giraffe",
                is_final=True,
                item_id="item-1",
            ),
            RealtimeEvent(
                type="input_transcript",
                text="what color is the violet giraffe",
                is_final=True,
                item_id="item-1",
            ),
            RealtimeEvent(type="output_transcript_delta", text="Violet."),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            RealtimeEvent(type="turn_complete"),
        )
        await surface.wait_json(
            lambda m: m.get("type") == "turn_complete", since=since
        )
        await _wait_completed(completed)
        assert completed[0].user_text == "what color is the violet giraffe", (
            "the re-final replaces its item instead of double-booking it"
        )
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_a_partial_never_flips_the_call_language() -> None:
    """Language resolution runs on FINALS only: a growing caption used to
    flip the call language (and rebuild the scrub gate) mid-utterance."""
    session, provider, surface, _completed = _session_with_bus("partial-lang")
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": INPUT_RATE}),
        TIMEOUT_S,
    )
    try:
        wire = provider.sessions[0]
        opening_language = session._language  # noqa: SLF001
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(
                type="input_transcript",
                # A decidedly German partial on an English-opened call.
                text="wie viele Raeder hat das erfundene Fahrrad denn nun",
                is_final=False,
            ),
        )
        await asyncio.sleep(0.15)
        assert session._language == opening_language, (  # noqa: SLF001
            "a partial flipped the call language"
        )
    finally:
        await _end(session)


@pytest.mark.asyncio
async def test_a_thin_first_fragment_does_not_set_the_call_language() -> None:
    """A misheard 328 ms fragment ("Mask it up!") flipped a German call to
    English AND stuck. Before the conversation is established, a final under
    the voiced-duration floor resolves without its unreliable words; the
    first substantive final then establishes the language for real."""
    session, provider, surface, _completed = _session_with_bus("thin-fragment")
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": INPUT_RATE}),
        TIMEOUT_S,
    )
    try:
        wire = provider.sessions[0]
        opening_language = session._language  # noqa: SLF001
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(
                type="input_transcript",
                text="Mask it up!",
                is_final=True,
                voiced_ms=328,
            ),
            RealtimeEvent(type="output_transcript_delta", text="Hey!"),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            RealtimeEvent(type="turn_complete"),
        )
        await surface.wait_json(
            lambda m: m.get("type") == "turn_complete", since=since
        )
        assert session._language == opening_language, (  # noqa: SLF001
            "a 328 ms misheard fragment set the call language"
        )
        assert session._conversation_established is False  # noqa: SLF001

        # The first SUBSTANTIVE final establishes the language for real.
        since = surface.mark()
        wire.push(
            RealtimeEvent(type="speech_started"),
            RealtimeEvent(
                type="input_transcript",
                text="Wie viele Raeder hat mein erfundenes Fahrrad? Bitte sag es mir.",
                is_final=True,
                voiced_ms=2100,
            ),
            RealtimeEvent(type="output_transcript_delta", text="Fuenf."),
            RealtimeEvent(type="audio_delta", audio=_audio()),
            RealtimeEvent(type="turn_complete"),
        )
        await surface.wait_json(
            lambda m: m.get("type") == "turn_complete", since=since
        )
        assert session._conversation_established is True  # noqa: SLF001
    finally:
        await _end(session)
