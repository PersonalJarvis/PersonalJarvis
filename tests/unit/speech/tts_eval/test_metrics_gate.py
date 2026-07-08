"""The acceptance gate: each hard threshold fails independently; a None metric
is skipped (absent measurement is not a failure)."""
from __future__ import annotations

from jarvis.speech.tts_eval.harness import MetricResult, Thresholds, gate

TH = Thresholds()


def test_all_within_thresholds_passes():
    r = MetricResult(wer=0.02, mos=4.0, drift=0.95, ttfa_ms=180, rtf=0.4)
    passed, reasons = gate(r, TH)
    assert passed and reasons == []


def test_high_wer_fails_and_names_wer():
    r = MetricResult(wer=0.09, mos=4.0, drift=0.95, ttfa_ms=180, rtf=0.4)
    passed, reasons = gate(r, TH)
    assert not passed
    assert any(x.startswith("wer") for x in reasons)


def test_low_mos_fails():
    r = MetricResult(mos=2.5)
    passed, reasons = gate(r, TH)
    assert not passed and any(x.startswith("mos") for x in reasons)


def test_voice_drift_below_floor_fails():
    r = MetricResult(drift=0.80)
    passed, reasons = gate(r, TH)
    assert not passed and any(x.startswith("drift") for x in reasons)


def test_slow_ttfa_and_rtf_over_one_fail():
    r = MetricResult(ttfa_ms=450, rtf=1.4)
    passed, reasons = gate(r, TH)
    assert not passed
    assert any(x.startswith("ttfa") for x in reasons)
    assert any(x.startswith("rtf") for x in reasons)


def test_none_metrics_are_skipped_not_failed():
    # No measurements at all → nothing to fail on → passes the gate.
    r = MetricResult()
    passed, reasons = gate(r, TH)
    assert passed and reasons == []


def test_custom_thresholds_are_honored():
    strict = Thresholds(wer_max=0.01)
    r = MetricResult(wer=0.02)
    passed, _ = gate(r, strict)
    assert not passed
