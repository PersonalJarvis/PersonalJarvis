"""The eval harness core: thresholds, the pass/fail gate, and the async
``evaluate`` driver that scores each corpus item and produces a report.

Pure and dependency-light: the metric BACKENDS (WER via ASR, MOS, drift) and the
TTS synth are INJECTED, so this module (and its tests) never import a model or
touch the network — the real backends live in ``metrics.py`` and degrade to
``None`` when their dependency is absent.

Design: docs/superpowers/specs/2026-07-07-tts-quality-curation-design.md §3.6.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from jarvis.speech.tts_eval.corpus import EvalItem


@dataclass(frozen=True)
class SynthResult:
    """What a synth function returns for one utterance: the raw PCM chunks (in
    order), their sample rate, and the measured latency. ``ttfa_ms`` /
    ``rtf`` are ``None`` when timing was not captured."""

    chunks: tuple[bytes, ...]
    sample_rate: int
    ttfa_ms: float | None = None
    rtf: float | None = None


@dataclass(frozen=True)
class MetricResult:
    """The four metric values for one utterance. Any metric whose backend is
    absent is ``None`` and is skipped by the gate (not counted as a failure)."""

    wer: float | None = None
    mos: float | None = None
    drift: float | None = None
    ttfa_ms: float | None = None
    rtf: float | None = None


@dataclass(frozen=True)
class Thresholds:
    """Acceptance thresholds (design §3.6). A model failing any hard gate is not
    ``allowed``. Values copied verbatim from the spec."""

    wer_max: float = 0.06        # round-trip ASR error, per language
    dnsmos_min: float = 3.0      # naturalness floor (DNSMOS OVRL)
    drift_min: float = 0.85      # speaker-embedding cosine across chunks
    ttfa_ms_max: float = 300.0   # time-to-first-audio
    rtf_max: float = 1.0         # real-time factor (< 1.0 = faster than playback)


class WerBackend(Protocol):
    def measure(
        self, pcm: bytes, sample_rate: int, reference_text: str, language: str
    ) -> float | None: ...


class MosBackend(Protocol):
    def measure(self, pcm: bytes, sample_rate: int) -> float | None: ...


class DriftBackend(Protocol):
    def measure(self, chunks: Sequence[bytes], sample_rate: int) -> float | None: ...


@dataclass
class Metrics:
    """The bundle of metric backends. Any may be ``None`` (that metric is
    skipped). Timing (TTFA/RTF) comes from the ``SynthResult``, not a backend."""

    wer: WerBackend | None = None
    mos: MosBackend | None = None
    drift: DriftBackend | None = None


def gate(result: MetricResult, thresholds: Thresholds) -> tuple[bool, list[str]]:
    """Return ``(passed, reasons)``. A ``None`` metric is skipped — it never
    fails the gate (honest: absent measurement ≠ failure). Each failed metric
    yields one human-readable reason string prefixed by the metric name."""
    reasons: list[str] = []
    if result.wer is not None and result.wer > thresholds.wer_max:
        reasons.append(f"wer {result.wer:.3f} > {thresholds.wer_max:.3f}")
    if result.mos is not None and result.mos < thresholds.dnsmos_min:
        reasons.append(f"mos {result.mos:.2f} < {thresholds.dnsmos_min:.2f}")
    if result.drift is not None and result.drift < thresholds.drift_min:
        reasons.append(f"drift {result.drift:.3f} < {thresholds.drift_min:.3f}")
    if result.ttfa_ms is not None and result.ttfa_ms > thresholds.ttfa_ms_max:
        reasons.append(f"ttfa {result.ttfa_ms:.0f}ms > {thresholds.ttfa_ms_max:.0f}ms")
    if result.rtf is not None and result.rtf > thresholds.rtf_max:
        reasons.append(f"rtf {result.rtf:.2f} > {thresholds.rtf_max:.2f}")
    return (not reasons, reasons)


@dataclass(frozen=True)
class ItemReport:
    item: EvalItem
    result: MetricResult
    passed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class EvalReport:
    provider: str
    items: tuple[ItemReport, ...]
    thresholds: Thresholds = field(default_factory=Thresholds)

    @property
    def passed(self) -> bool:
        """Overall verdict: every item must pass (empty report → not passed)."""
        return bool(self.items) and all(r.passed for r in self.items)

    @property
    def pass_rate(self) -> float:
        if not self.items:
            return 0.0
        return sum(1 for r in self.items if r.passed) / len(self.items)

    @property
    def failures(self) -> tuple[ItemReport, ...]:
        return tuple(r for r in self.items if not r.passed)


SynthFn = Callable[[EvalItem], Awaitable[SynthResult]]


async def evaluate(
    synth_fn: SynthFn,
    items: Sequence[EvalItem],
    metrics: Metrics,
    thresholds: Thresholds | None = None,
    provider: str = "",
) -> EvalReport:
    """Synthesize every item through ``synth_fn``, score it with ``metrics``,
    gate it against ``thresholds``, and collect an :class:`EvalReport`.

    ``synth_fn`` is injected (a real one wraps a built TTS provider and times
    TTFA/RTF; a fake returns canned audio) so the harness is provider-agnostic
    and testable without any model or network.
    """
    th = thresholds or Thresholds()
    reports: list[ItemReport] = []
    for item in items:
        synth = await synth_fn(item)
        pcm = b"".join(synth.chunks)
        wer = (
            metrics.wer.measure(pcm, synth.sample_rate, item.text, item.language)
            if metrics.wer is not None
            else None
        )
        mos = metrics.mos.measure(pcm, synth.sample_rate) if metrics.mos is not None else None
        drift = (
            metrics.drift.measure(synth.chunks, synth.sample_rate)
            if metrics.drift is not None
            else None
        )
        result = MetricResult(
            wer=wer, mos=mos, drift=drift, ttfa_ms=synth.ttfa_ms, rtf=synth.rtf
        )
        passed, reasons = gate(result, th)
        reports.append(
            ItemReport(item=item, result=result, passed=passed, reasons=tuple(reasons))
        )
    return EvalReport(provider=provider, items=tuple(reports), thresholds=th)
