"""Tests fuer ``jarvis.brain.plausibility.check_plausibility`` (Phase 4).

Persona-Mandat Phase 4: Vor jeder Tool-Execution mit ``risk_tier ∈ {ask,
monitor}`` prueft der Plausibility-Guard zwei Signale:

  - Whisper-Confidence des aktuellen Turns (``Transcript.confidence``)
  - Wake-Time-Differenz (``wake_age_s`` seit letztem Wake-Trigger)

Bei niedriger Confidence (< Threshold) ODER stale Wake (> stale_seconds):
  - ``ask``-Tier: zusaetzliche Voice-Bestaetigung (require_confirmation=True)
  - ``monitor``-Tier: nur Log-Warning, kein Block (require_confirmation=False)

Plausibility ist KEIN Risk-Tier — Whitelist-downgraded Tools (``safe``)
laufen weiter ohne Plausibility-Check, sonst ist die Whitelist sinnlos.
"""
from __future__ import annotations

import pytest

from jarvis.brain.plausibility import (
    PlausibilityDecision,
    check_plausibility,
)
from jarvis.core.protocols import Transcript


def _t(confidence: float | None) -> Transcript:
    """Hilfs-Konstruktor fuer Test-Transcripts.

    Akzeptiert ``None`` fuer Failure-Mode-3 (manche STT-Provider liefern
    keine Confidence). ``Transcript`` ist als ``float`` typisiert, aber wir
    castet defensiv — der Plausibility-Guard muss sowohl float als auch
    None vertragen.
    """
    return Transcript(
        text="test",
        language="de",
        confidence=confidence,  # type: ignore[arg-type]
        is_partial=False,
    )


# ---------------------------------------------------------------------------
# 5 Cases aus dem Mandat
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
    # Low confidence dominiert ueber stale_wake — beides triggert, aber
    # die Confidence-Diagnose ist informativer fuer Debugging.
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
    """Case 5: low confidence + ANY + safe → durchwinken, Plausibility tut nichts."""
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
# Failure-Mode-3-Test: Transcript.confidence == None konservativ als 0.0
# ---------------------------------------------------------------------------


def test_none_confidence_treated_as_zero_for_ask_tier() -> None:
    """``Transcript.confidence`` kann ``None`` sein (Failure-Mode 3 Mandat).

    Konservative Behandlung: ``None`` → 0.0 → triggert require_confirmation
    bei ``ask``-Tier. Verhindert, dass ein STT-Provider ohne Confidence-
    Reporting die Plausibility-Schicht stillschweigend deaktiviert.
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
    """``None``-Confidence bei monitor → log-only, kein Block."""
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
    """Fehlender Transcript (None) wird wie None-Confidence behandelt."""
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
# Konfigurierbarkeits-Tests: Threshold/Stale-Seconds aus Config
# ---------------------------------------------------------------------------


def test_custom_confidence_threshold() -> None:
    """Threshold von 0.8 (statt Default 0.5) macht 0.6 zu low_confidence."""
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
    """``stale_wake_seconds=10`` macht 15s zu stale, was bei monitor log-only triggert."""
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
# Edge-Case: Confidence genau an der Schwelle.
# ---------------------------------------------------------------------------


def test_confidence_exactly_at_threshold_passes() -> None:
    """Confidence genau == Threshold gilt als ausreichend (>=, nicht >)."""
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=_t(0.5),
        wake_age_s=5.0,
    )
    assert decision.require_confirmation is False
    assert decision.reason == "ok"


def test_wake_age_exactly_at_threshold_passes() -> None:
    """Wake-Age genau == 30s gilt als nicht-stale (<=, nicht <)."""
    decision = check_plausibility(
        tool_name="run_shell",
        risk_tier="ask",
        transcript=_t(0.9),
        wake_age_s=30.0,
    )
    assert decision.require_confirmation is False
    assert decision.reason == "ok"
