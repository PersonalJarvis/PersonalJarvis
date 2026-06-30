"""Validation + launch expansion for the workspace launcher."""
from __future__ import annotations

import pytest

from jarvis.workspace.launcher import (
    LAYOUT_CHOICES,
    build_slots,
    validate_split,
)


@pytest.mark.parametrize("layout", LAYOUT_CHOICES)
def test_valid_split_passes(layout: int) -> None:
    validate_split(layout, {"claude": layout, "codex": 0})


def test_split_must_sum_to_layout() -> None:
    with pytest.raises(ValueError, match="sum to"):
        validate_split(8, {"claude": 5, "codex": 2})  # sums to 7, not 8


def test_layout_must_be_a_known_choice() -> None:
    with pytest.raises(ValueError, match="layout must be"):
        validate_split(7, {"claude": 7})


def test_unknown_agent_rejected() -> None:
    with pytest.raises(ValueError, match="unknown agents"):
        validate_split(2, {"claude": 1, "grok": 1})


def test_negative_count_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        validate_split(2, {"claude": 4, "codex": -2})


def test_zero_total_rejected() -> None:
    with pytest.raises(ValueError):
        validate_split(1, {"claude": 0, "codex": 0})


def test_build_slots_expands_counts_grouped() -> None:
    slots = build_slots({"claude": 5, "codex": 3})
    assert len(slots) == 8
    agents = [s.agent for s in slots]
    assert agents.count("claude") == 5
    assert agents.count("codex") == 3
    # grouped: all claude first, then codex
    assert agents[:5] == ["claude"] * 5
    assert agents[5:] == ["codex"] * 3
    # indices are unique + contiguous, display names resolved
    assert [s.index for s in slots] == list(range(8))
    assert slots[0].display_name == "Claude Code"
    assert slots[5].display_name == "Codex"


def test_build_slots_skips_zero_count_agents() -> None:
    slots = build_slots({"claude": 2, "codex": 0})
    assert all(s.agent == "claude" for s in slots)
    assert len(slots) == 2
