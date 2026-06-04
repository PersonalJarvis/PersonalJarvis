"""Tests fuer choose_critic_model (Tier-Eskalation)."""
from __future__ import annotations

from jarvis.missions.critic.escalation import (
    DEFAULT_FALLBACK_MODEL,
    MODEL_TIER_BY_ITERATION,
    choose_critic_model,
)


# --- Default-Tier-Tabelle ---


def test_iter_0_default_sonnet() -> None:
    assert choose_critic_model(0) == "sonnet"


def test_iter_1_default_sonnet() -> None:
    assert choose_critic_model(1) == "sonnet"


def test_iter_2_default_opus() -> None:
    assert choose_critic_model(2) == "opus"


def test_iter_above_max_falls_back_to_opus() -> None:
    """Defensive: falls je >2 (sollte durch MAX_CRITIC_LOOPS=3 nie passieren)."""
    assert choose_critic_model(99) == DEFAULT_FALLBACK_MODEL


# --- Security-Tag ---


def test_security_tag_forces_opus_at_iter_0() -> None:
    assert choose_critic_model(0, security_tag=True) == "opus"


def test_security_tag_forces_opus_at_iter_1() -> None:
    assert choose_critic_model(1, security_tag=True) == "opus"


# --- Low-Confidence Trigger ---


def test_low_confidence_forces_opus() -> None:
    assert choose_critic_model(0, prior_confidence=0.3) == "opus"


def test_high_confidence_keeps_default() -> None:
    assert choose_critic_model(0, prior_confidence=0.9) == "sonnet"


def test_confidence_at_threshold_keeps_default() -> None:
    """0.4 ist Boundary — `< 0.4` triggert, `== 0.4` nicht."""
    assert choose_critic_model(0, prior_confidence=0.4) == "sonnet"


def test_no_confidence_uses_iteration_table() -> None:
    assert choose_critic_model(0, prior_confidence=None) == "sonnet"


# --- Module-Level Constants ---


def test_tier_table_covers_iterations_0_1_2() -> None:
    assert MODEL_TIER_BY_ITERATION[0] == "sonnet"
    assert MODEL_TIER_BY_ITERATION[1] == "sonnet"
    assert MODEL_TIER_BY_ITERATION[2] == "opus"


def test_fallback_model_is_opus() -> None:
    assert DEFAULT_FALLBACK_MODEL == "opus"
