"""The pipeline's Thinking-pause hold: the microphone outranks the transcript.

Maintainer directive 2026-08-18: "wait for a clear pause before you take the
turn; when I keep talking, append." The VAD already waits the configured pause
before it ends an utterance — but its final TRANSCRIPT lands a recognizer
round-trip later, and by then the user may audibly be into the next sentence.
Dispatching at that moment answers half a request and lets the instant ack
talk over the second half. So a COMPLETE utterance whose final arrives while
the VAD reports speech again is held (``ContinuationBuffer.hold``) and joined
with the next utterance: one dispatch, no interruption. Bounded by the drain
timer (short, deferred only while the user holds the floor) and released at
once on a VAD false start.

Driven against a real ``ContinuationBuffer`` with the ``__new__`` stubbing
pattern of ``test_continuation_drain``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from jarvis.speech import pipeline as pipeline_module
from jarvis.speech.continuation_buffer import REASON_MIC_RESUMED, ContinuationBuffer
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState

_FIRST = "Wie spät ist es"  # i18n-allow: spoken test utterance
_SECOND = "in Berlin gerade"  # i18n-allow: spoken test utterance


def _make_pipe(*, buffer_timeout_s: float = 8.0) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._continuation_buffer = ContinuationBuffer(timeout_s=buffer_timeout_s)
    pipe._continuation_drain_task = None
    pipe._clarify_timer_task = None
    pipe._turn_state = TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT
    pipe._vad_speech_after_endpoint = False
    pipe._continuation_pending_drop = None
    pipe._brain = None

    voice_cfg = MagicMock()
    voice_cfg.clarify_incomplete_enabled = False
    cfg = MagicMock()
    cfg.voice = voice_cfg
    pipe._config = cfg

    pipe._dispatched: list[tuple[str, str]] = []
    pipe._state_history: list[TurnTakingState] = []

    async def _fake_dispatch(text: str, lang: str = "de") -> None:
        pipe._dispatched.append((text, lang))

    async def _fake_set_turn_state(
        state: TurnTakingState, *, only_from: TurnTakingState | None = None
    ) -> None:
        if only_from is not None and pipe._turn_state is not only_from:
            return
        pipe._state_history.append(state)
        pipe._turn_state = state

    pipe._handle_flushed_pending_text = _fake_dispatch  # type: ignore[method-assign]
    pipe._set_turn_state = _fake_set_turn_state  # type: ignore[method-assign]
    return pipe


# --------------------------------------------------------------------------- #
# The microphone's word                                                        #
# --------------------------------------------------------------------------- #


def test_no_hold_without_microphone_evidence() -> None:
    pipe = _make_pipe()
    assert pipe._mic_says_user_resumed() is False


def test_a_vad_start_after_the_endpoint_means_the_user_resumed() -> None:
    pipe = _make_pipe()
    pipe._on_vad_speech_start()
    assert pipe._mic_says_user_resumed() is True


def test_a_silence_cancel_after_the_endpoint_means_the_user_resumed() -> None:
    pipe = _make_pipe()
    pipe._on_vad_silence_cancel()
    assert pipe._mic_says_user_resumed() is True


def test_user_speaking_state_alone_is_enough() -> None:
    """The turn state is moved to USER_SPEAKING by the same VAD callbacks."""
    pipe = _make_pipe()
    pipe._turn_state = TurnTakingState.USER_SPEAKING
    assert pipe._mic_says_user_resumed() is True


@pytest.mark.asyncio
async def test_the_endpoint_clears_the_resumed_flag() -> None:
    """Whatever the VAD heard BEFORE this utterance ended is not a resume."""
    pipe = _make_pipe()
    pipe._on_vad_speech_start()
    pipe._on_vad_endpoint("silence")
    await asyncio.sleep(0)
    assert bool(pipe._vad_speech_after_endpoint) is False


# --------------------------------------------------------------------------- #
# The hold itself                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_hold_joins_with_the_next_utterance() -> None:
    """The held first half and the second half dispatch as ONE text."""
    pipe = _make_pipe()
    pipe._on_vad_speech_start()  # the user talks on while the STT worked
    pipe._hold_for_resumed_speech(_FIRST, "de")
    try:
        buf = pipe._continuation_buffer
        assert buf.has_pending()
        assert buf.last_reason == REASON_MIC_RESUMED
        assert pipe._continuation_drain_task is not None
        # ...the next final arrives, as ``_handle_utterance_turn`` processes it:
        pipe._cancel_continuation_drain()
        joined = buf.process(_SECOND, language="de")
        assert joined == f"{_FIRST} {_SECOND}"
        assert not buf.has_pending()
        assert pipe._dispatched == [], "the join dispatches inline, never twice"
    finally:
        pipe._cancel_continuation_drain()


@pytest.mark.asyncio
async def test_hold_releases_at_once_on_a_vad_false_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The speech that held the text was a cough: the answer comes right away."""
    monkeypatch.setattr(pipeline_module, "_MIC_HOLD_DRAIN_S", 5.0)  # would be slow
    monkeypatch.setattr(pipeline_module, "_MIC_HOLD_RELEASE_S", 0.05)
    pipe = _make_pipe()
    pipe._on_vad_speech_start()
    pipe._turn_state = TurnTakingState.USER_SPEAKING
    pipe._hold_for_resumed_speech(_FIRST, "de")
    try:
        pipe._on_vad_endpoint("false_start")
        await asyncio.sleep(0.3)
        assert pipe._dispatched == [(_FIRST, "de")]
        assert not pipe._continuation_buffer.has_pending()
    finally:
        pipe._cancel_continuation_drain()


