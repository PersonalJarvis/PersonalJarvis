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
from jarvis.speech.pipeline import (
    _CLARIFY_QUESTION_PHRASE,
    SpeechPipeline,
    TurnTakingState,
)


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

    async def _fake_speak(
        text: str, language: str | None = None, *, kind: str = "reply"
    ) -> bool:
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
    # User-mandated 2026-06-09 (REVERSES the 2026-06-08 opt-in): the maintainer
    # was constantly interrogated with "Wie meinst du das genau?" because every
    # empty brain turn (Gemini function_call without narration / Codex CLI
    # timing out on the voice path) fell through to the clarifying question. The
    # original "Jarvis hört für immer zu" cause was the watchdog stale-counter
    # (BUG-032), fixed separately — so the clarify question lost its only real
    # purpose and now just blames the user for a brain glitch. Shipped default
    # is OFF; the feature stays available as an explicit opt-in.
    assert cfg.clarify_incomplete_enabled is False
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
async def test_ellipsis_trailoff_clarifies_even_when_globally_disabled() -> None:
    # User choice 2026-06-14: a TRAILED-OFF sentence ("...") asks "what exactly?"
    # even with the global clarify default OFF — scoped to trail-offs only, so
    # the 2026-05-26 silent mandate stays intact for every OTHER incomplete case.
    from jarvis.speech.completion import REASON_TRAILING_ELLIPSIS

    pipe = _make_pipe(enabled=False, clarify_after_ms=80)
    held = pipe._continuation_buffer.process(
        "Kannst du mir sagen, was genau...", language="de"
    )
    assert held is None
    assert pipe._continuation_buffer.last_reason == REASON_TRAILING_ELLIPSIS

    # The pipeline forces the clarify for a trail-off despite the disabled flag.
    pipe._arm_clarify_question("de", force=True)
    await asyncio.sleep(0.3)

    assert len(pipe._spoken) == 1, pipe._spoken
    spoken_text, spoken_lang = pipe._spoken[0]
    assert spoken_text.strip().endswith("?"), spoken_text
    assert spoken_lang == "de"


@pytest.mark.asyncio
async def test_non_ellipsis_incomplete_stays_silent_when_disabled() -> None:
    # Scoping guard: a NON-trail-off incomplete (dangling conjunction) with the
    # global flag OFF must still stay silent — force is only set for trail-offs.
    pipe = _make_pipe(enabled=False, clarify_after_ms=80)
    pipe._continuation_buffer.process("erinnere mich daran, dass", language="de")
    pipe._arm_clarify_question("de", force=False)
    await asyncio.sleep(0.3)
    assert pipe._clarify_timer_task is None
    assert pipe._spoken == []


@pytest.mark.asyncio
async def test_absent_voice_config_stays_silent_for_non_ellipsis() -> None:
    # Safe-default guard: if the voice config is missing entirely, a non-forced
    # (non-trail-off) incomplete must NOT arm the clarify timer — the absent
    # config defaults to OFF, not ON (2026-06-09 "don't interrogate me" mandate).
    pipe = _make_pipe(enabled=False, clarify_after_ms=80)
    pipe._config.voice = None  # no voice section at all
    pipe._continuation_buffer.process("erinnere mich daran, dass", language="de")
    pipe._arm_clarify_question("de", force=False)
    await asyncio.sleep(0.2)
    assert pipe._clarify_timer_task is None
    assert pipe._spoken == []


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
# Floor guard: the clarify question must never speak OVER a resuming user.     #
#                                                                             #
# Live incident 2026-06-17 14:47 (session f6403ec0): the user trailed off on  #
# "...liegt sie im..." → the ContinuationBuffer held it (reason=trailing_      #
# ellipsis) and force-armed the clarify timer. 4 ms later the user RESUMED     #
# speaking the continuation ("im Lead zu Vergleich zu anderen"), but the       #
# clarify timer fired 2.5 s into that continuation, spoke "Wie meinst du das   #
# genau?" while turn-state was USER_SPEAKING, and DISCARDED the held first     #
# half — so the continuation reached the brain alone and got a confused        #
# non-answer. The cancel path only runs once the NEXT utterance FINALISES,     #
# which is too late for a continuation that takes >grace to speak. The fix     #
# mirrors the Flash-Brain ack / announcement AD-OE5 floor guard: defer while   #
# the user holds the floor.                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_clarify_defers_while_user_holds_floor() -> None:
    from jarvis.speech.completion import REASON_TRAILING_ELLIPSIS

    pipe = _make_pipe(clarify_after_ms=60)
    held = pipe._continuation_buffer.process(
        "was genau liegt sie im...", language="de"
    )
    assert held is None
    assert pipe._continuation_buffer.last_reason == REASON_TRAILING_ELLIPSIS

    # The user has RESUMED speaking the continuation — they hold the floor.
    pipe._turn_state = TurnTakingState.USER_SPEAKING

    pipe._arm_clarify_question("de", force=True)
    await asyncio.sleep(0.25)  # several grace windows elapse while user speaks

    # Must NOT have spoken over the user and must NOT have discarded the held
    # fragment (it has to survive so the continuation can coalesce on finalise).
    assert pipe._spoken == [], pipe._spoken
    assert TurnTakingState.JARVIS_SPEAKING not in pipe._state_history
    assert pipe._continuation_buffer.has_pending() is True
    # The timer was re-armed (deferred), not dropped.
    assert pipe._clarify_timer_task is not None


