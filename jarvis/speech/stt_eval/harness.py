"""Provider-neutral driver for repeatable STT model comparisons."""

from __future__ import annotations

import statistics
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from jarvis.speech.stt_eval.corpus import STTEvalItem, read_pcm16
from jarvis.speech.stt_eval.metrics import (
    repeatability_error_rate,
    switch_error_rate,
    word_error_rate,
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
class ItemReport:
    id: str
    wer: float
    switch_error_rate: float | None
    repeatability_error_rate: float | None
    median_latency_ms: float
    errors: tuple[str, ...]


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


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    contenders: tuple[ContenderReport, ...]


RecognizeFn = Callable[[bytes], Awaitable[Recognition]]


def _mean_optional(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return statistics.fmean(present) if present else None


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
    """Measure WER, switch loss, latency, cost, and run-to-run variance."""
    repeats = max(1, int(repeats))
    item_reports: list[ItemReport] = []
    all_latencies: list[float] = []
    measured_costs: list[float] = []
    measured_cost_complete = True
    total_audio_s = 0.0
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
    )


__all__ = [
    "ContenderReport",
    "EvaluationReport",
    "ItemReport",
    "Recognition",
    "evaluate_contender",
]
