"""Unit tests for jarvis.brain.streaming.is_length_truncated.

Pins the truncation guard that keeps a length-capped LLM generation out of
durable memory. Covers every provider's finish-reason dialect plus the
no-reason punctuation fallback.
"""
from __future__ import annotations

import pytest

from jarvis.brain.streaming import is_length_truncated


@pytest.mark.parametrize(
    "reason",
    ["length", "max_tokens", "MAX_TOKENS", "FinishReason.MAX_TOKENS", "max-tokens"],
)
def test_length_markers_are_truncated(reason: str) -> None:
    """Every provider's max-token marker is recognised, case-insensitively."""
    assert is_length_truncated(reason, "a cut off sentence with no period") is True


@pytest.mark.parametrize("reason", ["stop", "end_turn", "tool_use", "stop_sequence", "STOP"])
def test_natural_stop_reasons_are_not_truncated(reason: str) -> None:
    """A real stop reason means the model finished — even mid-word text is kept."""
    assert is_length_truncated(reason, "no trailing period here") is False


def test_no_reason_incomplete_text_is_truncated() -> None:
    """No finish_reason + non-final punctuation => heuristic flags truncation."""
    assert is_length_truncated(None, "The session covered three open threads and") is True


def test_no_reason_complete_text_is_not_truncated() -> None:
    """No finish_reason but sentence-final punctuation => treated as complete."""
    assert is_length_truncated(None, "The session wrapped up cleanly.") is False
    assert is_length_truncated("", '[{"target": "x.md"}]') is False  # JSON array close


def test_empty_text_is_not_truncated() -> None:
    """Empty text is the caller's 'empty' case, not a truncation."""
    assert is_length_truncated(None, "") is False
    assert is_length_truncated("", "   \n ") is False
