"""Verifies that BrainTierConfig.model is optional.

Integration with the resolver from manager.py is tested in
test_tier_defaults.py; here we only cover the config schema.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.core.config import BrainTierConfig


class TestBrainTierConfigOptional:
    def test_provider_only_is_valid(self):
        cfg = BrainTierConfig(provider="gemini")
        assert cfg.provider == "gemini"
        assert cfg.model is None

    def test_explicit_model_preserved(self):
        cfg = BrainTierConfig(provider="claude-api", model="claude-haiku-4-5-20251001")
        assert cfg.model == "claude-haiku-4-5-20251001"

    def test_provider_is_required(self):
        with pytest.raises(ValidationError):
            BrainTierConfig()

    def test_fallbacks_optional(self):
        cfg = BrainTierConfig(provider="gemini")
        assert cfg.fallback_provider is None
        assert cfg.fallback_model is None

    def test_full_config_still_works(self):
        cfg = BrainTierConfig(
            provider="claude-api",
            model="claude-haiku-4-5-20251001",
            fallback_provider="gemini",
            fallback_model="gemini-3-flash",
            fallback_provider_2="openai",
            fallback_model_2="gpt-5-mini",
        )
        assert cfg.fallback_provider == "gemini"
        assert cfg.fallback_model_2 == "gpt-5-mini"

    def test_extra_fields_allowed(self):
        # Backward-compat: old configs with "deep_model" or other
        # fields should not crash
        cfg = BrainTierConfig(provider="gemini", model=None, custom_field="ignored")
        assert cfg.provider == "gemini"
