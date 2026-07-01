"""Tests for ``jarvis.brain.plausibility.check_plausibility`` (phase 4).

Persona mandate phase 4: before every tool execution with ``risk_tier ∈
{ask, monitor}``, the plausibility guard checks two signals:

  - Whisper confidence of the current turn (``Transcript.confidence``)
  - Wake-time difference (``wake_age_s`` since the last wake trigger)

On low confidence (< threshold) OR a stale wake (> stale_seconds):
  - ``ask`` tier: additional voice confirmation (require_confirmation=True)
  - ``monitor`` tier: log-warning only, no block (require_confirmation=False)

Plausibility is NOT a risk tier — whitelist-downgraded tools (``safe``)
keep running without a plausibility check, otherwise the whitelist would
be pointless.
"""
from __future__ import annotations

import pytest

from jarvis.brain.plausibility import (
    PlausibilityDecision,
    check_plausibility,
)
from jarvis.core.protocols import Transcript


def _t(confidence: float | None) -> Transcript:
    """Helper constructor for test transcripts.

    Accepts ``None`` for failure mode 3 (some STT providers do not report
    a confidence). ``Transcript`` is typed as ``float``, but we cast
    defensively — the plausibility guard must tolerate both a float and
    None.
    """
    return Transcript(
        text="test",
        language="de",
        confidence=confidence,  # type: ignore[arg-type]
        is_partial=False,
    )


# ---------------------------------------------------------------------------
# 5 cases from the mandate
# ---------------------------------------------------------------------------


def test_high_confidence_recent_wake_ask_passes() -> None:
    """Case 1: high confidence + recent wake + ask → proceed=True, no extra confirm."""
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=_t(0.9),
        wake_age_s=5.0,
    )
    assert isinstance(decision, PlausibilityDecision)
    assert decision.proceed is True
    assert decision.require_confirmation is False
    assert decision.reason == "ok"


def test_low_confidence_recent_wake_ask_requires_confirmation() -> None:
    """Case 2: low confidence + recent wake + ask → proceed=True, require_confirmation=True."""
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=_t(0.3),
        wake_age_s=5.0,
    )
    assert decision.proceed is True
    assert decision.require_confirmation is True
    assert decision.reason == "low_confidence_ask"


def test_low_confidence_stale_wake_ask_requires_confirmation() -> None:
    """Case 3: low confidence + stale wake + ask → proceed=True, require_confirmation=True."""
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=_t(0.3),
        wake_age_s=60.0,
    )
    assert decision.proceed is True
    assert decision.require_confirmation is True
    # Low confidence dominates over stale_wake — both trigger, but the
    # confidence diagnosis is more informative for debugging.
    assert decision.reason == "low_confidence_ask"


def test_high_confidence_stale_wake_monitor_log_only() -> None:
    """Case 4: high confidence + stale wake + monitor → proceed=True, log-only."""
    decision = check_plausibility(
        tool_name="open_app",
        risk_tier="monitor",
        transcript=_t(0.9),
        wake_age_s=60.0,
    )
    assert decision.proceed is True
    assert decision.require_confirmation is False
    assert decision.reason == "stale_wake"


def test_low_confidence_safe_tier_passes_through() -> None:
    """Case 5: low confidence + ANY + safe → waved through, plausibility does nothing."""
    decision = check_plausibility(
        tool_name="search_web",
        risk_tier="safe",
        transcript=_t(0.1),
        wake_age_s=120.0,
    )
    assert decision.proceed is True
    assert decision.require_confirmation is False
    assert decision.reason == "ok"


# ---------------------------------------------------------------------------
# Failure-mode-3 test: Transcript.confidence == None treated conservatively as 0.0
# ---------------------------------------------------------------------------


def test_none_confidence_treated_as_zero_for_ask_tier() -> None:
    """``Transcript.confidence`` can be ``None`` (failure-mode 3 mandate).

    Conservative handling: ``None`` → 0.0 → triggers require_confirmation
    at the ``ask`` tier. Prevents an STT provider without confidence
    reporting from silently disabling the plausibility layer.
    """
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=_t(None),
        wake_age_s=5.0,
    )
    assert decision.proceed is True
    assert decision.require_confirmation is True
    assert decision.reason == "low_confidence_ask"


def test_none_confidence_treated_as_zero_for_monitor_tier() -> None:
    """``None`` confidence at monitor → log-only, no block."""
    decision = check_plausibility(
        tool_name="open_app",
        risk_tier="monitor",
        transcript=_t(None),
        wake_age_s=5.0,
    )
    assert decision.proceed is True
    assert decision.require_confirmation is False
    assert decision.reason == "low_confidence_monitor"


def test_no_transcript_treated_as_zero_confidence() -> None:
    """A missing transcript (None) is treated like a None confidence."""
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=None,
        wake_age_s=5.0,
    )
    assert decision.proceed is True
    assert decision.require_confirmation is True
    assert decision.reason == "low_confidence_ask"


# ---------------------------------------------------------------------------
# Configurability tests: threshold/stale-seconds from config
# ---------------------------------------------------------------------------


def test_custom_confidence_threshold() -> None:
    """A threshold of 0.8 (instead of the default 0.5) makes 0.6 low_confidence."""
    from jarvis.core.config import BrainPlausibilityConfig

    cfg = BrainPlausibilityConfig(confidence_threshold=0.8)
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=_t(0.6),
        wake_age_s=5.0,
        config=cfg,
    )
    assert decision.require_confirmation is True
    assert decision.reason == "low_confidence_ask"


def test_custom_stale_wake_seconds() -> None:
    """``stale_wake_seconds=10`` makes 15s stale, which triggers log-only at monitor."""
    from jarvis.core.config import BrainPlausibilityConfig

    cfg = BrainPlausibilityConfig(stale_wake_seconds=10.0)
    decision = check_plausibility(
        tool_name="open_app",
        risk_tier="monitor",
        transcript=_t(0.9),
        wake_age_s=15.0,
        config=cfg,
    )
    assert decision.proceed is True
    assert decision.require_confirmation is False
    assert decision.reason == "stale_wake"


# ---------------------------------------------------------------------------
# Edge case: confidence exactly at the threshold.
# ---------------------------------------------------------------------------


def test_confidence_exactly_at_threshold_passes() -> None:
    """A confidence exactly == threshold counts as sufficient (>=, not >)."""
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=_t(0.5),
        wake_age_s=5.0,
    )
    assert decision.require_confirmation is False
    assert decision.reason == "ok"


def test_wake_age_exactly_at_threshold_passes() -> None:
    """A wake age exactly == 30s counts as not stale (<=, not <)."""
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=_t(0.9),
        wake_age_s=30.0,
    )
    assert decision.require_confirmation is False
    assert decision.reason == "ok"
