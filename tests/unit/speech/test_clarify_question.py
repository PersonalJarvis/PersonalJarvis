"""Clarifying-question ("Zwischenfrage") for an abandoned incomplete utterance.

Root cause (user report 2026-06-08, "Jarvis hört für immer zu"): when the user
trails off on an open-ended fragment ("…erinnere mich daran, dass" + silence),
the ``ContinuationBuffer`` holds it with NO active timeout — it only drops the
stale fragment lazily on the *next* ``process()`` call. So a user who stops
talking is left in silence indefinitely: no answer, no question, mic open. This
violates AD-OE6 ("zero silent drops") for the incomplete-hold path.

This supersedes the 2026-05-26 *silent-discard* mandate (encoded in
``test_pipeline_completion.py``): the user now explicitly wants a clarifying
question instead of silence. The patience window is preserved — the question
only fires AFTER the grace window expires with no continuation, so a genuine
thinking-pause-then-continue is never interrupted.

Tested via the same ``SpeechPipeline.__new__`` stubbing pattern as
``test_pipeline_completion`` — the new clarify helpers
(``_arm_clarify_question`` / ``_cancel_clarify_question`` /
``_clarify_question_fire``) are driven directly against a real
``ContinuationBuffer``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from jarvis.core.config import VoiceConfig
from jarvis.speech.continuation_buffer import ContinuationBuffer
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


def _make_pipe(
    *,
    enabled: bool = True,
    clarify_after_ms: int = 80,
) -> SpeechPipeline:
    """Minimal ``SpeechPipeline`` stub for the clarify-question helpers."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._continuation_buffer = ContinuationBuffer()
    pipe._clarify_timer_task = None
    pipe._turn_state = TurnTakingState.LISTENING

    voice_cfg = MagicMock()
    voice_cfg.clarify_incomplete_enabled = enabled
    voice_cfg.clarify_after_ms = clarify_after_ms
    cfg = MagicMock()
    cfg.voice = voice_cfg
    pipe._config = cfg

    pipe._spoken: list[tuple[str, str | None]] = []
    pipe._state_history: list[TurnTakingState] = []

    async def _fake_speak(text: str, language: str | None = None) -> bool:
        pipe._spoken.append((text, language))
        return True

    async def _fake_set_turn_state(state: TurnTakingState) -> None:
        pipe._state_history.append(state)
        pipe._turn_state = state

    pipe._speak = _fake_speak  # type: ignore[method-assign]
    pipe._set_turn_state = _fake_set_turn_state  # type: ignore[method-assign]
    return pipe


# --------------------------------------------------------------------------- #
# Config defaults                                                              #
# --------------------------------------------------------------------------- #


def test_voice_config_has_clarify_defaults() -> None:
    cfg = VoiceConfig()
    # User-mandated 2026-06-08: ask a clarifying question instead of silently
    # dropping an abandoned incomplete fragment.
    assert cfg.clarify_incomplete_enabled is True
    assert cfg.clarify_after_ms == 2500


# --------------------------------------------------------------------------- #
# The fix: ask, don't drop silently                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_abandoned_incomplete_fragment_triggers_clarifying_question() -> None:
    pipe = _make_pipe(clarify_after_ms=80)
    # User trails off on a dangling conjunction → ContinuationBuffer holds it.
    held = pipe._continuation_buffer.process("erinnere mich daran, dass", language="de")
    assert held is None  # buffered, waiting for the continuation
    assert pipe._continuation_buffer.has_pending() is True

    pipe._arm_clarify_question("de")
    await asyncio.sleep(0.3)  # let the grace window expire

    # A clarifying question was SPOKEN (not silently discarded) — AD-OE6.
    assert len(pipe._spoken) == 1, pipe._spoken
    spoken_text, spoken_lang = pipe._spoken[0]
    assert spoken_text.strip().endswith("?"), spoken_text
    assert spoken_lang == "de"
    # The stale fragment was cleared so it can't pollute the next turn.
    assert pipe._continuation_buffer.has_pending() is False
    # It actually spoke (entered JARVIS_SPEAKING) then returned to LISTENING.
    assert TurnTakingState.JARVIS_SPEAKING in pipe._state_history
    assert pipe._state_history[-1] == TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_clarifying_question_is_english_for_english_fragment() -> None:
    pipe = _make_pipe(clarify_after_ms=80)
    held = pipe._continuation_buffer.process("remind me tomorrow that", language="en")
    assert held is None
    pipe._arm_clarify_question("en")
    await asyncio.sleep(0.3)
    assert len(pipe._spoken) == 1
    _text, lang = pipe._spoken[0]
    assert lang == "en"


# --------------------------------------------------------------------------- #
# Patience preserved: a continuation cancels the question                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_continuation_cancels_pending_clarifying_question() -> None:
    pipe = _make_pipe(clarify_after_ms=10_000)  # long timer
    pipe._continuation_buffer.process("erinnere mich daran, dass", language="de")
    pipe._arm_clarify_question("de")
    task = pipe._clarify_timer_task
    assert task is not None and not task.done()

    # The next utterance arrived → the pipeline cancels the pending question.
    pipe._cancel_clarify_question()
    assert pipe._clarify_timer_task is None
    await asyncio.sleep(0)  # let the cancellation settle
    assert task.done()
    assert pipe._spoken == []  # nothing spoken — the user kept the floor


