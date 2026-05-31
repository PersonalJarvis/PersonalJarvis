"""Critic-tier escalation logic.

Sonnet as default; Opus on the third iteration (`iteration == 2`),
when `security_tag=True`, or when the previous critic round returns
`requires_escalation()` True (low confidence / security fail).

Source: Research-Doc §F.6 + ADR-0009 §2.
"""
from __future__ import annotations

from typing import Final


MODEL_TIER_BY_ITERATION: Final[dict[int, str]] = {
    0: "sonnet",
    1: "sonnet",
    2: "opus",
}

DEFAULT_FALLBACK_MODEL: Final[str] = "opus"


def choose_critic_model(
    iteration: int,
    *,
    security_tag: bool = False,
    prior_confidence: float | None = None,
) -> str:
    """Returns the Anthropic model slug for the current critic call.

    Escalation trigger priority (first match wins):
    1. `security_tag=True` -> Opus (Research-Doc §F.6: "always Opus for
       security-sensitive output").
    2. `prior_confidence < 0.4` -> Opus (verdict aggregation rule).
    3. `MODEL_TIER_BY_ITERATION[iteration]` (sonnet/sonnet/opus).
    4. Fallback Opus for iteration > 2 (should never happen due to
       MAX_CRITIC_LOOPS=3, but kept defensively).
    """
    if security_tag:
        return "opus"
    if prior_confidence is not None and prior_confidence < 0.4:
        return "opus"
    return MODEL_TIER_BY_ITERATION.get(iteration, DEFAULT_FALLBACK_MODEL)


__all__ = [
    "DEFAULT_FALLBACK_MODEL",
    "MODEL_TIER_BY_ITERATION",
    "choose_critic_model",
]
