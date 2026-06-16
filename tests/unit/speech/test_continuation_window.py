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
