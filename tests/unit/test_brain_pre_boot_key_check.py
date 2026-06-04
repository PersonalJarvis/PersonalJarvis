"""Bug-E-Tests (2026-04-29): Pre-Boot-Key-Check filtert Provider ohne Key.

Hintergrund: Vorher konnten Provider ohne API-Key in der Fallback-Chain
landen, mit BrainTurnStarted publishen, dann beim _ensure_client crashen
und einen Halluzinations-Tag in voice_turns hinterlassen
("openai/gpt-4o" obwohl kein OpenAI-Key existiert).

Jetzt: BrainManager.from_tier_config() macht einen Pre-Boot-Healthcheck
ueber alle bekannten Provider und schiebt fehlende Keys direkt in
_dead_providers — kein BrainTurnStarted-Event mehr fuer Halluzinations-
Provider.
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
    cfg.brain.sub_jarvis = BrainTierConfig(
        provider="gemini",
        model="gemini-3.1-pro-preview",
    )
    return cfg


def test_provider_without_key_lands_in_dead_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wenn nur claude-api+gemini Keys haben, sind openai+grok+openrouter
    in _dead_providers nach from_tier_config().
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
    """Worst-Case: kein einziger Key. Alle Provider sind dead."""
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
