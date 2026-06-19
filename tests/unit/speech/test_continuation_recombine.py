"""Pipeline-level continuation recombine + arm (Unit A/C wiring)."""
from __future__ import annotations

from jarvis.speech.continuation_window import ContinuationWindow
from jarvis.speech.pipeline import SpeechPipeline


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


def test_recombine_defers_drop_until_arm():
    brain = FakeBrain()
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    win.note_dispatch("ich moechte nach", continued=False)
    p = _pipeline(window=win, brain=brain)
    text, continued = p._maybe_recombine_continuation("Griechenland")
    assert text == "ich moechte nach Griechenland"
    assert continued is True
    # Window is consumed immediately so a later guard that blocks dispatch
    # cannot leave the old prior armed to re-fire on the next utterance.
    assert not win.is_armed
    # The history drop is DEFERRED — not applied until the turn truly dispatches.
    assert brain.dropped == []
    p._arm_continuation(text, continued=continued)
    assert brain.dropped == ["ich moechte nach"]
    assert win.text == "ich moechte nach Griechenland"  # re-armed on dispatch


def test_recombine_then_blocked_turn_does_not_drop_or_refire():
    """A recombine whose turn is blocked before dispatch (e.g. held by the
    ContinuationBuffer, or eaten by a privacy/skill guard) must not mutate
    history and must not leave the old prior armed to re-merge later."""
    brain = FakeBrain()
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    win.note_dispatch("ich moechte nach", continued=False)
    p = _pipeline(window=win, brain=brain)

    text, continued = p._maybe_recombine_continuation("und dann")
    assert text == "ich moechte nach und dann"
    assert continued is True
    assert not win.is_armed       # consumed -> cannot re-fire
    assert brain.dropped == []    # deferred drop NOT applied (turn never dispatched)

    # _handle_utterance's finally clears the parked drop when no dispatch happened.
    p._continuation_pending_drop = None

    # The next utterance must be independent — no re-merge against the stale prior.
    text2, continued2 = p._maybe_recombine_continuation("etwas Neues")
    assert text2 == "etwas Neues"
    assert continued2 is False
    assert brain.dropped == []


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


def test_two_utterances_coalesce_across_idle_boundary():
    """Turn 1 dispatches; on idle a grace window opens; turn 2 recombines."""
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    p = _pipeline(window=win, brain=FakeBrain())

    # Turn 1: fresh dispatch of the truncated half.
    t1, c1 = p._maybe_recombine_continuation("ich moechte nach")
    assert (t1, c1) == ("ich moechte nach", False)
    p._arm_continuation(t1, continued=c1)
    win.mark_idle()  # turn 1 finished (aborted or spoken)

    # Turn 2: the continuation re-attaches to turn 1's text.
    t2, c2 = p._maybe_recombine_continuation("Griechenland")
    assert t2 == "ich moechte nach Griechenland"
    assert c2 is True
    p._arm_continuation(t2, continued=c2)
    assert win.text == "ich moechte nach Griechenland"


def test_disabled_keeps_utterances_independent():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    p = _pipeline(enabled=False, window=win, brain=FakeBrain())
    t1, c1 = p._maybe_recombine_continuation("ich moechte nach")
    p._arm_continuation(t1, continued=c1)
    win.mark_idle()
    t2, c2 = p._maybe_recombine_continuation("Griechenland")
    assert t2 == "Griechenland"   # NOT merged
    assert c2 is False
    assert not win.is_armed       # window never armed when disabled


def test_on_vad_speech_start_freezes_the_continuation_buffer():
    """The pre-dispatch ContinuationBuffer must get the SAME speech-resume freeze
    as the in-flight ContinuationWindow. Live bug 2026-06-18 (session 241a1984):
    only the window was frozen on speech-start, so a slow-to-finalize fragment
    held by the buffer expired against its 8 s deadline and the turn split into
    an empty Turn 0. _on_vad_speech_start must call note_speech_resumed() on the
    buffer too."""

    class _RecordingBuffer:
        def __init__(self) -> None:
            self.resumed = 0

        def note_speech_resumed(self) -> None:
            self.resumed += 1

    p = SpeechPipeline.__new__(SpeechPipeline)
    buf = _RecordingBuffer()
    p._continuation_buffer = buf
    # No running loop here: _schedule_turn_state catches the RuntimeError and
    # returns, and _continuation_window is absent (getattr → None), so the call
    # exercises ONLY the buffer-freeze path.
    p._on_vad_speech_start()
    assert buf.resumed == 1
