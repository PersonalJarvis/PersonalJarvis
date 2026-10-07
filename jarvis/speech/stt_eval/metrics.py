"""Deterministic metrics for multilingual STT comparisons."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

_NON_WORD = re.compile(r"[^\w]+", flags=re.UNICODE)
_WER_WORD = re.compile(r"[^\w']+", flags=re.UNICODE)


def _comparison_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return " ".join(part for part in _NON_WORD.split(value) if part)


def _wer_words(text: str) -> list[str]:
    """Preserve the historical WER tokenization used by evaluation reports."""
    return [word for word in _WER_WORD.split((text or "").lower()) if word]


@dataclass(frozen=True, slots=True)
class WordErrors:
    """Edit counts; reference_words is the denominator for speech WER."""

    reference_words: int
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0

    @property
    def total(self) -> int:
        return self.substitutions + self.deletions + self.insertions


def word_errors(reference: str, hypothesis: str) -> WordErrors:
    """Count a minimum edit alignment, preferring substitutions on equal cost."""
    expected = _wer_words(reference)
    actual = _wer_words(hypothesis)
    # Each cell stores substitutions, deletions and insertions. Only the
    # previous row is retained, so long recordings do not require a full matrix.
    previous = [(0, 0, column) for column in range(len(actual) + 1)]
    for row, expected_word in enumerate(expected, start=1):
        current = [(0, row, 0)]
        for column, actual_word in enumerate(actual, start=1):
            if expected_word == actual_word:
                current.append(previous[column - 1])
            else:
                sub, delete, insert = previous[column - 1]
                above = previous[column]
                left = current[column - 1]
                current.append(
                    min(
                        (sub + 1, delete, insert),
                        (above[0], above[1] + 1, above[2]),
                        (left[0], left[1], left[2] + 1),
                        key=sum,
                    )
                )
        previous = current
    return WordErrors(len(expected), *previous[-1])


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Return WER, retaining the historical binary score for an empty reference."""
    counts = word_errors(reference, hypothesis)
    if not counts.reference_words:
        return float(bool(counts.total))
    return counts.total / counts.reference_words


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


__all__ = [
    "WordErrors", "repeatability_error_rate", "switch_error_rate",
    "word_error_rate", "word_errors",
]
