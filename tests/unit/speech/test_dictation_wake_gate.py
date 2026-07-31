"""The wake word must stay silent while a dictation is running — and only then.

Maintainer contract: pressing the dictation shortcut is a dictation turn, so
the words being dictated must never trip the wake word. Everywhere else wake
behaves exactly as before.

The gate lives in ``SpeechPipeline._activation_allowed`` because that predicate
is the ONE thing all three wake gates consult (wake-loop entry, the pre-emit
activation point, and the state-loop backstop), plus push-to-talk and
``request_voice_session``.

Why the shape of the gate matters more than the gate itself: a leaked "a
dictation is running" flag is BUG-037 — permanently deaf with no visible cause
and only an app restart to fix it. So the condition is derived from the live
task (``task.done()`` clears it even on a crash) AND bounded by a watchdog
deadline (a task that hangs forever still cannot deafen wake forever). These
tests pin both halves, including a task killed mid-flight.

This is a STATE gate, not the transcript-CONTENT gate AP-27 forbids: it never
looks at audio or at any recognized text.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import WakeCandidateDetected
from jarvis.speech.pipeline import PipelineState, SpeechPipeline


class _StubSTT:
    async def transcribe_pcm(self, pcm: bytes):  # pragma: no cover - never called
        raise AssertionError("no transcription in this unit test")


def _gate_pipeline() -> SpeechPipeline:
    """A pipeline reduced to exactly what the activation gate reads."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._muted = False
    pipe._state = PipelineState.IDLE
    pipe._dictation_task = None
    pipe._dictation_wake_block_until = 0.0
    return pipe


async def _blocking_task() -> None:
    await asyncio.Event().wait()


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------


def test_wake_is_allowed_when_no_dictation_runs() -> None:
    pipe = _gate_pipeline()
    assert pipe._dictation_blocks_activation() is False
    assert pipe._activation_allowed() is True
    assert pipe._activation_block_reason() == ""


@pytest.mark.asyncio
async def test_a_running_dictation_closes_the_activation_gate() -> None:
    pipe = _gate_pipeline()
    pipe._dictation_task = asyncio.create_task(_blocking_task())
    pipe._dictation_wake_block_until = time.time() + 300.0
    try:
        assert pipe._dictation_blocks_activation() is True
        assert pipe._activation_allowed() is False
        assert pipe._activation_block_reason() == "a dictation is running"
    finally:
        pipe._dictation_task.cancel()


@pytest.mark.asyncio
async def test_wake_returns_the_moment_the_dictation_task_finishes() -> None:
    pipe = _gate_pipeline()

    done_now = asyncio.Event()

    async def _short() -> None:
        await done_now.wait()

    pipe._dictation_task = asyncio.create_task(_short())
    pipe._dictation_wake_block_until = time.time() + 300.0
    await asyncio.sleep(0)
    assert pipe._activation_allowed() is False

    done_now.set()
    await pipe._dictation_task

    assert pipe._activation_allowed() is True
    assert pipe._activation_block_reason() == ""


@pytest.mark.asyncio
async def test_a_dictation_task_killed_mid_flight_leaves_wake_working() -> None:
    """The BUG-037 case: the dictation dies, nobody clears anything.

    Nothing runs a ``finally`` here — the task is cancelled outright — so this
    is exactly the situation in which a bare bool would stay True and Jarvis
    would go permanently deaf.
    """
    pipe = _gate_pipeline()
    pipe._dictation_task = asyncio.create_task(_blocking_task())
    pipe._dictation_wake_block_until = time.time() + 300.0
    await asyncio.sleep(0)
    assert pipe._activation_allowed() is False

    pipe._dictation_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pipe._dictation_task

    assert pipe._dictation_blocks_activation() is False
    assert pipe._activation_allowed() is True


@pytest.mark.asyncio
async def test_a_crashed_dictation_task_leaves_wake_working() -> None:
    pipe = _gate_pipeline()

    async def _boom() -> None:
        raise RuntimeError("dictation exploded")

    pipe._dictation_task = asyncio.create_task(_boom())
    pipe._dictation_wake_block_until = time.time() + 300.0
    with pytest.raises(RuntimeError):
        await pipe._dictation_task

    assert pipe._activation_allowed() is True


