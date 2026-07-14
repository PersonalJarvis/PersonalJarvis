"""Canonical Tool Model config and legacy alias compatibility."""
from __future__ import annotations

from jarvis.core.config import BrainConfig, BrainProviderConfig, BrainTierConfig


def test_legacy_computer_use_tier_populates_canonical_tool_model() -> None:
    cfg = BrainConfig(computer_use={"provider": "gemini"})

    assert cfg.tool_model is not None
    assert cfg.tool_model.provider == "gemini"
    assert cfg.computer_use is cfg.tool_model


def test_canonical_tool_model_wins_when_both_tiers_are_present() -> None:
    cfg = BrainConfig(
        tool_model=BrainTierConfig(provider="openai"),
        computer_use=BrainTierConfig(provider="gemini"),
    )

    assert cfg.tool_model is not None
    assert cfg.tool_model.provider == "openai"
    assert cfg.computer_use is cfg.tool_model


def test_legacy_cu_model_populates_canonical_provider_model() -> None:
    cfg = BrainProviderConfig(cu_model="legacy-model")

    assert cfg.tool_model == "legacy-model"
    assert cfg.cu_model == "legacy-model"


def test_legacy_alias_assignment_updates_canonical_fields() -> None:
    provider = BrainProviderConfig(tool_model="first")
    provider.cu_model = "second"
    brain = BrainConfig(tool_model=BrainTierConfig(provider="gemini"))
    brain.computer_use = BrainTierConfig(provider="openai")

    assert provider.tool_model == "second"
    assert brain.tool_model is not None
    assert brain.tool_model.provider == "openai"
