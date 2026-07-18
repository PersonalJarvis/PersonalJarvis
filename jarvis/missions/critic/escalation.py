"""Critic-tier escalation logic.

Sonnet as default; the frontier model (claude-fable-5) on the third
iteration (`iteration == 2`), when `security_tag=True`, or when the
previous critic round returns `requires_escalation()` True (low
confidence / security fail).

Source: Research-Doc §F.6 + ADR-0009 §2. The escalation target used to be
the CLI alias "opus"; user mandate 2026-06-10 forbids any automatic
fallback to claude-opus-*, so escalation now targets the current frontier
model explicitly. The principle (escalate to the strongest model) is
unchanged — only the strongest model moved.
"""
from __future__ import annotations

from typing import Final

# The strongest available model for escalation rounds. An explicit slug, not
# the "opus" CLI alias — the alias would silently resolve to claude-opus-* on
# the claude CLI, which is the exact auto-fallback the user forbade.
# 2026-06-14: switched from "claude-fable-5" to the explicit "claude-opus-4-8"
# model id (NOT the bare "opus" alias) after the maintainer's Claude Max
# subscription lost CLI access to Fable ("Claude Fable 5 is currently
# unavailable" — approved-access-only). Opus-4-8 is the configured deep_model
# and the strongest accessible frontier; using the full id avoids the
# alias-resolution ambiguity the original comment warned about. Per the
# maintainer's 2026-06-14 decision (Fable inaccessible → approve Opus).
FRONTIER_MODEL: Final[str] = "claude-opus-4-8"

MODEL_TIER_BY_ITERATION: Final[dict[int, str]] = {
    0: "sonnet",
    1: "sonnet",
    2: FRONTIER_MODEL,
}

DEFAULT_FALLBACK_MODEL: Final[str] = FRONTIER_MODEL


def choose_critic_model(
    iteration: int,
    *,
    security_tag: bool = False,
    prior_confidence: float | None = None,
) -> str:
    """Returns the Anthropic model slug for the current critic call.

    Escalation trigger priority (first match wins):
    1. `security_tag=True` -> frontier (Research-Doc §F.6: "always the
       strongest tier for security-sensitive output").
    2. `prior_confidence < 0.4` -> frontier (verdict aggregation rule).
    3. `MODEL_TIER_BY_ITERATION[iteration]` (sonnet/sonnet/frontier).
    4. Fallback frontier for iteration > 2 (should never happen due to
       MAX_CRITIC_LOOPS=3, but kept defensively).
    """
    if security_tag:
        return FRONTIER_MODEL
    if prior_confidence is not None and prior_confidence < 0.4:
        return FRONTIER_MODEL
    return MODEL_TIER_BY_ITERATION.get(iteration, DEFAULT_FALLBACK_MODEL)


__all__ = [
    "DEFAULT_FALLBACK_MODEL",
    "FRONTIER_MODEL",
    "MODEL_TIER_BY_ITERATION",
    "choose_critic_model",
]
