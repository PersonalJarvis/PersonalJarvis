"""Regression: the in-process API critic must grade with the user's PICKED model
for the keyed provider, never fall to a hardcoded paid default.

Live forensic 2026-06-29: ``_resolve_api_critic_provider`` returned ``model=None``
for any provider that was not the worker's primary. The in-process critic then did
``cls()`` → the plugin's ``DEFAULT_MODEL`` (OpenRouter = anthropic/claude-opus-4.8),
billing the user's OpenRouter key for Opus while reviewing a mission whose worker
ran elsewhere. The critic must reuse the provider's own configured model
(AP-21/AP-22, open-source single-key §3).
"""
from __future__ import annotations

import pytest

from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.missions.critic import runner

_FREE = "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_cross_family_critic_uses_provider_pick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = JarvisConfig()
    config.brain.providers["openrouter"] = BrainProviderConfig(model=_FREE)
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: config)
    # Only the OpenRouter key is present at runtime.
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret",
        lambda p: "sk-test" if p == "openrouter" else None,
    )

    # Worker ran on antigravity (a non-API provider) → critic crosses family to
    # the keyed OpenRouter provider.
    prov, model = runner._resolve_api_critic_provider("antigravity", None)

    assert prov == "openrouter"
    assert model == _FREE, f"cross-family critic did not reuse the pick: {model!r}"
    assert "anthropic/claude" not in (model or "")


def test_same_provider_critic_keeps_primary_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret",
        lambda p: "sk-test" if p == "openrouter" else None,
    )
    prov, model = runner._resolve_api_critic_provider("openrouter", _FREE)
    assert prov == "openrouter"
    assert model == _FREE
