"""Provider-neutral driver for repeatable STT model comparisons."""

from __future__ import annotations

import math
import statistics
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from jarvis.speech.stt_eval.corpus import STTEvalItem, read_pcm16
from jarvis.speech.stt_eval.metrics import (
    WordErrors,
    repeatability_error_rate,
    switch_error_rate,
    word_error_rate,
    word_errors,
)


@dataclass(frozen=True, slots=True)
class Recognition:
    """One timed provider answer. Transcript text stays in memory only."""

    text: str
    latency_ms: float
    reported_languages: tuple[str, ...] = ()
    cost_usd: float | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class QualitySummary:
    """Counts span all attempts; speech WER excludes empty-reference cases.

    Silence is measured separately so a large quiet corpus cannot dilute speech
    errors. Failed quiet attempts never count as successful silence suppression.
    Latency includes failed attempts, which remain visible in failed_attempts.
    """

    attempts: int
    failed_attempts: int
    speech_errors: WordErrors
    word_weighted_wer: float | None
    silence_evaluated: int
    silence_hallucinations: int
    silence_hallucination_rate: float | None
    p95_latency_ms: float


@dataclass(frozen=True, slots=True)
class ItemReport:
    id: str
    wer: float
    switch_error_rate: float | None
    repeatability_error_rate: float | None
    median_latency_ms: float
    errors: tuple[str, ...]
    tags: tuple[str, ...]
    quality: QualitySummary


@dataclass(frozen=True, slots=True)
class ContenderReport:
    label: str
    provider: str
    model: str
    repeats: int
    wer: float
    switch_error_rate: float | None
    repeatability_error_rate: float | None
    median_latency_ms: float
    measured_cost_usd: float | None
    estimated_cost_usd: float
    items: tuple[ItemReport, ...]
    quality: QualitySummary
    by_tag: dict[str, QualitySummary]


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    contenders: tuple[ContenderReport, ...]


RecognizeFn = Callable[[bytes], Awaitable[Recognition]]


def _mean_optional(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return statistics.fmean(present) if present else None


def _quality(
    scores: Sequence[QualitySummary], latencies: Sequence[float],
) -> QualitySummary:
    counts = WordErrors(
        **{
            field: sum(getattr(score.speech_errors, field) for score in scores)
            for field in ("reference_words", "substitutions", "deletions", "insertions")
        }
    )
    silence_evaluated = sum(score.silence_evaluated for score in scores)
    hallucinations = sum(score.silence_hallucinations for score in scores)
    ordered = sorted(latencies)
    return QualitySummary(
        attempts=sum(score.attempts for score in scores),
        failed_attempts=sum(score.failed_attempts for score in scores),
        speech_errors=counts,
        word_weighted_wer=counts.total / counts.reference_words if counts.reference_words else None,
        silence_evaluated=silence_evaluated,
        silence_hallucinations=hallucinations,
        silence_hallucination_rate=(
            hallucinations / silence_evaluated if silence_evaluated else None
        ),
        # Nearest-rank percentile, including small corpora without extrapolation.
        p95_latency_ms=ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else 0.0,
    )


async def evaluate_contender(
    recognize: RecognizeFn,
    items: Sequence[STTEvalItem],
    *,
    label: str,
    provider: str,
    model: str,
    repeats: int = 3,
    price_per_minute_usd: float = 0.0,
) -> ContenderReport:
    """Measure speech errors, silence, language switches, latency and cost.

    ``wer`` retains its historical per-item mean. ``quality.word_weighted_wer``
    is the speech-only corpus rate; category summaries use manifest tags.
    """
    if not items:
        raise ValueError("The STT evaluation corpus is empty.")
    repeats = max(1, int(repeats))
    item_reports: list[ItemReport] = []
    all_latencies: list[float] = []
    measured_costs: list[float] = []
    measured_cost_complete = True
    total_audio_s = 0.0
    tag_latencies: dict[str, list[float]] = {}
    for item in items:
        pcm, duration_s = read_pcm16(item)
        total_audio_s += duration_s * repeats
        answers = [await recognize(pcm) for _ in range(repeats)]
        texts = [answer.text if not answer.error else "" for answer in answers]
        latencies = [max(0.0, answer.latency_ms) for answer in answers]
        all_latencies.extend(latencies)
        for answer in answers:
            if answer.error or answer.cost_usd is None:
                measured_cost_complete = False
            else:
                measured_costs.append(max(0.0, float(answer.cost_usd)))
        wers = [word_error_rate(item.reference, text) for text in texts]
        switch_rates = [switch_error_rate(item.switch_anchors, text) for text in texts]
        scores: list[QualitySummary] = []
        for text, answer in zip(texts, answers, strict=True):
            counts = word_errors(item.reference, text)
            silence_ok = counts.reference_words == 0 and not answer.error
            hallucination = silence_ok and bool(text.strip())
            scores.append(
                QualitySummary(
                    attempts=1,
                    failed_attempts=int(bool(answer.error)),
                    speech_errors=counts if counts.reference_words else WordErrors(0),
                    word_weighted_wer=(
                        counts.total / counts.reference_words if counts.reference_words else None
                    ),
                    silence_evaluated=int(silence_ok),
                    silence_hallucinations=int(hallucination),
                    silence_hallucination_rate=float(hallucination) if silence_ok else None,
                    p95_latency_ms=max(0.0, answer.latency_ms),
                )
            )
        tags = tuple(dict.fromkeys(item.tags))
        for tag in tags:
            tag_latencies.setdefault(tag, []).extend(latencies)
        item_reports.append(
            ItemReport(
                id=item.id,
                wer=statistics.fmean(wers),
                switch_error_rate=_mean_optional(switch_rates),
                repeatability_error_rate=repeatability_error_rate(texts),
                median_latency_ms=statistics.median(latencies),
                errors=tuple(
                    dict.fromkeys(answer.error for answer in answers if answer.error)
                ),
                tags=tags,
                quality=_quality(scores, latencies),
            )
        )
    return ContenderReport(
        label=label,
        provider=provider,
        model=model,
        repeats=repeats,
        wer=statistics.fmean(item.wer for item in item_reports),
        switch_error_rate=_mean_optional(
            [item.switch_error_rate for item in item_reports]
        ),
        repeatability_error_rate=_mean_optional(
            [item.repeatability_error_rate for item in item_reports]
        ),
        median_latency_ms=statistics.median(all_latencies),
        measured_cost_usd=(
            sum(measured_costs) if measured_cost_complete else None
        ),
        estimated_cost_usd=(total_audio_s / 60.0) * max(0.0, price_per_minute_usd),
        items=tuple(item_reports),
        quality=_quality([item.quality for item in item_reports], all_latencies),
        by_tag={
            tag: _quality(
                [item.quality for item in item_reports if tag in item.tags], latencies,
            )
            for tag, latencies in tag_latencies.items()
        },
    )


__all__ = [
    "ContenderReport",
    "EvaluationReport",
    "ItemReport",
    "Recognition",
    "QualitySummary",
    "evaluate_contender",
]
