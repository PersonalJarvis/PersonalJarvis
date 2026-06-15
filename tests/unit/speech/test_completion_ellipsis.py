"""Trailing-ellipsis trail-off detection (live bug 2026-06-14).

A cut-off voice fragment — "Kannst du mir sagen, was genau..." — was dispatched
to the brain as a COMPLETE turn (its last word "genau" matches no open marker
and it ends in "...", not a comma). Handed a contentless fragment, the brain
hallucinated a screen description. The speech recognizer emits a trailing
ellipsis ("..." or the unicode "…") precisely when the speaker audibly broke
off, so that ellipsis is a high-precision "incomplete" signal — the structural
twin of the existing trailing-comma rule.

Cross-platform note: pure stdlib text logic. The ellipsis can arrive as three
ASCII dots or as U+2026 ("…"); both must fire. A SINGLE trailing period is a
normal sentence terminator and must NEVER fire.
"""
from __future__ import annotations

import pytest

from jarvis.speech.completion import (
    REASON_TRAILING_ELLIPSIS,
    is_incomplete,
)

# Fragments that trail off → must be classified incomplete.
_TRAILED_OFF = [
    "Kannst du mir sagen, was genau...",          # the exact live failure
    "Kannst du mir sagen, was genau…",            # unicode ellipsis (U+2026)
    "Ich wollte nur fragen ob du..",               # two dots (STT artifact)
    "Can you tell me what exactly...",             # English trail-off
    "I was just wondering something…",             # no marker collision
]

# Complete utterances that happen to end in a single period → must NOT fire.
_COMPLETE = [
    "Das ist alles.",
    "Thanks, that is all.",
    "Marie Curie won two Nobel Prizes.",
]


@pytest.mark.parametrize("text", _TRAILED_OFF)
def test_trailing_ellipsis_is_incomplete(text: str) -> None:
    verdict = is_incomplete(text)
    assert verdict is not None, text
    assert verdict.reason == REASON_TRAILING_ELLIPSIS, (text, verdict)


@pytest.mark.parametrize("text", _COMPLETE)
def test_single_period_is_complete(text: str) -> None:
    # A normal sentence terminator (exactly one ".") is NOT incomplete — these
    # plain statements carry no open marker, comma, or ellipsis.
    assert is_incomplete(text) is None, text


def test_conjunction_before_ellipsis_keeps_conjunction_reason() -> None:
    # A more specific tail (open conjunction) wins over the generic ellipsis.
    from jarvis.speech.completion import REASON_CONJUNCTION

    verdict = is_incomplete("ich gehe los und...")
    assert verdict is not None
    assert verdict.reason == REASON_CONJUNCTION


def test_single_word_plus_ellipsis_is_noise() -> None:
    # Below the 2-token floor — a lone word + "..." is filler/noise, not a held
    # fragment.
    assert is_incomplete("Also...") is None