# --------------------------------------------------------------------------- #
# Backwards-compat: the feature can be turned off (old silent behaviour)       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_disabled_flag_keeps_the_old_silent_behaviour() -> None:
    pipe = _make_pipe(enabled=False, clarify_after_ms=80)
    pipe._continuation_buffer.process("erinnere mich daran, dass", language="de")
    pipe._arm_clarify_question("de")
    await asyncio.sleep(0.3)
    # Feature off → no timer armed, nothing spoken, fragment still pending
    # (the ContinuationBuffer's own lazy timeout handles it as before).
    assert pipe._clarify_timer_task is None
    assert pipe._spoken == []
    assert pipe._continuation_buffer.has_pending() is True
    # The most dangerous regression: the state machine must NOT be touched.
    assert TurnTakingState.JARVIS_SPEAKING not in pipe._state_history


# =========================================================================== #
# Silent-brain-turn guarantee (the dominant "Jarvis antwortet nie" cause).     #
#                                                                              #
# Live logs 2026-06-08: conversational turns made the router brain return a    #
# `function_call` (Computer-Use) or empty content. The turn produced NO TTS    #
# frames, the playback watchdog mis-fired "device-wedge recovery", and the     #
# turn ended in silence. `_handle_silent_brain_turn` closes that AD-OE6 hole:  #
# an empty turn that is NOT a fire-and-forget spawn gets a spoken clarifying    #
# question, which also produces audio frames and breaks the watchdog cascade.  #
# =========================================================================== #


class _FakeBrain:
    def __init__(self, *, failed: bool, suppressed: bool) -> None:
        self._last_turn_all_failed = failed
        self._last_turn_suppressed = suppressed


def _make_silent_pipe(
    *,
    failed: bool = False,
    suppressed: bool = False,
    enabled: bool = True,
) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._brain = _FakeBrain(failed=failed, suppressed=suppressed)

    voice_cfg = MagicMock()
    voice_cfg.clarify_incomplete_enabled = enabled
    cfg = MagicMock()
    cfg.voice = voice_cfg
    pipe._config = cfg

    pipe._spoken: list[tuple[str, str | None]] = []
    pipe._state_history: list[TurnTakingState] = []

    async def _fake_speak(text: str, language: str | None = None) -> bool:
        pipe._spoken.append((text, language))
        return True

    async def _fake_set_turn_state(state: TurnTakingState) -> None:
        pipe._state_history.append(state)

    pipe._speak = _fake_speak  # type: ignore[method-assign]
    pipe._set_turn_state = _fake_set_turn_state  # type: ignore[method-assign]
    return pipe


@pytest.mark.asyncio
async def test_empty_non_spawn_turn_speaks_a_clarifying_question() -> None:
    # The user's case: brain returned a function_call / empty (NOT a spawn,
    # NOT a total failure). Must speak instead of dropping to silence.
    pipe = _make_silent_pipe(failed=False, suppressed=False)
    await pipe._handle_silent_brain_turn("de")
    assert len(pipe._spoken) == 1, pipe._spoken
    text, lang = pipe._spoken[0]
    assert text.strip().endswith("?")
    assert lang == "de"
    assert TurnTakingState.JARVIS_SPEAKING in pipe._state_history


@pytest.mark.asyncio
async def test_suppress_response_spawn_stays_silent() -> None:
    # Fire-and-forget spawn_worker: feedback arrives over the bus, so the
    # pipeline must NOT speak a clarifying question on top of it.
    pipe = _make_silent_pipe(failed=False, suppressed=True)
    await pipe._handle_silent_brain_turn("de")
    assert pipe._spoken == []
    assert TurnTakingState.JARVIS_SPEAKING not in pipe._state_history


@pytest.mark.asyncio
async def test_total_provider_failure_speaks_unavailable_not_clarify() -> None:
    # A real all-providers-down turn keeps its dedicated "brain unreachable"
    # message — it must NOT be replaced by the clarifying question.
    pipe = _make_silent_pipe(failed=True, suppressed=False)
    await pipe._handle_silent_brain_turn("de")
    assert len(pipe._spoken) == 1
    text, _lang = pipe._spoken[0]
    # The brain-unavailable phrase is a statement, not the "?" clarify cue.
    assert not text.strip().endswith("?")


@pytest.mark.asyncio
async def test_silent_turn_clarify_disabled_stays_silent() -> None:
    pipe = _make_silent_pipe(failed=False, suppressed=False, enabled=False)
    await pipe._handle_silent_brain_turn("de")
    assert pipe._spoken == []


@pytest.mark.asyncio
async def test_explicit_cancel_does_not_trigger_clarifying_question() -> None:
    # "vergiss das" is a deliberate abort — answering it with "Wie meinst du
    # das?" would be wrong. The user explicitly took the floor back.
    pipe = _make_silent_pipe(failed=False, suppressed=False)
    await pipe._handle_silent_brain_turn("de", "vergiss das")
    assert pipe._spoken == []
    assert TurnTakingState.JARVIS_SPEAKING not in pipe._state_history
