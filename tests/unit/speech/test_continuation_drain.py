"""Autonomous drain of a silently-held continuation fragment (AD-OE6).

Live wedge 2026-06-19 (session da25113a, "…morgen ist ja Montag, oder?"):
a COMPLETE question whose last word was the conjunction "oder" was classified as
an open continuation and held by the ``ContinuationBuffer``. The buffer has NO
timer of its own — it only drops a stale fragment lazily on the *next*
``process()`` call. The user said nothing more (only a VAD false-start), so
``process()`` never ran again, the fragment hung in LISTENING, and ~30 s later
the session idle-timeout silently ``discard()``ed it — the brain was NEVER
called. Jarvis "listened forever" and never answered (the recurring
"hört für immer zu" report).

The clarifying-question timer (``_arm_clarify_question``) only covers the
trail-off case (``REASON_TRAILING_ELLIPSIS`` / the globally-enabled flag); for
every other held reason under the shipped clarify-OFF default it arms NOTHING,
which is exactly the silent hang. The fix is a second, independent timer:
``_arm_continuation_drain`` DISPATCHES the held fragment to the brain after the
grace window so the user always gets an answer attempt (zero silent drop). It
mirrors the clarify floor guard — it never pre-empts a continuation the user is
still speaking.

Driven directly against a real ``ContinuationBuffer`` + stubbed
``_handle_flushed_pending_text`` / ``_set_turn_state``, mirroring
``test_clarify_question``'s ``__new__`` stubbing pattern.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from jarvis.speech.completion import REASON_TRAILING_ELLIPSIS
from jarvis.speech.continuation_buffer import ContinuationBuffer
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


def _make_pipe(*, drain_timeout_s: float = 0.08) -> SpeechPipeline:
    """Minimal ``SpeechPipeline`` stub for the continuation-drain helpers."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._continuation_buffer = ContinuationBuffer(timeout_s=drain_timeout_s)
    pipe._continuation_drain_task = None
    pipe._clarify_timer_task = None
    pipe._turn_state = TurnTakingState.LISTENING

    voice_cfg = MagicMock()
    voice_cfg.clarify_incomplete_enabled = False  # shipped default
    voice_cfg.clarify_after_ms = 80
    cfg = MagicMock()
    cfg.voice = voice_cfg
    pipe._config = cfg

    pipe._dispatched: list[tuple[str, str]] = []
    pipe._state_history: list[TurnTakingState] = []
    pipe._spoken: list[tuple[str, str | None]] = []

    async def _fake_dispatch(text: str, lang: str = "de") -> None:
        pipe._dispatched.append((text, lang))

    async def _fake_set_turn_state(state: TurnTakingState) -> None:
        pipe._state_history.append(state)
        pipe._turn_state = state

    async def _fake_speak(
        text: str, language: str | None = None, *, kind: str = "reply"
    ) -> bool:
        pipe._spoken.append((text, language))
        return True

    pipe._handle_flushed_pending_text = _fake_dispatch  # type: ignore[method-assign]
    pipe._set_turn_state = _fake_set_turn_state  # type: ignore[method-assign]
    pipe._speak = _fake_speak  # type: ignore[method-assign]
    return pipe


# --------------------------------------------------------------------------- #
# The fix: drain a silently-held fragment to the brain instead of hanging.     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_held_fragment_drains_to_brain_when_no_continuation() -> None:
    # The exact live wedge: a conjunction-tail held fragment with the clarify
    # question OFF must be DISPATCHED to the brain after the grace window —
    # never left to rot until idle-timeout discards it (AD-OE6).
    pipe = _make_pipe(drain_timeout_s=0.08)
    held = pipe._continuation_buffer.process("Nimm den Bus oder", language="de")
    assert held is None and pipe._continuation_buffer.has_pending()

    pipe._arm_continuation_drain("de")
    await asyncio.sleep(0.3)  # let the grace window expire

    assert pipe._dispatched, "held fragment must be dispatched to the brain (AD-OE6)"
    text, lang = pipe._dispatched[0]
    assert "Bus" in text and lang == "de"
    assert not pipe._continuation_buffer.has_pending(), "buffer drained on dispatch"


@pytest.mark.asyncio
async def test_drain_cancelled_by_next_utterance() -> None:
    # A real continuation arriving cancels the pending drain so it can't
    # double-dispatch on top of the joined turn.
    pipe = _make_pipe(drain_timeout_s=10.0)  # long timer
    pipe._continuation_buffer.process("Nimm den Bus oder", language="de")
    pipe._arm_continuation_drain("de")
    task = pipe._continuation_drain_task
    assert task is not None and not task.done()

    pipe._cancel_continuation_drain()
    assert pipe._continuation_drain_task is None
    await asyncio.sleep(0)  # let the cancellation settle
    assert task.done()
    assert pipe._dispatched == []


# --------------------------------------------------------------------------- #
# Floor guard: the drain must never pre-empt a continuation in progress.       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_drain_defers_while_user_holds_floor() -> None:
    # The user RESUMED speaking the continuation → never pre-empt them. Defer +
    # re-arm; the held fragment must survive so the continuation can coalesce.
    pipe = _make_pipe(drain_timeout_s=0.06)
    pipe._continuation_buffer.process("Nimm den Bus oder", language="de")
    pipe._turn_state = TurnTakingState.USER_SPEAKING
    pipe._arm_continuation_drain("de")
    await asyncio.sleep(0.25)  # several grace windows elapse while user speaks

    assert pipe._dispatched == [], "must not dispatch while the user holds the floor"
    assert pipe._continuation_buffer.has_pending(), "held fragment must survive"
    assert pipe._continuation_drain_task is not None, "timer re-armed, not dropped"


