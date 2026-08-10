"""Response-identity recovery: a superseded response never kills the next one.

Two pinned regressions around ``_active_provider_response_id``:

* ``_reset_output_state`` retires the active provider response on every path
  that ends one — including the half-duplex emergency release, where no
  provider boundary ever arrived and the scrub gate is still bound
  fail-closed to the dead response. Leaving that binding standing made the
  NEXT response's ``begin_response`` read as a ``response_identity_mismatch``
  hard leak, cancelling the real answer into the generic fallback phrase.

* In ``_accept_provider_response_event``'s identity-mismatch path,
  ``_cancel_unsafe_output`` armed ``_drop_provider_output_until_new_response``
  for the STALE identity, and on adapters without
  ``isolates_response_generations`` nothing ever cleared it: the superseding
  response's own audio and transcript were withheld, contradicting the
  branch's clean-start promise. Only events of the CANCELLED id may stay
  dropped (via the completed-ids ledger).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession

TIMEOUT_S = 5.0
RATE = 24_000
A_PCM = b"\x01\x02" * 160  # int16 peak 513, above the silence floor
A_LATE_PCM = b"\x05\x06" * 160  # late audio of the cancelled identity
B_PCM = b"\x03\x04" * 160  # the superseding response's audio
SILENT_PCM = b"\x00\x00" * 160


class _ScriptedWire:
    session_id = "identity-recovery-wire"
    supports_tool_updates = True
    creates_responses_automatically = False
    # Deliberately False: the withhold-release contract must hold on EVERY
    # adapter, not only ones that isolate response generations.
    isolates_response_generations = False

    def __init__(self, events: list[RealtimeEvent]) -> None:
        self._events = events

    async def receive(self):  # noqa: ANN201 - async generator, protocol shape
        for event in self._events:
            yield event
            await asyncio.sleep(0)

    async def send_audio(self, chunk: Any) -> None:
        del chunk

    async def update_session(self, **kwargs: Any) -> None:
        del kwargs

    async def request_response(self, **kwargs: Any) -> None:
        del kwargs

    async def send_text(self, text: str) -> None:
        del text

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms

    async def interrupt(self, **kwargs: Any) -> None:
        del kwargs

    async def send_tool_result(self, *args: Any) -> None:
        del args

    async def close(self) -> None:
        return None


class _Provider:
    name = "scripted"
    supports_realtime = True
    input_sample_rate = RATE
    output_sample_rate = RATE

    def __init__(self, events: list[RealtimeEvent]) -> None:
        self._events = events

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _ScriptedWire:
        del config
        return _ScriptedWire(self._events)


class _RecordingGate:
    """Fake scrub gate recording its lifecycle calls (fakes, not mocks)."""

    def __init__(self, response_id: str = "") -> None:
        self.response_id = response_id
        self.calls: list[Any] = []

    def drain(self) -> None:
        self.calls.append("drain")
        self.response_id = ""

    def begin_response(self, response_id: str = "") -> bool:
        self.calls.append(("begin", response_id))
        return True


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


def _session(
    events: list[RealtimeEvent] | None = None,
    binaries: list[bytes] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> RealtimeVoiceSession:
    async def _collect_binary(data: bytes) -> None:
        if binaries is not None:
            binaries.append(bytes(data))

    async def _collect_json(message: dict[str, Any]) -> None:
        if messages is not None:
            messages.append(dict(message))

    return RealtimeVoiceSession(
        session_id="identity-recovery",
        send_binary=_collect_binary,
        send_json=_collect_json,
        providers=[_Provider(events or [])],
        config=_config(),
        bus=None,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=RATE,
    )


def _audio_event(response_id: str, pcm: bytes = A_PCM) -> RealtimeEvent:
    return RealtimeEvent(
        type="audio_delta",
        audio=AudioChunk(pcm=pcm, sample_rate=RATE, timestamp_ns=0),
        provider_turn_id=response_id,
    )


async def test_output_reset_unbinds_the_gate_from_the_retired_response() -> (
    None
):
    """Mute release with no boundary: the next response must start clean."""
    session = _session()
    assert await session._accept_provider_response_event(
        _audio_event("resp-a")
    )
    assert session._gate.response_id == "resp-a"

    # No provider boundary ever arrives; the half-duplex emergency release
    # (or any other end-of-response path) resets the output state.
    session._reset_output_state(reason="half-duplex mute outlived its turn")

    assert session._gate.response_id == "", (
        "the retired response may not keep owning the scrub gate"
    )
    assert await session._accept_provider_response_event(
        _audio_event("resp-b")
    ), "the next response must be accepted, not read as an identity leak"
    assert not session._gate.hard_leak_pending()
    assert session._response_identity_drops == 0
    assert session._scrub_cancelled_for_turn is False


def test_output_reset_drains_only_a_bound_gate() -> None:
    session = _session()

    bound = _RecordingGate("resp-a")
    session._gate = bound  # type: ignore[assignment]
    session._active_provider_response_id = "resp-a"
    session._reset_output_state(reason="test: bound gate")
    assert "drain" in bound.calls
    assert bound.response_id == ""


async def test_a_watchdog_retirement_keeps_late_audio_playable() -> None:
    """The 2026-08-09 silence: 1 419 discarded frames, 28.4 s of speech.

    The half-duplex watchdog reopens the microphone after 2 s of provider
    quiet, but ChatGPT-Live delivered its audio 5-13 s after the transcript.
    Completing the response on that guess put its id on the completed ledger,
    so every frame still in flight was dropped as late and the user heard
    nothing. A local timeout may release the microphone, never the answer.
    """
    binaries: list[bytes] = []
    session = _session(binaries=binaries)
    assert await session._accept_provider_response_event(_audio_event("resp-a"))

    session._reset_output_state(
        reason="half-duplex mute outlived its turn",
        provisional=True,
    )
    assert session._active_provider_response_id == ""
    assert "resp-a" not in session._completed_provider_response_ids, (
        "a watchdog guess must not complete a response the provider never ended"
    )

    # The rest of the SAME answer finally arrives.
    assert await session._accept_provider_response_event(
        _audio_event("resp-a", A_LATE_PCM)
    ), "late audio of a provisionally retired response must still be played"
    assert session._late_response_readoptions == 1
    assert session._response_identity_drops == 0
    assert session._active_provider_response_id == "resp-a"


async def test_a_watchdog_retirement_ignores_a_silent_webrtc_tail() -> None:
    """Silent carrier frames must not repeatedly deafen the next turn.

    The live Codex transport kept emitting silent PCM after the first reply.
    Every frame re-adopted the provisionally retired response, set output
    active again, and made half-duplex discard the user's follow-up.  Late
    metadata is equally unable to prove that audible output resumed.
    """
    session = _session()
    assert await session._accept_provider_response_event(_audio_event("resp-a"))
    session._reset_output_state(reason="watchdog", provisional=True)

    assert not await session._accept_provider_response_event(
        _audio_event("resp-a", SILENT_PCM)
    )
    assert not await session._accept_provider_response_event(
        RealtimeEvent(
            type="output_transcript_delta",
            text="late metadata",
            provider_turn_id="resp-a",
        )
    )
    assert session._active_provider_response_id == ""
    assert "resp-a" in session._provisional_response_retirements
    assert session._late_response_readoptions == 0

    assert await session._accept_provider_response_event(
        _audio_event("resp-a", A_LATE_PCM)
    ), "real audible audio may still revive the delayed answer"
    assert session._active_provider_response_id == "resp-a"
    assert session._late_response_readoptions == 1


async def test_a_superseding_response_completes_the_provisional_one() -> None:
    """Re-adoption is bounded: a real successor buries its predecessor."""
    session = _session()
    assert await session._accept_provider_response_event(_audio_event("resp-a"))
    session._reset_output_state(reason="watchdog", provisional=True)

    # A genuinely new response binds — resp-a is superseded for good.
    assert await session._accept_provider_response_event(_audio_event("resp-b"))
    assert "resp-a" in session._completed_provider_response_ids
    assert not session._provisional_response_retirements

    session._reset_output_state(reason="surface turn boundary")
    assert not await session._accept_provider_response_event(
        _audio_event("resp-a", A_LATE_PCM)
    ), "a superseded response may not surface behind the answer replacing it"
    assert session._response_identity_drops == 1


async def test_transport_rebuild_resets_response_identity_scope() -> None:
    """Tagged ids from a dead wire cannot poison its replacement transport."""
    session = _session()
    provider = session._providers[0]  # noqa: SLF001 - contract fixture
    provider.rebuild_on_transport_death = True
    await session._open()
    try:
        assert await session._accept_provider_response_event(
            _audio_event("reused-id")
        )
        assert await session._accept_provider_response_event(
            RealtimeEvent(type="turn_complete", provider_turn_id="reused-id")
        )
        assert session._provider_response_identity_required is True
        assert "reused-id" in session._completed_provider_response_ids

        assert await session._rebuild_transport(detail="identity scope test")

        assert session._provider_response_identity_required is False
        assert not session._completed_provider_response_ids
        assert not session._provisional_response_retirements
        assert await session._accept_provider_response_event(
            _audio_event("")
        ), "an ordered untagged replacement transport must remain compatible"
        assert await session._accept_provider_response_event(
            _audio_event("reused-id")
        ), "a fresh transport may reuse an id from the dead transport"
    finally:
        await session.end(reason="test")


async def test_the_readoption_window_expires() -> None:
    """Patience is bounded by the provider's own declared render budget."""
    session = _session()
    assert await session._accept_provider_response_event(_audio_event("resp-a"))
    session._reset_output_state(reason="watchdog", provisional=True)

    # Age the pending retirement past its deadline.
    session._provisional_response_retirements["resp-a"] = -1.0
    assert not await session._accept_provider_response_event(
        _audio_event("resp-a", A_LATE_PCM)
    ), "audio arriving long after the window must stay dropped"
    assert session._late_response_readoptions == 0
    assert session._response_identity_drops == 1