@pytest.mark.asyncio
async def test_the_block_expires_even_if_the_dictation_task_never_ends() -> None:
    """The watchdog half: a hung task must not deafen wake beyond the deadline."""
    pipe = _gate_pipeline()
    pipe._dictation_task = asyncio.create_task(_blocking_task())
    try:
        pipe._dictation_wake_block_until = time.time() + 5.0
        assert pipe._activation_allowed() is False

        # The task is still very much alive; only the deadline has passed.
        pipe._dictation_wake_block_until = time.time() - 0.001
        assert pipe._dictation_task.done() is False
        assert pipe._dictation_blocks_activation() is False
        assert pipe._activation_allowed() is True
    finally:
        pipe._dictation_task.cancel()


def test_a_pipeline_that_never_dictated_is_never_blocked() -> None:
    """Both defaults fail OPEN — a missing attribute can never cause deafness."""
    bare = SpeechPipeline.__new__(SpeechPipeline)
    assert bare._dictation_blocks_activation() is False


@pytest.mark.asyncio
async def test_mute_still_wins_and_is_reported_first() -> None:
    """Mute is checked before dictation, so a muted user is told the truth."""
    pipe = _gate_pipeline()
    pipe._muted = True
    pipe._dictation_task = asyncio.create_task(_blocking_task())
    pipe._dictation_wake_block_until = time.time() + 300.0
    try:
        assert pipe._activation_allowed() is False
        assert pipe._activation_block_reason() == "voice is muted"
    finally:
        pipe._dictation_task.cancel()


def test_a_closed_capture_gate_is_named_without_guessing() -> None:
    pipe = _gate_pipeline()
    pipe._activation_gate = lambda: False
    assert pipe._activation_allowed() is False
    assert "microphone capture is not permitted" in pipe._activation_block_reason()


# --------------------------------------------------------------------------
# Every gated site names the REAL reason
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_state_loop_names_the_dictation_instead_of_guessing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hardcoded reason here once sent a live diagnosis after the wrong cause.

    The state loop is the backstop that drops an activation edge; it closes for
    a mute and for a running dictation too, so "the desktop window is not
    available" is a guess that can be flatly wrong.
    """
    pipe = _gate_pipeline()
    pipe._call_event = asyncio.Event()
    pipe._ptt_mode = False
    pipe._dictation_task = asyncio.create_task(_blocking_task())
    pipe._dictation_wake_block_until = time.time() + 300.0

    aborted = asyncio.Event()

    async def _abort() -> None:
        aborted.set()

    pipe._abort_pending_wake_handoff = _abort  # type: ignore[method-assign]

    loop_task = asyncio.create_task(pipe._state_loop())
    try:
        with caplog.at_level("INFO", logger="jarvis.speech.pipeline"):
            pipe._call_event.set()
            await asyncio.wait_for(aborted.wait(), timeout=1.0)
        messages = [r.getMessage() for r in caplog.records]
        assert any("a dictation is running" in m for m in messages), messages
        assert not any("desktop activation is unavailable" in m for m in messages)
    finally:
        loop_task.cancel()
        pipe._dictation_task.cancel()
        for task in (loop_task, pipe._dictation_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


# --------------------------------------------------------------------------
# The bar must not flicker out of its dictation look
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_optimistic_wake_candidate_is_suppressed_during_dictation() -> None:
    """A dictation leaves ``_state`` at IDLE, so this needs its own clause."""
    pipe = _gate_pipeline()
    assert pipe._should_show_optimistic_candidate() is True

    pipe._dictation_task = asyncio.create_task(_blocking_task())
    pipe._dictation_wake_block_until = time.time() + 300.0
    try:
        assert pipe._should_show_optimistic_candidate() is False
    finally:
        pipe._dictation_task.cancel()


@pytest.mark.asyncio
async def test_an_early_vosk_candidate_is_suppressed_but_a_retract_still_passes() -> None:
    bus = EventBus()
    seen: list[WakeCandidateDetected] = []

    async def _collect(event: WakeCandidateDetected) -> None:
        seen.append(event)

    bus.subscribe(WakeCandidateDetected, _collect)

    pipe = _gate_pipeline()
    pipe._bus = bus
    pipe._begin_wake_preroll = lambda: None  # type: ignore[method-assign]
    pipe._discard_wake_preroll = lambda: None  # type: ignore[method-assign]
    pipe._dictation_task = asyncio.create_task(_blocking_task())
    pipe._dictation_wake_block_until = time.time() + 300.0
    try:
        await pipe._vosk_early_candidate_listener(True)
        assert seen == [], "a dictation owns the bar; no wake look may flash"

        # A retract must ALWAYS pass through, or a bar shown before the
        # dictation started could stay stuck in the wake look.
        await pipe._vosk_early_candidate_listener(False)
        assert len(seen) == 1
        assert seen[0].active is False
    finally:
        pipe._dictation_task.cancel()
