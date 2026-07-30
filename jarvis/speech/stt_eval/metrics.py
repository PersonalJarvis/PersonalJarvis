"""Deterministic metrics for multilingual STT comparisons."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from jarvis.speech.tts_eval.metrics import word_error_rate

_NON_WORD = re.compile(r"[^\w]+", flags=re.UNICODE)


def _comparison_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return " ".join(part for part in _NON_WORD.split(value) if part)


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
