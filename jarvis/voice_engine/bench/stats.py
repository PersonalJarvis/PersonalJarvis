"""Small, dependency-free statistics for bench reports."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def percentile(values: Iterable[float], q: float) -> float | None:
    data = sorted(v for v in values if v is not None and not math.isnan(v))
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    rank = (len(data) - 1) * q / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return data[low]
    return data[low] + (data[high] - data[low]) * (rank - low)


def summary(values: Iterable[float]) -> dict[str, float | int | None]:
    data = [v for v in values if v is not None]
    return {
        "n": len(data),
        "p50": percentile(data, 50),
        "p95": percentile(data, 95),
        "max": max(data) if data else None,
    }


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split())


def _edit_distance(a: Sequence[str], b: Sequence[str]) -> int:
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            substitution = previous[j - 1] + (left != right)
            current.append(min(previous[j] + 1, current[j - 1] + 1, substitution))
        previous = current
    return previous[-1]


def error_rates(reference: str, hypothesis: str) -> tuple[float, float]:
    """(character error rate, word error rate) after normalisation."""
    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    ref_chars = ref.replace(" ", "")
    hyp_chars = hyp.replace(" ", "")
    cer = _edit_distance(ref_chars, hyp_chars) / max(1, len(ref_chars))
    wer = _edit_distance(ref.split(), hyp.split()) / max(1, len(ref.split()))
    return cer, wer
