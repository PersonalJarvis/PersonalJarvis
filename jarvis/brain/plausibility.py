"""Plausibility guard before tool execution (Persona Mandate Phase 4).

Layer ABOVE the risk tier — checks whether the voice pipeline is currently
in a "trustworthy" state:

1. **Confidence**: Did the STT clearly understand the utterance? Whisper
   confidence < ``confidence_threshold`` (default 0.5) -> uncertain.
2. **Wake-Age**: When was Jarvis last woken? > ``stale_wake_seconds``
   (default 30) -> stale, likely background audio rather than user intent.

Reaction:

- ``risk_tier == "ask"``: On uncertainty, sets ``require_confirmation=True``.
  The ``ToolExecutor`` then triggers an additional voice confirmation on top
  of the normal approval workflow ("Should I go ahead and do {action}?").
- ``risk_tier == "monitor"``: log-only, no block. Telemetry sees the
  plausibility hit, but the tool runs.
- ``risk_tier == "safe"``: Plausibility does nothing. Whitelist-downgraded
  tools must NOT be blocked by plausibility — otherwise the whitelist is
  pointless.

Failure mode 3 (mandate): Some STT providers (e.g. Grok-Voice-STT) do not
return a confidence score. ``Transcript.confidence == None`` is treated
conservatively as ``0.0`` -> triggers the confidence threshold and causes
``require_confirmation`` for the ``ask`` tier.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jarvis.core.protocols import RiskTier, Transcript

if TYPE_CHECKING:
    from jarvis.core.config import BrainPlausibilityConfig


# Default thresholds — overridden when ``config`` is provided.
_DEFAULT_CONFIDENCE_THRESHOLD = 0.5
_DEFAULT_STALE_WAKE_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class PlausibilityDecision:
    """Result of ``check_plausibility``.

    Attributes:
        proceed: ``True`` if tool execution may continue.
            Currently always ``True`` (plausibility does not hard-block —
            it only escalates via ``require_confirmation``).
        require_confirmation: ``True`` if the ``ToolExecutor`` should request
            an additional voice confirmation. Only applies when
            ``risk_tier == "ask"`` and confidence/staleness is low.
        reason: Diagnostic string for telemetry/logging:
            ``"ok"``, ``"low_confidence_ask"``, ``"low_confidence_monitor"``,
            ``"stale_wake"``.
    """
    proceed: bool
    require_confirmation: bool
    reason: str


def check_plausibility(
    *,
    tool_name: str,
    risk_tier: RiskTier,
    transcript: Transcript | None,
    wake_age_s: float | None,
    config: "BrainPlausibilityConfig | None" = None,
) -> PlausibilityDecision:
    """Checks confidence and wake-age against the plausibility thresholds.

    Args:
        tool_name: Tool name (used for logging/reason only — not for logic).
        risk_tier: Tier of the tool (``safe``/``monitor``/``ask``/``block``).
        transcript: Optional. When ``None`` or ``confidence is None``,
            ``confidence = 0.0`` is assumed (conservative).
        wake_age_s: Seconds since the last wake trigger. ``None`` -> 0.0.
        config: Optional. When ``None``, hardcoded defaults (0.5 / 30s)
            are used — for tests/tools without config access.

    Returns:
        ``PlausibilityDecision`` — see class docstring.
    """
    threshold = (
        config.confidence_threshold if config is not None
        else _DEFAULT_CONFIDENCE_THRESHOLD
    )
    stale_seconds = (
        config.stale_wake_seconds if config is not None
        else _DEFAULT_STALE_WAKE_SECONDS
    )

    # safe tier (whitelist downgrade target): plausibility does nothing.
    if risk_tier == "safe":
        return PlausibilityDecision(
            proceed=True, require_confirmation=False, reason="ok",
        )

    # block tier should already have been rejected by ``RiskTierEvaluator``
    # before reaching here. Defensive fallback: do not execute.
    if risk_tier == "block":
        return PlausibilityDecision(
            proceed=False, require_confirmation=False, reason="blocked_tier",
        )

    # Determine confidence — None treated conservatively as 0.0.
    raw_conf = (
        transcript.confidence if transcript is not None else None
    )
    confidence = float(raw_conf) if raw_conf is not None else 0.0

    # Wake age — None treated as 0.0 (just woken).
    age = float(wake_age_s) if wake_age_s is not None else 0.0

    low_conf = confidence < threshold
    stale_wake = age > stale_seconds

    # Everything in the green zone -> pass through.
    if not low_conf and not stale_wake:
        return PlausibilityDecision(
            proceed=True, require_confirmation=False, reason="ok",
        )

    # ask tier: additional confirmation. low_conf takes priority for the
    # reason string (more informative for debugging).
    if risk_tier == "ask":
        reason = "low_confidence_ask" if low_conf else "stale_wake"
        return PlausibilityDecision(
            proceed=True, require_confirmation=True, reason=reason,
        )

    # monitor tier: log-only, no block, no extra confirmation.
    # ``require_confirmation`` stays False — the caller logs ``reason``
    # and continues without any additional stop.
    reason = "low_confidence_monitor" if low_conf else "stale_wake"
    return PlausibilityDecision(
        proceed=True, require_confirmation=False, reason=reason,
    )


__all__ = ["PlausibilityDecision", "check_plausibility"]