@pytest.mark.asyncio
async def test_clarify_defers_while_waiting_for_completion() -> None:
    # The held-fragment state itself (WAITING_FOR_COMPLETION) is a floor state:
    # the words are still being finalised, so the question must not barge.
    pipe = _make_pipe(clarify_after_ms=60)
    pipe._continuation_buffer.process("was genau liegt sie im...", language="de")
    pipe._turn_state = TurnTakingState.WAITING_FOR_COMPLETION

    pipe._arm_clarify_question("de", force=True)
    await asyncio.sleep(0.2)

    assert pipe._spoken == [], pipe._spoken
    assert pipe._continuation_buffer.has_pending() is True


@pytest.mark.asyncio
async def test_clarify_fires_after_floor_clears() -> None:
    # Defense-in-depth: deferring while the floor is held must NOT drop the
    # question forever. Once the user truly stops (floor clears) the re-armed
    # timer asks the clarifying question — the AD-OE6 zero-silent-drop contract
    # for a genuine trail-off-into-silence is preserved ("Jarvis listens
    # forever" must not return).
    pipe = _make_pipe(clarify_after_ms=60)
    pipe._continuation_buffer.process("was genau liegt sie im...", language="de")
    pipe._turn_state = TurnTakingState.USER_SPEAKING
    pipe._arm_clarify_question("de", force=True)
    await asyncio.sleep(0.2)  # deferred while the floor is held
    assert pipe._spoken == []

    # The user stopped without ever continuing → the floor clears.
    pipe._turn_state = TurnTakingState.LISTENING
    await asyncio.sleep(0.2)  # the re-armed timer now fires

    assert len(pipe._spoken) == 1, pipe._spoken
    text, lang = pipe._spoken[0]
    assert text.strip().endswith("?")
    assert lang == "de"
    assert pipe._continuation_buffer.has_pending() is False


# --------------------------------------------------------------------------- #
# Beheaded turn (no-first-frame TTS ceiling): always audible, clarify-off     #
# notwithstanding (AD-OE6)                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_beheaded_turn_speaks_timeout_notice_despite_clarify_off() -> None:
    """A turn beheaded by the no-first-frame TTS ceiling must end AUDIBLY even
    with the clarify question disabled (its shipped default). Live bug
    2026-06-10 14:34: a 20 s mute brain turn was ceiling-aborted, came back
    empty, and the session dropped to silent LISTENING until the idle hang-up.
    The timeout notice is an error report, not an interrogating question, so
    the 2026-06-09 clarify-off mandate does not cover it."""
    pipe = _make_pipe(enabled=False)
    pipe._playback_aborted_no_first_frame = True

    await pipe._handle_silent_brain_turn("de", "spawne einen sub-agent")  # i18n-allow: quoted German voice command

    assert len(pipe._spoken) == 1, pipe._spoken
    spoken_text, spoken_lang = pipe._spoken[0]
    from jarvis.speech.pipeline import _BRAIN_TIMEOUT_PHRASE

    assert spoken_text == _BRAIN_TIMEOUT_PHRASE["de"]
    assert spoken_lang == "de"
    # The mark is consumed — the next empty turn must not re-fire the notice.
    assert pipe._playback_aborted_no_first_frame is False


