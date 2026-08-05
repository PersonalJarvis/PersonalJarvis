"""Deterministic metrics for multilingual STT comparisons."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

_NON_WORD = re.compile(r"[^\w]+", flags=re.UNICODE)
_WER_WORD = re.compile(r"[^\w']+", flags=re.UNICODE)


def _comparison_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return " ".join(part for part in _NON_WORD.split(value) if part)


def _wer_words(text: str) -> list[str]:
    """Preserve the historical WER tokenization used by evaluation reports."""
    return [word for word in _WER_WORD.split((text or "").lower()) if word]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Return deterministic word-level Levenshtein error for STT output."""
    expected = _wer_words(reference)
    actual = _wer_words(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0

    previous = list(range(len(actual) + 1))
    for row, expected_word in enumerate(expected, start=1):
        current = [row]
        for column, actual_word in enumerate(actual, start=1):
            substitution = 0 if expected_word == actual_word else 1
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + substitution,
                )
            )
        previous = current
    return previous[-1] / len(expected)


def switch_error_rate(anchors: Sequence[str], hypothesis: str) -> float | None:
    """Fraction of annotated language-switch anchors missing from a result.

    An anchor should span the boundary (for example, the last two words in one
    language and first two in the next). Exact normalized containment is
    intentionally strict: a corrupted boundary is the failure being measured.
    ``None`` means the corpus item has no annotated switch.
    """
    expected = [_comparison_text(anchor) for anchor in anchors]
    expected = [anchor for anchor in expected if anchor]
    if not expected:
        return None
    actual = _comparison_text(hypothesis)
    missing = sum(1 for anchor in expected if anchor not in actual)
    return missing / len(expected)


def repeatability_error_rate(hypotheses: Sequence[str]) -> float | None:
    """Mean WER between the first result and later repeats of the same audio."""
    values = [str(value or "").strip() for value in hypotheses]
    if len(values) < 2:
        return None
    baseline = values[0]
    return sum(word_error_rate(baseline, value) for value in values[1:]) / (
        len(values) - 1
    )


__all__ = ["repeatability_error_rate", "switch_error_rate", "word_error_rate"]
