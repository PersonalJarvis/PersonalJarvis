"""The evaluate() driver with FAKE synth + metric backends — no model download,
fully deterministic. Proves the harness wires synth → metrics → gate → report."""
from __future__ import annotations

import pytest

from jarvis.speech.tts_eval.corpus import EvalItem
from jarvis.speech.tts_eval.harness import (
    EvalReport,
    Metrics,
    SynthResult,
    Thresholds,
    evaluate,
)

ITEMS = (
    EvalItem("a", "en", "one", ("persona",)),
    EvalItem("b", "de", "zwei", ("persona",)),  # i18n-allow: one-word fixture under test
)


async def _fake_synth(item: EvalItem) -> SynthResult:
    # Two chunks so the drift backend has something to compare; fixed timing.
    return SynthResult(chunks=(b"\x00\x01" * 50, b"\x00\x01" * 50), sample_rate=24_000,
                       ttfa_ms=150.0, rtf=0.5)


class _FixedWer:
    def __init__(self, value: float) -> None:
        self._v = value

    def measure(self, pcm, sample_rate, reference_text, language):
        return self._v


class _FixedMos:
    def __init__(self, value: float) -> None:
        self._v = value

    def measure(self, pcm, sample_rate):
        return self._v


class _FixedDrift:
    def __init__(self, value: float) -> None:
        self._v = value

    def measure(self, chunks, sample_rate):
        return self._v


@pytest.mark.asyncio
async def test_all_pass_report():
    metrics = Metrics(wer=_FixedWer(0.02), mos=_FixedMos(4.0), drift=_FixedDrift(0.95))
    report = await evaluate(_fake_synth, ITEMS, metrics, provider="fake")
    assert isinstance(report, EvalReport)
    assert report.provider == "fake"
    assert report.passed
    assert report.pass_rate == 1.0
    assert report.failures == ()
    # timing came from SynthResult, not a backend
    assert report.items[0].result.ttfa_ms == 150.0


@pytest.mark.asyncio
async def test_high_wer_fails_the_report():
    metrics = Metrics(wer=_FixedWer(0.20), mos=_FixedMos(4.0), drift=_FixedDrift(0.95))
    report = await evaluate(_fake_synth, ITEMS, metrics)
    assert not report.passed
    assert report.pass_rate == 0.0
    assert len(report.failures) == 2
    assert any(r.startswith("wer") for r in report.items[0].reasons)


@pytest.mark.asyncio
async def test_missing_backends_still_gate_on_latency():
    # No WER/MOS/drift backends → only the TTFA/RTF from SynthResult are gated.
    metrics = Metrics()  # all None
    slow_th = Thresholds(ttfa_ms_max=100.0)  # SynthResult ttfa is 150 → fails
    report = await evaluate(_fake_synth, ITEMS, metrics, thresholds=slow_th)
    assert not report.passed
    assert any(r.startswith("ttfa") for r in report.items[0].reasons)
    # the absent metrics did not manufacture a failure of their own
    assert report.items[0].result.wer is None


@pytest.mark.asyncio
async def test_empty_corpus_is_not_passed():
    report = await evaluate(_fake_synth, (), Metrics())
    assert not report.passed
    assert report.pass_rate == 0.0
