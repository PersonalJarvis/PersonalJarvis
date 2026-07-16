"""Unit tests for TIER_DEFAULTS_BY_PROVIDER, _resolve_tier_model, and get_tier_default_model."""
from __future__ import annotations

import pytest

from jarvis.brain.manager import (
    TIER_DEFAULTS_BY_PROVIDER,
    _resolve_tier_model,
    get_tier_default_model,
)


class TestTierDefaultsCatalog:
    def test_all_known_providers_have_router_defaults(self):
        required = {"claude-api", "gemini", "openai"}
        assert required <= set(TIER_DEFAULTS_BY_PROVIDER["router"])

    def test_all_known_providers_have_deep_defaults(self):
        required = {"claude-api", "gemini", "openai"}
        assert required <= set(TIER_DEFAULTS_BY_PROVIDER["deep"])

    def test_router_models_look_fast(self):
        # 2026-04-29 update: GPT-5.5 has no -mini variant released; OpenAI
        # itself says "GPT-5.5 has per-token latency like GPT-5.4". The fast-tier
        # heuristic therefore also accepts frontier main models.
        # See the TIER_DEFAULTS_BY_PROVIDER doc in jarvis/brain/manager.py.
        for provider, model in TIER_DEFAULTS_BY_PROVIDER["router"].items():
            if provider == "openrouter":
                continue  # deliberate free-model default; not a fast-tier slug
            if provider == "gemini":
                continue  # has no fast mode
            if provider == "nvidia":
                # NVIDIA NIM is a deliberate "not recommended"/slow provider
                # (free dev tier, 13-30s TTFB); its fastest catalogued router
                # model (llama-3.3-70b) is not a fast-tier slug.
                continue
            if provider == "openai" and model in {"gpt-5.5", "gpt-5"}:
                continue  # frontier main model without -mini suffix
            if provider == "grok":
                # xAI retired the -fast variants (grok-4.1-fast 404s); grok-4.3
                # is the only broadly available tool-capable model and serves
                # both tiers deliberately (see TIER_DEFAULTS_BY_PROVIDER).
                continue
            assert any(tag in model.lower() for tag in ("haiku", "flash", "mini", "fast", "chat", "small")), \
                f"{provider}: {model} does not look like a fast tier"

    def test_sub_models_look_frontier(self):
        for provider, model in TIER_DEFAULTS_BY_PROVIDER["deep"].items():
            if provider == "openrouter":
                continue  # deliberate free-model default; not a frontier slug
            if provider == "grok":
                continue  # grok-4.3 serves both tiers — see the router-tier note above
            assert any(tag in model.lower() for tag in
                       ("fable", "opus", "pro", "large", "reasoner", "gpt-4", "gpt-5",
                        "nemotron", "ultra")), \
                f"{provider}: {model} does not look like a frontier tier"

    def test_claude_deep_tier_is_reachable_opus_never_fable(self):
        """Maintainer decision 2026-06-14 (supersedes the 2026-06-10 fable
        mandate): claude-fable-5 is approved-access-only and the Claude Max
        subscription cannot reach it ("Claude Fable 5 is currently
        unavailable"). This deep-tier default feeds the computer-use planner and
        the deep brain — both call the Brain API directly with no
        model-unavailable retry — so it must pin a model we can actually reach."""
        assert TIER_DEFAULTS_BY_PROVIDER["deep"]["claude-api"] == "claude-opus-4-8"

    def test_router_and_deep_tiers_present(self):
        assert "router" in TIER_DEFAULTS_BY_PROVIDER
        assert "deep" in TIER_DEFAULTS_BY_PROVIDER

    def test_no_empty_model_strings(self):
        for tier, providers in TIER_DEFAULTS_BY_PROVIDER.items():
            for provider, model in providers.items():
                assert model, f"Leerer Model-String bei {tier}/{provider}"


class TestResolveTierModel:
    def test_explicit_model_wins(self):
        assert _resolve_tier_model("router", "gemini", "custom-gemini-model") == "custom-gemini-model"

    def test_falls_back_to_default_when_none(self):
        assert _resolve_tier_model("router", "claude-api", None) == "claude-haiku-4-5-20251001"

    def test_falls_back_to_default_when_empty_string(self):
        assert _resolve_tier_model("router", "claude-api", "") == "claude-haiku-4-5-20251001"

    def test_unknown_provider_returns_empty_string(self):
        assert _resolve_tier_model("router", "nonexistent-provider", None) == ""

    def test_unknown_tier_returns_empty_string(self):
        assert _resolve_tier_model("super_frontier", "claude-api", None) == ""

    def test_deep_gemini_default(self):
        # 2026-04-29: frontier everywhere (user mandate).
        assert _resolve_tier_model("deep", "gemini", None) == "gemini-3.1-pro-preview"

    def test_deep_openai_default(self):
        assert _resolve_tier_model("deep", "openai", None) == "gpt-5.5-pro"


class TestPublicGetter:
    def test_get_default_returns_model(self):
        # 2026-04-29 frontier update + Bug-API-1: gemini-3-flash-preview
        # because the Google API does not list the stable alias 'gemini-3-flash'.
        assert get_tier_default_model("router", "gemini") == "gemini-3-flash-preview"

    def test_get_default_returns_none_for_unknown_provider(self):
        assert get_tier_default_model("router", "nope") is None

    def test_get_default_returns_none_for_unknown_tier(self):
        assert get_tier_default_model("unknown_tier", "gemini") is None
