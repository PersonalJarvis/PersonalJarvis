"""Cost pricing table for brain providers (USD per 1M tokens).

As of 2026-04. Sources:

- Anthropic — https://www.anthropic.com/pricing
  (Claude Opus 4.x: $15 in / $75 out, Sonnet 4.x: $3 / $15, Haiku 4.5: $0.80 / $4)
- Google — https://ai.google.dev/pricing
  (Gemini 2.5 Pro: $1.25 / $10, Gemini 2.5 Flash: $0.075 / $0.30)
- OpenAI — https://openai.com/api/pricing
  (GPT-4o: $2.50 / $10, GPT-4o-mini: $0.15 / $0.60)
- xAI / Grok — https://console.x.ai/pricing
  (Grok-3: $5 / $15, Grok-4.1-fast: $0.40 / $1.60,
   Grok-4.3: $1.25 / $2.50 — doubles above 200k input)
- DeepSeek — https://platform.deepseek.com/api-docs/pricing
  (deepseek-chat: $0.27 / $1.10, deepseek-reasoner: $0.55 / $2.19)

If a model is missing here, ``calculate_cost_usd`` returns 0.0 — no crash,
but also no cost tracking. Logging at the call site is mandatory so that
missing entries become visible rather than silently turning into a
"free" banner.

Aliases are NOT mapped here — callers must pass the canonical model name
(e.g. ``claude-opus-4-7-20251022`` instead of ``opus``). PROVIDER_ALIASES
in ``manager.py`` is for provider names, not model IDs.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Mapping: model ID -> (input_per_mtok, output_per_mtok) in USD.
# As of 2026-04-29 — frontier model update (user mandate: frontier only).
# Older snapshots are kept so that cost tracking for historical sessions
# continues to return values; they are NOT in the tier defaults —
# the frontier resolver picks only the most recent variant.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # ── Anthropic Claude (Frontier: Fable 5, Sonnet 4.6, Haiku 4.5) ──
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (15.0, 75.0),
    "claude-opus-4-7-20251022": (15.0, 75.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-5": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "claude-haiku-4-5": (0.80, 4.0),
    # ── Google Gemini (Frontier: 3.1-pro-preview, 3-flash) ──────────
    "gemini-3.1-pro-preview": (2.0, 12.0),
    "gemini-3-pro-preview": (1.50, 10.0),
    "gemini-3-flash": (0.10, 0.40),
    "gemini-3-flash-preview": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.05, 0.20),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-2.5-flash-lite": (0.075, 0.30),
    "gemini-3.1-flash-tts-preview": (0.075, 0.30),  # TTS, same rate
    # ── OpenAI (Frontier: GPT-5.5 + 5.5-pro, released 2026-04-23) ──
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.5-pro": (15.0, 60.0),
    "gpt-5": (3.0, 15.0),
    "gpt-5-mini": (0.30, 1.20),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.0, 30.0),
    # ── xAI Grok (frontier since 2026-04-30: 4.3 — faster AND
    # smarter than 4.20; older entries kept for historical
    # cost-tracking analysis) ────────────────────────────────────────
    "grok-4.3": (1.25, 2.50),
    "grok-4.20": (2.0, 6.0),
    "grok-4-0709": (5.0, 15.0),
    "grok-4": (5.0, 15.0),
    "grok-4.1-fast": (0.40, 1.60),
    "grok-3": (5.0, 15.0),
    # ── DeepSeek ────────────────────────────────────────────────────
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # ── OpenRouter (proxied Anthropic models, same price) ──────────────
    "anthropic/claude-haiku-4.5": (0.80, 4.0),
    "anthropic/claude-opus-4.8": (15.0, 75.0),
    "anthropic/claude-opus-4.7": (15.0, 75.0),
    "anthropic/claude-sonnet-4.6": (3.0, 15.0),
    # ── Mistral (same ordering) ──────────────────────────────────────
    "mistral-small-3.1": (0.20, 0.60),
    "mistral-large-3": (3.0, 9.0),
}


def calculate_cost_usd(
    model: str | None,
    tokens_in: int,
    tokens_out: int,
) -> float:
    """Return the cost in USD for a single brain call.

    Args:
        model: Canonical model ID (e.g. ``"claude-opus-4-7-20251022"``).
            ``None`` or unknown → 0.0.
        tokens_in: Prompt tokens.
        tokens_out: Completion tokens.

    Returns:
        Cost in USD. 0.0 if the model is not in the pricing table
        or if the token counts are non-positive.
    """
    if not model or tokens_in <= 0 and tokens_out <= 0:
        return 0.0
    rates = PRICING_USD_PER_MTOK.get(model)
    if rates is None:
        log.debug("Cost pricing missing for model %r — returning 0.0", model)
        return 0.0
    in_rate, out_rate = rates
    return (max(0, tokens_in) * in_rate + max(0, tokens_out) * out_rate) / 1_000_000


__all__ = ["PRICING_USD_PER_MTOK", "calculate_cost_usd"]
