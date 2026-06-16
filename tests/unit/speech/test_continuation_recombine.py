"""Pipeline-level continuation recombine + arm (Unit A/C wiring)."""
from __future__ import annotations

from jarvis.speech.pipeline import SpeechPipeline
from jarvis.speech.continuation_window import ContinuationWindow


class FakeBrain:
    def __init__(self) -> None:
        self.dropped: list[str] = []

    def drop_last_turn(self, expected_user_text: str) -> bool:
        self.dropped.append(expected_user_text)
        return True


def _pipeline(*, enabled=True, window=None, brain=None):
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._continuation_interrupt_enabled = enabled
    p._continuation_window = window or ContinuationWindow(grace_ms=2500, max_chain=3)
    p._brain = brain
    p._continuation_dispatched_this_turn = False
    return p


def test_recombine_joins_and_requests_drop():
    brain = FakeBrain()
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    win.note_dispatch("ich moechte nach", continued=False)
    p = _pipeline(window=win, brain=brain)
    text, continued = p._maybe_recombine_continuation("Griechenland")
    assert text == "ich moechte nach Griechenland"
    assert continued is True
    assert brain.dropped == ["ich moechte nach"]


def test_recombine_noop_when_disabled():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    win.note_dispatch("ich moechte nach", continued=False)
    p = _pipeline(enabled=False, window=win, brain=FakeBrain())
    text, continued = p._maybe_recombine_continuation("Griechenland")
    assert text == "Griechenland"
    assert continued is False


def test_recombine_noop_when_unarmed():
    p = _pipeline(window=ContinuationWindow(grace_ms=2500, max_chain=3), brain=FakeBrain())
    text, continued = p._maybe_recombine_continuation("hello")
    assert text == "hello"
    assert continued is False


def test_cancel_text_clears_window_and_does_not_merge():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    win.note_dispatch("ich moechte nach", continued=False)
    p = _pipeline(window=win, brain=FakeBrain())
    text, continued = p._maybe_recombine_continuation("vergiss das")
    assert text == "vergiss das"
    assert continued is False
    assert not win.is_armed


def test_arm_continuation_records_dispatch_and_marks_flag():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    p = _pipeline(window=win)
    p._arm_continuation("ich moechte nach", continued=False)
    assert win.is_armed
    assert win.text == "ich moechte nach"
    assert p._continuation_dispatched_this_turn is True


def test_arm_continuation_noop_when_disabled():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    p = _pipeline(enabled=False, window=win)
    p._arm_continuation("x", continued=False)
    assert not win.is_armed
    assert p._continuation_dispatched_this_turn is False