def test_the_readoption_window_follows_the_provider_capability() -> None:
    """AP-21: a capability read, never a provider-id check."""
    session = _session()
    assert session._late_response_readoption_window_s() == 15.0

    session._provider = SimpleNamespace(readback_render_budget_s=12.0)
    assert session._late_response_readoption_window_s() == 15.0, (
        "the floor still outlasts the 2 s watchdog that retired the response"
    )
    session._provider = SimpleNamespace(readback_render_budget_s=30.0)
    assert session._late_response_readoption_window_s() == 30.0

    unbound = _RecordingGate("")
    session._gate = unbound  # type: ignore[assignment]
    session._reset_output_state(reason="test: unbound gate")
    assert unbound.calls == [], (
        "an unbound gate keeps its state (direct-speech clearance and all); "
        "reset has nothing to end there"
    )


async def test_a_superseding_response_flows_after_the_stale_one_is_cancelled() -> (  # noqa: E501
    None
):
    """Response B supersedes A without a boundary: B's audio must play."""
    binaries: list[bytes] = []
    messages: list[dict[str, Any]] = []
    events = [
        RealtimeEvent(
            type="input_transcript",
            text="Hello how are you",
            is_final=True,
            item_id="turn-1",
        ),
        RealtimeEvent(
            type="output_transcript_delta",
            text="Answer A.",
            provider_turn_id="resp-a",
        ),
        _audio_event("resp-a", A_PCM),
        # Identity changes with no boundary for resp-a: this first mismatched
        # event is dropped and resp-a is cancelled once.
        RealtimeEvent(
            type="output_transcript_delta",
            text="Answer B instead.",
            provider_turn_id="resp-b",
        ),
        # Late audio of the CANCELLED identity stays dropped.
        _audio_event("resp-a", A_LATE_PCM),
        # The superseding response's own audio and transcript must flow.
        _audio_event("resp-b", B_PCM),
        RealtimeEvent(
            type="output_transcript_delta",
            text="Answer B instead.",
            provider_turn_id="resp-b",
        ),
        RealtimeEvent(type="turn_complete", provider_turn_id="resp-b"),
    ]
    session = _session(events, binaries, messages)
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": RATE}),
        TIMEOUT_S,
    )
    await asyncio.wait_for(session.wait_finished(), TIMEOUT_S)
    await asyncio.wait_for(session.end(reason="test"), TIMEOUT_S)

    assert B_PCM in binaries, (
        "the superseding response's audio was withheld — the drop armed for "
        "the cancelled identity must not silence the new one"
    )
    assert A_LATE_PCM not in binaries, (
        "late audio of the cancelled identity must stay dropped"
    )
    assert any(
        message.get("type") == "transcript"
        and message.get("role") == "assistant"
        and "Answer B" in str(message.get("text") or "")
        for message in messages
    ), "the superseding response's transcript must reach the surface"
    cancels = [m for m in messages if m.get("type") == "tts_cancel"]
    assert len(cancels) == 1, (
        "exactly one cancel: the stale identity, never the superseding one"
    )


async def test_an_idle_response_rollover_never_leaks_an_apology_into_the_next_turn() -> None:
    """A late identity race after turn completion has no audible owner.

    Live 2026-08-09 20:47: the old turn had already returned to listening when
    a mismatched provider response arrived. The generic fallback was queued on
    no turn, falsely recorded as spoken, then prepended to the next answer.
    """
    messages: list[dict[str, Any]] = []
    session = _session(messages=messages)
    session._active_provider_response_id = "resp-a"
    session._gate.begin_response("resp-a")

    accepted = await session._accept_provider_response_event(
        RealtimeEvent(
            type="output_transcript_delta",
            text="stale successor",
            provider_turn_id="resp-b",
        )
    )

    assert accepted is False
    assert "".join(session._output_transcript) == ""
    assert not any(message.get("type") == "error_spoken" for message in messages)
    assert session._scrub_cancelled_for_turn is False
    assert session._active_provider_response_id == "resp-b"
