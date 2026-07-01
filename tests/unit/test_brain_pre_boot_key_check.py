"""Bug-E tests (2026-04-29): pre-boot key check filters providers without a key.

Background: previously, providers without an API key could land in the fallback
chain, publish BrainTurnStarted, then crash at _ensure_client and
leave a hallucination tag in voice_turns
("openai/gpt-4o" even though no OpenAI key exists).

Now: BrainManager.from_tier_config() runs a pre-boot healthcheck
across all known providers and pushes missing keys straight into
_dead_providers — no more BrainTurnStarted event for hallucination-
prone providers.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import (
    BrainProviderConfig,
    BrainRouterPolicyConfig,
    BrainTierConfig,
    JarvisConfig,
)


def _make_cfg() -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.primary = "claude-api"
    cfg.brain.providers["claude-api"] = BrainProviderConfig(
        model="claude-haiku-4-5-20251001",
        deep_model="claude-opus-4-7",
    )
    cfg.brain.providers["gemini"] = BrainProviderConfig(
        model="gemini-3-flash",
        deep_model="gemini-3.1-pro-preview",
    )
    cfg.brain.providers["openai"] = BrainProviderConfig(
        model="gpt-5.5",
        deep_model="gpt-5.5-pro",
    )
    cfg.brain.providers["grok"] = BrainProviderConfig(
        model="grok-4.1-fast",
        deep_model="grok-4.20",
    )
    cfg.brain.router = BrainTierConfig(
        provider="claude-api",
        model="claude-haiku-4-5-20251001",
        fallback_provider="gemini",
        fallback_model="gemini-3-flash",
        policy=BrainRouterPolicyConfig(
            escalate_on_uncertainty=True,
            default_intent_on_low_confidence="spawn_worker",
        ),
    )
    cfg.brain.worker = BrainTierConfig(
        provider="gemini",
        model="gemini-3.1-pro-preview",
    )
    return cfg


def test_provider_without_key_lands_in_dead_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only claude-api+gemini have keys, openai+grok+openrouter are
    in _dead_providers after from_tier_config().
    """
    from jarvis.core import config as _cfg_mod

    available_keys = {"anthropic_api_key", "gemini_api_key"}

    def fake_get_secret(key: str, env_fallback: str | None = None) -> str | None:
        return "fake-key" if key in available_keys else None

    monkeypatch.setattr(_cfg_mod, "get_secret", fake_get_secret)

    cfg = _make_cfg()
    bm = BrainManager.from_tier_config("router", cfg, EventBus())

    assert "openai" in bm._dead_providers
    assert "grok" in bm._dead_providers
    assert "openrouter" in bm._dead_providers
    assert "claude-api" not in bm._dead_providers
    assert "gemini" not in bm._dead_providers


def test_all_keys_missing_kills_all_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worst case: not a single key. All providers are dead."""
    from jarvis.core import config as _cfg_mod

    monkeypatch.setattr(
        _cfg_mod, "get_secret", lambda key, env_fallback=None: None,
    )

    cfg = _make_cfg()
    bm = BrainManager.from_tier_config("router", cfg, EventBus())

    for prov in ("claude-api", "gemini", "openai", "grok", "openrouter"):
        assert prov in bm._dead_providers


def test_all_keys_present_no_dead_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy-Path: alle Keys da. _dead_providers ist leer."""
    from jarvis.core import config as _cfg_mod

    monkeypatch.setattr(
        _cfg_mod, "get_secret",
        lambda key, env_fallback=None: "fake-key-" + key,
    )

    cfg = _make_cfg()
    bm = BrainManager.from_tier_config("router", cfg, EventBus())

    assert bm._dead_providers == set()


def test_provider_key_aliases_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini/xAI alias keys count as configured for the canonical providers."""
    from jarvis.core import config as _cfg_mod

    available_keys = {
        "anthropic_api_key",
        "google_aistudio_api_key",
        "xai_api_key",
    }

    def fake_get_secret(key: str, env_fallback: str | None = None) -> str | None:
        return "fake-key" if key in available_keys else None

    monkeypatch.setattr(_cfg_mod, "get_secret", fake_get_secret)

    cfg = _make_cfg()
    bm = BrainManager.from_tier_config("router", cfg, EventBus())

    assert "gemini" not in bm._dead_providers
    assert "grok" not in bm._dead_providers
    assert "claude-api" not in bm._dead_providers
    assert "openai" in bm._dead_providers
    assert "openrouter" in bm._dead_providers


def test_chain_excludes_dead_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider-Chain filtert _dead_providers raus."""
    from jarvis.core import config as _cfg_mod

    monkeypatch.setattr(
        _cfg_mod, "get_secret",
        lambda key, env_fallback=None: (
            "fake-key" if key in {"anthropic_api_key", "gemini_api_key"} else None
        ),
    )

    cfg = _make_cfg()
    bm = BrainManager.from_tier_config("router", cfg, EventBus())
    chain = bm._build_fallback_chain("fast")

    chain_providers = {prov for prov, _ in chain}
    assert "openai" not in chain_providers
    assert "grok" not in chain_providers
    assert "openrouter" not in chain_providers
