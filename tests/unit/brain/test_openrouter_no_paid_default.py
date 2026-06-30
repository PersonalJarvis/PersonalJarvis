"""Regression: no OpenRouter code path may default to a PAID Anthropic model.

OpenRouter is a universal gateway — sending ``anthropic/claude-opus-4.8`` bills the
user's OpenRouter key for the single most expensive model in the catalog. When the
user pinned no model, the LAST-RESORT default must be a free/non-Anthropic id (a
wrong free id degrades with a clean 404; a wrong-but-valid paid id bills silently).
When the user DID pin a model, their pick must win over any default. Live forensic
2026-06-29 (~5€ drained on Opus 4.8 + Haiku 4.5). AP-21/AP-22, open-source §3.
"""
from __future__ import annotations

from jarvis.core.config import BrainProviderConfig, JarvisConfig

_FREE = "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_tier_defaults_openrouter_not_paid_anthropic() -> None:
    from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER

    for tier in ("router", "deep"):
        got = TIER_DEFAULTS_BY_PROVIDER[tier]["openrouter"]
        assert "anthropic/claude" not in got, (
            f"TIER_DEFAULTS[{tier}][openrouter] is a paid Anthropic id: {got!r}"
        )


def test_openrouter_plugin_default_model_not_paid_anthropic() -> None:
    from jarvis.plugins.brain.openrouter import DEFAULT_MODEL

    assert "anthropic/claude" not in DEFAULT_MODEL, (
        f"OpenRouterBrain.DEFAULT_MODEL is a paid Anthropic id: {DEFAULT_MODEL!r}"
    )


def test_curator_cheap_fallback_openrouter_not_paid_anthropic() -> None:
    from jarvis.memory.wiki.curator_llm import _CHEAP_MODEL_FALLBACK

    got = _CHEAP_MODEL_FALLBACK["openrouter"]
    assert "anthropic/claude" not in got, (
        f"curator cheap fallback for openrouter is paid Anthropic: {got!r}"
    )


def test_frontier_resolver_chain_honors_openrouter_pick() -> None:
    """resolve_frontier_brain (skill creation, board/bio) must run the user's
    picked OpenRouter model, not the hardcoded deep TIER default."""
    from jarvis.brain.resolver import _resolve_chain

    config = JarvisConfig()
    config.brain.primary = "openrouter"
    config.brain.providers["openrouter"] = BrainProviderConfig(model=_FREE)
    # No legacy worker tier so stage 3 (primary) is the one under test.
    config.brain.worker = None

    chain = _resolve_chain(config)
    openrouter_models = [m for (p, m) in chain if p == "openrouter"]
    assert openrouter_models, "openrouter primary must appear in the frontier chain"
    for m in openrouter_models:
        assert m == _FREE, f"frontier chain ignored the pick, used {m!r}"
        assert "anthropic/claude" not in (m or "")
