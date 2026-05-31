"""Unit-Tests für IterationBudget."""
from __future__ import annotations

from jarvis.brain import IterationBudget


def test_not_exceeded_initially():
    b = IterationBudget(max_turns=3, max_tokens_total=1000)
    assert not b.exceeded()
    assert b.remaining_turns() == 3
    assert b.remaining_tokens() == 1000


def test_turns_counted():
    b = IterationBudget(max_turns=2)
    b.record_turn(tokens_in=10, tokens_out=20)
    assert not b.exceeded()
    b.record_turn(tokens_in=10, tokens_out=20)
    assert b.exceeded()


def test_tokens_cap():
    b = IterationBudget(max_turns=100, max_tokens_total=50)
    b.record_turn(tokens_in=30, tokens_out=30)
    assert b.exceeded()


def test_snapshot_structure():
    b = IterationBudget(max_turns=5, max_tokens_total=500)
    b.record_turn(tokens_in=100, tokens_out=50)
    s = b.snapshot()
    assert s["turns_used"] == 1
    assert s["tokens_used"] == 150
    assert s["turns_remaining"] == 4
    assert s["tokens_remaining"] == 350
