"""Unit tests for IterationBudget."""
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


def test_tokens_cap_counts_output_only():
    # The ceiling guards generated (output) tokens. Output over the cap exceeds.
    b = IterationBudget(max_turns=100, max_tokens_total=50)
    b.record_turn(tokens_in=30, tokens_out=60)
    assert b.exceeded()


def test_huge_resent_prompt_does_not_exhaust_the_loop():
    # Regression (live bug 2026-06-01): a single large-context turn (the re-sent
    # prompt is ~60k tokens) must NOT exhaust the loop budget — otherwise the
    # loop aborts a pending tool call before executing it.
    b = IterationBudget(max_turns=15, max_tokens_total=50_000)
    b.record_turn(tokens_in=60_000, tokens_out=40)
    assert not b.exceeded()
    assert b.input_tokens_seen == 60_000


def test_snapshot_structure():
    b = IterationBudget(max_turns=5, max_tokens_total=500)
    b.record_turn(tokens_in=100, tokens_out=50)
    s = b.snapshot()
    assert s["turns_used"] == 1
    assert s["tokens_used"] == 50            # output only
    assert s["turns_remaining"] == 4
    assert s["tokens_remaining"] == 450
    assert s["input_tokens_seen"] == 100