@pytest.mark.asyncio
async def test_hold_drains_after_the_short_grace_once_the_floor_is_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next utterance carried no words: the held text is answered anyway."""
    monkeypatch.setattr(pipeline_module, "_MIC_HOLD_DRAIN_S", 0.08)
    pipe = _make_pipe()
    pipe._on_vad_speech_start()
    await asyncio.sleep(0)  # the scheduled USER_SPEAKING lands...
    pipe._turn_state = TurnTakingState.LISTENING  # ...then the follow-up came and went
    pipe._hold_for_resumed_speech(_FIRST, "de")
    try:
        await asyncio.sleep(0.3)
        assert pipe._dispatched == [(_FIRST, "de")]
    finally:
        pipe._cancel_continuation_drain()


@pytest.mark.asyncio
async def test_hold_waits_while_the_user_speaks_and_stays_short_afterwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deferrals keep the SHORT release; the drain never pre-empts the user."""
    monkeypatch.setattr(pipeline_module, "_MIC_HOLD_DRAIN_S", 0.06)
    pipe = _make_pipe()
    pipe._on_vad_speech_start()
    pipe._turn_state = TurnTakingState.USER_SPEAKING
    pipe._hold_for_resumed_speech(_FIRST, "de")
    try:
        await asyncio.sleep(0.3)  # several grace windows: the user is talking
        assert pipe._dispatched == []
        assert pipe._continuation_buffer.has_pending()
        pipe._turn_state = TurnTakingState.LISTENING
        await asyncio.sleep(0.25)  # well under the 8 s buffer grace
        assert pipe._dispatched == [(_FIRST, "de")]
    finally:
        pipe._cancel_continuation_drain()


@pytest.mark.asyncio
async def test_hold_applies_a_parked_history_drop() -> None:
    """A recombine's deferred history drop lands with the hold, not never."""
    pipe = _make_pipe()
    brain = MagicMock()
    pipe._brain = brain
    pipe._continuation_pending_drop = "the prior dispatched text"
    pipe._on_vad_speech_start()
    pipe._hold_for_resumed_speech(f"the prior dispatched text {_SECOND}", "en")
    try:
        brain.drop_last_turn.assert_called_once_with("the prior dispatched text")
        assert pipe._continuation_pending_drop is None
    finally:
        pipe._cancel_continuation_drain()


# --------------------------------------------------------------------------- #
# ContinuationBuffer.hold                                                      #
# --------------------------------------------------------------------------- #


def test_buffer_hold_folds_into_an_existing_syntactic_fragment() -> None:
    buf = ContinuationBuffer(timeout_s=8.0, max_chain=2)
    assert buf.process("Nimm den Bus oder", language="de") is None  # i18n-allow: test utterance
    buf.hold("die Bahn nach Hause", language="de")  # i18n-allow: test utterance
    # ONE fragment, so a chain of holds never trips max_chain early.
    assert buf.has_pending()
    assert buf.last_reason == REASON_MIC_RESUMED
    joined = buf.process("und dann zurück.", language="de")  # i18n-allow: test utterance
    assert joined == "Nimm den Bus oder die Bahn nach Hause und dann zurück."  # i18n-allow


def test_buffer_hold_ignores_empty_text() -> None:
    buf = ContinuationBuffer()
    buf.hold("   ")
    assert not buf.has_pending()
    assert buf.last_reason == ""


def test_buffer_hold_normalizes_whitespace() -> None:
    buf = ContinuationBuffer()
    buf.hold("  what   is\nthe time ")
    assert buf.flush_pending() == "what is the time"
