"""Tests for choose_critic_model (tier escalation).

User mandate 2026-06-10: the heavy tier never auto-selects the bare Claude
Opus *alias*. 2026-06-14 update: after the maintainer's Claude Max
subscription lost CLI access to Fable, the frontier tier is the explicit
``claude-opus-4-8`` model id (the configured deep_model) — a sanctioned,
accessible pin, NOT the ambiguous bare "opus" alias the guard below forbids.
"""
from __future__ import annotations

from jarvis.missions.critic.escalation import (
    DEFAULT_FALLBACK_MODEL,
    MODEL_TIER_BY_ITERATION,
    choose_critic_model,
)

_FRONTIER = "claude-opus-4-8"


# --- Default tier table ---


def test_iter_0_default_sonnet() -> None:
    assert choose_critic_model(0) == "sonnet"


def test_iter_1_default_sonnet() -> None:
    assert choose_critic_model(1) == "sonnet"


def test_iter_2_escalates_to_frontier_not_opus() -> None:
    assert choose_critic_model(2) == _FRONTIER


def test_iter_above_max_falls_back_to_frontier() -> None:
    """Defensive: if ever >2 (should never happen due to MAX_CRITIC_LOOPS=3)."""
    assert choose_critic_model(99) == DEFAULT_FALLBACK_MODEL


# --- Security tag ---


def test_security_tag_forces_frontier_at_iter_0() -> None:
    assert choose_critic_model(0, security_tag=True) == _FRONTIER


def test_security_tag_forces_frontier_at_iter_1() -> None:
    assert choose_critic_model(1, security_tag=True) == _FRONTIER


# --- Low-confidence trigger ---


def test_low_confidence_forces_frontier() -> None:
    assert choose_critic_model(0, prior_confidence=0.3) == _FRONTIER


def test_high_confidence_keeps_default() -> None:
    assert choose_critic_model(0, prior_confidence=0.9) == "sonnet"


def test_confidence_at_threshold_keeps_default() -> None:
    """0.4 is the boundary — `< 0.4` triggers, `== 0.4` does not."""
    assert choose_critic_model(0, prior_confidence=0.4) == "sonnet"


def test_no_confidence_uses_iteration_table() -> None:
    assert choose_critic_model(0, prior_confidence=None) == "sonnet"


# --- Module-level constants ---


def test_tier_table_covers_iterations_0_1_2() -> None:
    assert MODEL_TIER_BY_ITERATION[0] == "sonnet"
    assert MODEL_TIER_BY_ITERATION[1] == "sonnet"
    assert MODEL_TIER_BY_ITERATION[2] == _FRONTIER


def test_fallback_model_is_frontier() -> None:
    assert DEFAULT_FALLBACK_MODEL == _FRONTIER


def test_no_bare_opus_alias_anywhere() -> None:
    """The CLI alias "opus" must not survive anywhere in the escalation
    surface — it would silently resolve to claude-opus-* on the claude CLI
    (the exact auto-fallback the user forbade)."""
    assert "opus" not in MODEL_TIER_BY_ITERATION.values()
    assert DEFAULT_FALLBACK_MODEL != "opus"
    for it in (0, 1, 2, 99):
        for sec in (False, True):
            assert choose_critic_model(it, security_tag=sec) != "opus"
