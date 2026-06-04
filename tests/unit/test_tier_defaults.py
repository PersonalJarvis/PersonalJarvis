"""Unit-Tests fuer TIER_DEFAULTS_BY_PROVIDER, _resolve_tier_model und get_tier_default_model."""
from __future__ import annotations

import pytest

from jarvis.brain.manager import (
    TIER_DEFAULTS_BY_PROVIDER,
    _resolve_tier_model,
    get_tier_default_model,
)


class TestTierDefaultsCatalog:
    def test_all_known_providers_have_router_defaults(self):
        required = {"claude-api", "gemini", "openai", "grok"}
        assert required <= set(TIER_DEFAULTS_BY_PROVIDER["router"])

    def test_all_known_providers_have_deep_defaults(self):
        required = {"claude-api", "gemini", "openai"}
        assert required <= set(TIER_DEFAULTS_BY_PROVIDER["deep"])

    def test_router_models_look_fast(self):
        # 2026-04-29 Update: GPT-5.5 hat keine -mini-Variante released; OpenAI
        # sagt selbst "GPT-5.5 hat per-token-Latenz wie GPT-5.4". Fast-Tier-
        # Heuristik akzeptiert daher auch Frontier-Hauptmodelle.
        # 2026-04-30 Update: xAI hat grok-4.3 als gleichzeitig schnellstes
        # UND intelligentestes Grok released — keine -fast-Variante mehr.
        # Siehe TIER_DEFAULTS_BY_PROVIDER-Doc in jarvis/brain/manager.py.
        for provider, model in TIER_DEFAULTS_BY_PROVIDER["router"].items():
            if provider == "gemini":
                continue  # hat keinen Fast-Mode
            if provider == "openai" and model in {"gpt-5.5", "gpt-5"}:
                continue  # Frontier-Hauptmodell ohne -mini-Suffix
            if provider == "grok" and model.startswith("grok-4"):
                continue  # Frontier-Hauptmodell ohne -fast-Suffix
            assert any(tag in model.lower() for tag in ("haiku", "flash", "mini", "fast", "chat", "small")), \
                f"{provider}: {model} sieht nicht nach Fast-Tier aus"

    def test_sub_models_look_frontier(self):
        for provider, model in TIER_DEFAULTS_BY_PROVIDER["deep"].items():
            assert any(tag in model.lower() for tag in
                       ("opus", "pro", "large", "reasoner", "gpt-4", "gpt-5", "grok-4")), \
                f"{provider}: {model} sieht nicht nach Frontier-Tier aus"

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
        # 2026-04-29: Frontier ueberall (User-Mandat).
        assert _resolve_tier_model("deep", "gemini", None) == "gemini-3.1-pro-preview"

    def test_deep_openai_default(self):
        assert _resolve_tier_model("deep", "openai", None) == "gpt-5.5-pro"


class TestPublicGetter:
    def test_get_default_returns_model(self):
        # 2026-04-29 Frontier-Update + Bug-API-1: gemini-3-flash-preview
        # weil Google API das Stable-Alias 'gemini-3-flash' nicht listet.
        assert get_tier_default_model("router", "gemini") == "gemini-3-flash-preview"

    def test_get_default_returns_none_for_unknown_provider(self):
        assert get_tier_default_model("router", "nope") is None

    def test_get_default_returns_none_for_unknown_tier(self):
        assert get_tier_default_model("unknown_tier", "gemini") is None