@pytest.mark.asyncio
async def test_drain_dispatches_in_waiting_for_completion_state() -> None:
    # Regression for the review finding: WAITING_FOR_COMPLETION is the held-and-
    # idle state the drain EXISTS to resolve — it must DISPATCH there, not defer.
    # Deferring on it would let the held fragment rot until the idle-timeout (the
    # very "Jarvis hört für immer zu" wedge). It is in the announcement floor set
    # but deliberately NOT in the drain floor set (_DRAIN_HOLDS_FLOOR).
    pipe = _make_pipe(drain_timeout_s=0.06)
    pipe._continuation_buffer.process("Nimm den Bus oder", language="de")
    pipe._turn_state = TurnTakingState.WAITING_FOR_COMPLETION
    pipe._arm_continuation_drain("de")
    await asyncio.sleep(0.25)

    assert pipe._dispatched, "drain must dispatch in WAITING_FOR_COMPLETION, not defer"
    assert not pipe._continuation_buffer.has_pending()


@pytest.mark.asyncio
async def test_drain_defers_while_transcribing_continuation() -> None:
    # WAITING_FOR_FINAL_TRANSCRIPT means a resumed continuation is being
    # transcribed — defer so the drain never pre-empts it.
    pipe = _make_pipe(drain_timeout_s=0.06)
    pipe._continuation_buffer.process("Nimm den Bus oder", language="de")
    pipe._turn_state = TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT
    pipe._arm_continuation_drain("de")
    await asyncio.sleep(0.25)

    assert pipe._dispatched == [], "must not dispatch while the continuation is finalising"
    assert pipe._continuation_buffer.has_pending()
    assert pipe._continuation_drain_task is not None  # re-armed


@pytest.mark.asyncio
async def test_drain_fires_after_floor_clears() -> None:
    # Deferring must NOT drop the fragment forever: once the floor clears the
    # re-armed drain dispatches it (the zero-silent-drop contract — "Jarvis
    # listens forever" must not return through the back door).
    pipe = _make_pipe(drain_timeout_s=0.06)
    pipe._continuation_buffer.process("Nimm den Bus oder", language="de")
    pipe._turn_state = TurnTakingState.USER_SPEAKING
    pipe._arm_continuation_drain("de")
    await asyncio.sleep(0.2)  # deferred while the floor is held
    assert pipe._dispatched == []

    # The user stopped without ever continuing → the floor clears.
    pipe._turn_state = TurnTakingState.LISTENING
    await asyncio.sleep(0.2)  # the re-armed drain now fires

    assert pipe._dispatched, "drain must dispatch once the floor clears"
    assert not pipe._continuation_buffer.has_pending()


@pytest.mark.asyncio
async def test_drain_noop_when_buffer_already_drained() -> None:
    # If the continuation already arrived (buffer emptied) before the timer
    # fires, the drain is a harmless no-op — no spurious second dispatch.
    pipe = _make_pipe(drain_timeout_s=0.06)
    pipe._continuation_buffer.process("Nimm den Bus oder", language="de")
    pipe._arm_continuation_drain("de")
    pipe._continuation_buffer.discard()  # the continuation consumed it
    await asyncio.sleep(0.2)
    assert pipe._dispatched == []


def test_arm_continuation_drain_without_event_loop_is_noop() -> None:
    # Fail-open: no running loop (sync teardown context) must not raise.
    pipe = _make_pipe()
    pipe._continuation_buffer.process("Nimm den Bus oder", language="de")
    pipe._arm_continuation_drain("de")  # no running loop here
    assert pipe._continuation_drain_task is None


# --------------------------------------------------------------------------- #
# _arm_clarify_question now REPORTS whether it armed, so _handle_utterance      #
# knows to fall back to the drain timer when no clarifying question was set.    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_arm_clarify_question_returns_false_on_silent_hold() -> None:
    # Non-trail-off incomplete + clarify OFF (shipped default) → no question
    # armed → the pipeline must learn this so it can arm the drain instead.
    pipe = _make_pipe()
    pipe._continuation_buffer.process("Nimm den Bus oder", language="de")
    armed = pipe._arm_clarify_question("de", force=False)
    assert armed is False
    assert pipe._clarify_timer_task is None


@pytest.mark.asyncio
async def test_arm_clarify_question_returns_true_when_forced() -> None:
    # A genuine trail-off forces the clarifying question even with the flag off
    # → arms a question → returns True → pipeline does NOT also arm the drain.
    pipe = _make_pipe()
    pipe._continuation_buffer.process(
        "Kannst du mir sagen, was genau...", language="de"
    )
    assert pipe._continuation_buffer.last_reason == REASON_TRAILING_ELLIPSIS
    armed = pipe._arm_clarify_question("de", force=True)
    assert armed is True
    pipe._cancel_clarify_question()  # cleanup the pending fire task
    await asyncio.sleep(0)