@pytest.mark.asyncio
async def test_empty_turn_without_beheading_stays_silent_with_clarify_off() -> None:
    """Guard for the 2026-06-09 user mandate: a plain empty turn (no ceiling
    abort) with the clarify question off keeps the silent behaviour."""
    pipe = _make_pipe(enabled=False)

    await pipe._handle_silent_brain_turn("de", "irgendein befehl")  # i18n-allow: quoted German voice command

    assert pipe._spoken == []


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
    def __init__(
        self,
        *,
        failed: bool,
        suppressed: bool,
        executed_action: bool = False,
    ) -> None:
        self._last_turn_all_failed = failed
        self._last_turn_suppressed = suppressed
        # Live bug 2026-06-09: the router brain ran a desktop-action tool
        # (computer_use → opened Chrome) but produced no narration. This flag
        # lets the pipeline distinguish a SUCCESSFUL wordless action from a
        # genuinely empty/confused turn.
        self._last_turn_executed_action_tool = executed_action


def _make_silent_pipe(
    *,
    failed: bool = False,
    suppressed: bool = False,
    executed_action: bool = False,
    enabled: bool = True,
) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._brain = _FakeBrain(
        failed=failed, suppressed=suppressed, executed_action=executed_action
    )

    voice_cfg = MagicMock()
    voice_cfg.clarify_incomplete_enabled = enabled
    cfg = MagicMock()
    cfg.voice = voice_cfg
    pipe._config = cfg

    pipe._spoken: list[tuple[str, str | None]] = []
    pipe._state_history: list[TurnTakingState] = []

    async def _fake_speak(
        text: str, language: str | None = None, *, kind: str = "reply"
    ) -> bool:
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
async def test_successful_action_turn_speaks_confirmation_not_clarify() -> None:
    # Live bug 2026-06-09 (data/jarvis_desktop.log 16:27): the router brain
    # (Gemini) emitted a function_call to `computer_use`, the CU loop opened
    # Chrome ([cu] step 1.1 open_app chrome → step 2 done), but Gemini produced
    # NO narration text. `_handle_silent_brain_turn` then spoke the clarifying
    # question "Wie meinst du das genau?" — making a SUCCESSFUL desktop action
    # look like incomprehension ("er checkt das nicht"). A turn that executed a
    # desktop-action tool must speak a positive CONFIRMATION, never the clarify
    # question.
    pipe = _make_silent_pipe(failed=False, suppressed=False, executed_action=True)
    await pipe._handle_silent_brain_turn("de", "kannst du chrome oeffnen")
    assert len(pipe._spoken) == 1, pipe._spoken
    text, lang = pipe._spoken[0]
    assert lang == "de"
    # NOT the clarifying question.
    assert not text.strip().endswith("?"), text
    assert text != _CLARIFY_QUESTION_PHRASE["de"], text
    # It actually spoke (so the user hears the action landed).
    assert TurnTakingState.JARVIS_SPEAKING in pipe._state_history


@pytest.mark.asyncio
async def test_successful_action_turn_confirmation_is_english() -> None:
    pipe = _make_silent_pipe(failed=False, suppressed=False, executed_action=True)
    await pipe._handle_silent_brain_turn("en", "can you open chrome")
    assert len(pipe._spoken) == 1, pipe._spoken
    text, lang = pipe._spoken[0]
    assert lang == "en"
    assert not text.strip().endswith("?"), text
    assert text != _CLARIFY_QUESTION_PHRASE["en"], text


