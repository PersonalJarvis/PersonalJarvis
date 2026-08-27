"""The hold-key watchdog and the out-of-loop stop (BUG-191).

A HOLD-started dictation is owed a release edge. When that edge is lost between
the OS poller and the pipeline, the recording used to run to its 30-minute cap
with nothing the user pressed able to end it. The watchdog asks the live hotkey
trigger whether the chord is physically down and finishes the recording when it
has been up for longer than any poll jitter — through the very stop event a
real release sets. A backend that cannot see the keyboard answers ``None`` and
the watchdog stands down: it must never invent a release.
"""

from __future__ import annotations

import asyncio

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.speech.pipeline import SpeechPipeline


class _FakeTrigger:
    """Answers ``chord_is_down`` from a script; records what was asked."""

    def __init__(self, answers: list[bool | None]) -> None:
        self._answers = list(answers)
        self.asked: list[str] = []

    def chord_is_down(self, event_name: str) -> bool | None:
        self.asked.append(event_name)
        if len(self._answers) > 1:
            return self._answers.pop(0)
        return self._answers[0]


def _pipeline(trigger: object | None) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_stop_event = asyncio.Event()
    pipe._dictate_key_down = True
    pipe._hotkey_trigger = trigger
    pipe._dictation_started_by = "hold_key"
    return pipe


@pytest.fixture(autouse=True)
def _fast_watchdog(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_DICTATE_HOLD_LOST_RELEASE_S", 0.05)
    monkeypatch.setattr(pipeline_mod, "_DICTATE_HOLD_WATCH_POLL_S", 0.005)


@pytest.mark.asyncio
async def test_a_chord_that_stays_up_finishes_the_recording_like_a_release() -> None:
    trigger = _FakeTrigger([False])
    pipe = _pipeline(trigger)

    await asyncio.wait_for(pipe._watch_dictation_hold_key(), timeout=2.0)

    assert pipe._dictation_stop_event.is_set(), "the lost release must end the recording"
    assert pipe._dictate_key_down is False, "the latch is cleared like a real release"
    assert trigger.asked and set(trigger.asked) == {"dictate"}


@pytest.mark.asyncio
async def test_a_chord_that_is_held_never_stops_the_recording() -> None:
    pipe = _pipeline(_FakeTrigger([True]))
    task = asyncio.create_task(pipe._watch_dictation_hold_key())

    await asyncio.sleep(0.2)  # many times the lost-release window
    assert not task.done()
    assert not pipe._dictation_stop_event.is_set()
    assert pipe._dictate_key_down is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_brief_up_reading_is_forgiven_when_the_key_comes_back() -> None:
    """One 'up' sample inside the window is poll jitter, not a release."""
    pipe = _pipeline(_FakeTrigger([False, True]))
    task = asyncio.create_task(pipe._watch_dictation_hold_key())

    await asyncio.sleep(0.2)
    assert not pipe._dictation_stop_event.is_set()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_backend_that_cannot_see_the_keyboard_stands_down() -> None:
    """``None`` is "I cannot tell" — never "up". The watchdog ends, silently."""
    pipe = _pipeline(_FakeTrigger([None]))

    await asyncio.wait_for(pipe._watch_dictation_hold_key(), timeout=2.0)

    assert not pipe._dictation_stop_event.is_set()
    assert pipe._dictate_key_down is True


@pytest.mark.asyncio
async def test_no_live_trigger_means_no_watchdog() -> None:
    pipe = _pipeline(None)

    await asyncio.wait_for(pipe._watch_dictation_hold_key(), timeout=2.0)

    assert not pipe._dictation_stop_event.is_set()


# ----------------------------------------------------------------------
# request_dictation_stop — the bar's close-X, from another thread
# ----------------------------------------------------------------------


class _RunningTask:
    def done(self) -> bool:
        return False


class _StopCounting(SpeechPipeline):
    def __init__(self) -> None:  # noqa: D107 — bypasses the real ctor on purpose
        self.stopped = 0
        self._dictation_task = None
        self._dictation_handover_task = None
        self._dictation_discard_requested = False
        self._dictate_key_down = True
        self._dictation_started_by = "hold_key"

    def stop_dictation(self) -> bool:  # type: ignore[override]
        self.stopped += 1
        return True


def test_request_dictation_stop_with_nothing_running_says_so() -> None:
    pipe = _StopCounting()
    assert pipe.request_dictation_stop() is False
    assert pipe.stopped == 0


def test_request_dictation_stop_finishes_by_default_and_discards_for_the_x() -> None:
    pipe = _StopCounting()
    pipe._dictation_task = _RunningTask()  # type: ignore[assignment]

    assert pipe.request_dictation_stop() is True
    assert pipe.stopped == 1
    assert pipe._dictation_discard_requested is False
    assert pipe._dictate_key_down is False

    assert pipe.request_dictation_stop(discard=True) is True
    assert pipe.stopped == 2
    assert pipe._dictation_discard_requested is True


@pytest.mark.asyncio
async def test_request_dictation_stop_is_marshalled_onto_the_owner_loop() -> None:
    """Called from a foreign thread, the stop lands on the pipeline's loop."""
    pipe = _StopCounting()
    pipe._dictation_task = _RunningTask()  # type: ignore[assignment]
    loop = asyncio.get_running_loop()
    pipe._runtime_loop = loop

    result = await asyncio.to_thread(pipe.request_dictation_stop, discard=True)
    assert result is True
    # The dispatch was queued, not run on the caller's thread.
    for _ in range(50):
        if pipe.stopped:
            break
        await asyncio.sleep(0.01)
    assert pipe.stopped == 1
    assert pipe._dictation_discard_requested is True


def test_stop_dictation_reaches_a_recording_even_while_a_handover_is_cancelled() -> None:
    """Swallow point B: cancelling a handover used to return before the stop
    event, so a recording alive at the same moment kept running."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_stop_event = asyncio.Event()
    pipe._dictation_task = _RunningTask()  # type: ignore[assignment]

    class _Handover(_RunningTask):
        def __init__(self) -> None:
            self.cancelled = 0

        def cancel(self) -> None:
            self.cancelled += 1

    handover = _Handover()
    pipe._dictation_handover_task = handover  # type: ignore[assignment]

    assert pipe.stop_dictation() is True
    assert handover.cancelled == 1
    assert pipe._dictation_stop_event.is_set()
