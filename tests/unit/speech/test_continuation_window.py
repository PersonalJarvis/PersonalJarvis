"""Unit tests for ContinuationWindow (voice continuation recombine, Unit A)."""
from __future__ import annotations

from jarvis.speech.continuation_window import ContinuationWindow


class FakeClock:
    def __init__(self) -> None:
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns

    def advance_ms(self, ms: int) -> None:
        self.now_ns += ms * 1_000_000


def test_fresh_dispatch_arms_window_in_flight():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    assert not w.is_armed
    w.note_dispatch("ich moechte nach", continued=False)
    assert w.is_armed
    assert w.text == "ich moechte nach"


def test_recombine_while_in_flight_joins_prior_and_new():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    w.note_dispatch("ich moechte nach", continued=False)
    # deadline is None (turn in flight) -> always active
    assert w.try_recombine("Griechenland") == "ich moechte nach Griechenland"


def test_recombine_within_grace_after_idle():
    clk = FakeClock()
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=clk)
    w.note_dispatch("ich moechte nach", continued=False)
    w.mark_idle()  # answer finished -> grace countdown starts
    clk.advance_ms(2000)  # within grace
    assert w.try_recombine("Griechenland") == "ich moechte nach Griechenland"


def test_recombine_after_grace_expires_returns_none_and_clears():
    clk = FakeClock()
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=clk)
    w.note_dispatch("ich moechte nach", continued=False)
    w.mark_idle()
    clk.advance_ms(3000)  # past grace
    assert w.try_recombine("Griechenland") is None
    assert not w.is_armed


def test_chain_cap_stops_merging_after_max_fragments():
    w = ContinuationWindow(grace_ms=9999, max_chain=3, clock=FakeClock())
    w.note_dispatch("a", continued=False)          # chain=1
    assert w.try_recombine("b") == "a b"
    w.note_dispatch("a b", continued=True)          # chain=2
    assert w.try_recombine("c") == "a b c"
    w.note_dispatch("a b c", continued=True)        # chain=3
    # 4th fragment would exceed max_chain=3 -> no merge, window cleared
    assert w.try_recombine("d") is None
    assert not w.is_armed


def test_clear_disarms():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    w.note_dispatch("x", continued=False)
    w.clear()
    assert not w.is_armed
    assert w.try_recombine("y") is None


def test_recombine_when_unarmed_returns_none():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    assert w.try_recombine("anything") is None


# ---------------------------------------------------------------------------
# note_speech_resumed — freeze the grace countdown when the user starts talking
# (live bug 2026-06-18, session 71f2d2de: ~3 s to formulate next fragment >
# 2.5 s grace -> became a fresh turn instead of being recombined).
# ---------------------------------------------------------------------------


def test_speech_resume_within_grace_freezes_deadline():
    """Grace ticking during THINKING silence stops as soon as user starts speaking."""
    clk = FakeClock()
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=clk)
    w.note_dispatch("erster teil", continued=False)
    w.mark_idle()                       # deadline = t0 + 2500 ms
    clk.advance_ms(1000)               # 1 s elapsed — still within grace
    w.note_speech_resumed()            # user started speaking -> freeze deadline
    clk.advance_ms(4000)               # 4 more seconds pass (past original deadline)
    # Window must still recombine: freeze kept it alive
    assert w.try_recombine("zweiter teil") == "erster teil zweiter teil"


def test_speech_resume_after_grace_does_not_revive():
    """A resume AFTER the deadline has already passed must NOT resurrect the window."""
    clk = FakeClock()
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=clk)
    w.note_dispatch("erster teil", continued=False)
    w.mark_idle()
    clk.advance_ms(3000)               # grace already expired
    w.note_speech_resumed()            # too late — must be a no-op
    assert w.try_recombine("zweiter teil") is None


def test_speech_resume_while_in_flight_is_harmless_noop():
    """Calling note_speech_resumed before any mark_idle must not crash or block recombine."""
    clk = FakeClock()
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=clk)
    w.note_dispatch("erster teil", continued=False)
    # No mark_idle call — window is still in flight (deadline None)
    w.note_speech_resumed()            # should be a pure no-op
    assert w.try_recombine("zweiter teil") == "erster teil zweiter teil"


# ---------------------------------------------------------------------------
# is_live — non-mutating mirror of try_recombine's gate. The pipeline uses it to
# tag TranscriptFinal.continues_previous so the recorder merges the coalesced
# fragments into ONE transcript turn (it must NOT consume the window).
# ---------------------------------------------------------------------------


def test_is_live_false_when_unarmed():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    assert w.is_live() is False


def test_is_live_true_while_in_flight():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    w.note_dispatch("erster teil", continued=False)
    assert w.is_live() is True


def test_is_live_tracks_grace_and_never_mutates():
    clk = FakeClock()
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=clk)
    w.note_dispatch("erster teil", continued=False)
    w.mark_idle()
    clk.advance_ms(2000)                 # within grace
    assert w.is_live() is True
    assert w.is_armed                    # not consumed
    assert w.try_recombine("zweiter teil") == "erster teil zweiter teil"
    clk.advance_ms(2000)                 # past the grace deadline
    assert w.is_live() is False          # expired
    assert w.is_armed                    # is_live did NOT clear it (try_recombine does)


def test_is_live_false_at_chain_cap():
    w = ContinuationWindow(grace_ms=9999, max_chain=2, clock=FakeClock())
    w.note_dispatch("a", continued=False)   # chain=1
    assert w.is_live() is True
    w.note_dispatch("a b", continued=True)  # chain=2 == max
    assert w.is_live() is False