@pytest.mark.asyncio
async def test_action_confirmation_fires_even_when_clarify_disabled() -> None:
    # The success confirmation is an AD-OE6 action ack, not a clarifying
    # question — turning OFF clarifying questions must NOT silence the
    # "your action landed" feedback.
    pipe = _make_silent_pipe(
        failed=False, suppressed=False, executed_action=True, enabled=False
    )
    await pipe._handle_silent_brain_turn("de", "mach mir spotify auf")
    assert len(pipe._spoken) == 1, pipe._spoken
    text, _lang = pipe._spoken[0]
    assert not text.strip().endswith("?"), text


@pytest.mark.asyncio
async def test_empty_turn_without_action_still_clarifies() -> None:
    # Regression guard: a genuinely empty, NON-action turn (no desktop tool ran)
    # MUST still ask the clarifying question — the AD-OE6 behaviour is preserved
    # for the case it was built for.
    pipe = _make_silent_pipe(failed=False, suppressed=False, executed_action=False)
    await pipe._handle_silent_brain_turn("de")
    assert len(pipe._spoken) == 1, pipe._spoken
    text, _lang = pipe._spoken[0]
    assert text.strip().endswith("?"), text


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


class _BareVoiceCfg:
    """A ``voice`` config object that genuinely LACKS ``clarify_incomplete_enabled``.

    Mirrors the committed-HEAD shape, where the field was never committed (it
    lives only in the working tree). The guard reads the attribute via
    ``getattr(cfg, "clarify_incomplete_enabled", <default>)`` — so when the field
    is absent the ``getattr`` DEFAULT decides. The safe default must be "do not
    interrogate" (False), otherwise the clarify question fires for everyone on
    HEAD and after any working-tree reset (live bug 2026-06-09).
    """

    clarify_after_ms = 80  # keep the Path-A timer fast for the test


@pytest.mark.asyncio
async def test_silent_turn_stays_silent_when_field_absent_from_config() -> None:
    # Defense-in-depth durability guard: even if VoiceConfig lacks the field
    # entirely (HEAD shape / config reload edge), an empty brain turn must NOT
    # speak the clarifying question. This pins the getattr fallback at
    # pipeline.py:_handle_silent_brain_turn to False.
    pipe = _make_silent_pipe(failed=False, suppressed=False)
    pipe._config.voice = _BareVoiceCfg()  # field absent → getattr default decides
    await pipe._handle_silent_brain_turn("de", "was geht ab")
    assert pipe._spoken == [], pipe._spoken
    assert TurnTakingState.JARVIS_SPEAKING not in pipe._state_history


@pytest.mark.asyncio
async def test_armed_clarify_stays_silent_when_field_absent_from_config() -> None:
    # Same defense-in-depth guard for the incomplete-fragment timer path
    # (_arm_clarify_question): a config lacking the field must NOT arm the timer.
    pipe = _make_pipe(clarify_after_ms=80)
    pipe._config.voice = _BareVoiceCfg()  # field absent → getattr default decides
    pipe._continuation_buffer.process("erinnere mich daran, dass", language="de")
    pipe._arm_clarify_question("de")
    await asyncio.sleep(0.3)
    assert pipe._clarify_timer_task is None
    assert pipe._spoken == []
    assert TurnTakingState.JARVIS_SPEAKING not in pipe._state_history


@pytest.mark.asyncio
async def test_default_config_empty_turn_does_not_interrogate_user() -> None:
    # User mandate 2026-06-09 ("er fragt mich ständig 'Wie meinst du das?' —
    # ich will, dass er einfach normal antwortet wie damals"): with the SHIPPED
    # default config (no [voice] override in jarvis.toml → the dataclass
    # default governs), an empty brain turn must NOT speak the clarifying
    # question. This ties the behaviour to the real ``VoiceConfig`` default
    # instead of a forced MagicMock, so a future flip back to True is caught.
    default_enabled = VoiceConfig().clarify_incomplete_enabled
    pipe = _make_silent_pipe(
        failed=False, suppressed=False, enabled=default_enabled
    )
    await pipe._handle_silent_brain_turn("de", "kannst du mein spotify öffnen")
    assert pipe._spoken == [], pipe._spoken
    assert TurnTakingState.JARVIS_SPEAKING not in pipe._state_history
