"""deep_brain must follow the user's chosen active provider.

User mandate: "<provider> for everything." Forensic: with primary=openai but
[brain.router].{provider,fallback_provider}=gemini, the startup override moved the
ACTIVE provider to openai (factory.py:817) while deep_brain stayed pinned to the
tier's orphaned fallback (gemini). Result: deep/code intents led with Gemini
(gemini-3.1-pro) even though the user picked OpenAI — chain[deep][0] == ("gemini", …).

These lock that deep_brain follows the active provider both at boot (override) and
on a runtime switch, UNLESS there is a deliberate cross-provider deep split
(fallback_provider != provider), which must be preserved.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainTierConfig, load_config


@pytest.fixture(autouse=True)
def _all_test_providers_have_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep chain-order tests independent of the host's private key store."""
    monkeypatch.setattr(
        "jarvis.core.config.get_secret_any",
        lambda _candidates: "test-key",
    )


def _cfg(*, primary: str, provider: str, fallback_provider: str | None):
    cfg = load_config()
    cfg.brain.primary = primary
    cfg.brain.router = BrainTierConfig(
        provider=provider,
        fallback_provider=fallback_provider,
    )
    return cfg


def test_deep_brain_follows_override_when_no_explicit_split() -> None:
    # primary=claude-api overrides the tier default gemini; fallback==provider
    # means there is NO deliberate cross-provider deep split → deep must follow
    # claude-api. The fixture supplies synthetic credentials so this assertion
    # never depends on a maintainer machine's private key store.
    cfg = _cfg(primary="claude-api", provider="gemini", fallback_provider="gemini")
    mgr = BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="claude-api")

    assert mgr.active_provider == "claude-api"
    assert mgr._config.brain.deep_brain == "claude-api"
    deep_chain = mgr._build_fallback_chain("deep")
    # The override provider leads deep/code, NOT Gemini (the exact regression).
    assert deep_chain[0][0] == "claude-api"
    assert deep_chain[0][0] != "gemini"


def test_code_intent_also_led_by_override_provider() -> None:
    cfg = _cfg(primary="claude-api", provider="gemini", fallback_provider="gemini")
    mgr = BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="claude-api")
    assert mgr._build_fallback_chain("code")[0][0] == "claude-api"


def test_explicit_cross_provider_deep_split_is_preserved() -> None:
    # fallback_provider != provider is a deliberate "delegate deep elsewhere"
    # split — an override of the fast provider must NOT erase it.
    cfg = _cfg(primary="claude-api", provider="gemini", fallback_provider="openai")
    mgr = BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="claude-api")
    assert mgr.active_provider == "claude-api"
    assert mgr._config.brain.deep_brain == "openai"


def test_deep_brain_follows_override_when_no_fallback_configured() -> None:
    # A minimal toml ([brain.router] provider only, no fallback_provider) must
    # NOT strand deep_brain at None when an override is active — it follows the
    # override provider.
    cfg = _cfg(primary="claude-api", provider="gemini", fallback_provider=None)
    mgr = BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="claude-api")
    assert mgr._config.brain.deep_brain == "claude-api"
    assert mgr._build_fallback_chain("deep")[0][0] == "claude-api"


def test_deep_brain_stable_across_two_boots() -> None:
    # The override re-derives deep_brain at EVERY boot from the persisted primary,
    # so deep_brain must be identical after a simulated restart (no persistence of
    # deep_brain itself needed). Load-bearing safety assumption — lock it.
    def boot():
        cfg = _cfg(primary="claude-api", provider="gemini", fallback_provider="gemini")
        return BrainManager.from_tier_config(
            "router", cfg, EventBus(), provider_override="claude-api"
        )._config.brain.deep_brain

    assert boot() == "claude-api"
    assert boot() == "claude-api"


def test_no_override_keeps_tier_fallback_as_deep() -> None:
    # No override (primary == tier provider) → deep_brain stays the tier fallback
    # (unchanged behaviour, model-based tiering still works).
    cfg = _cfg(primary="gemini", provider="gemini", fallback_provider="gemini")
    mgr = BrainManager.from_tier_config("router", cfg, EventBus())
    assert mgr._config.brain.deep_brain == "gemini"


@pytest.mark.asyncio
async def test_runtime_switch_carries_deep_brain() -> None:
    # Boot on gemini (deep_brain=gemini), then switch to openai at runtime: deep
    # must follow so a frontier switch leads ALL intents, not just fast ones.
    cfg = _cfg(primary="gemini", provider="gemini", fallback_provider="gemini")
    mgr = BrainManager.from_tier_config("router", cfg, EventBus())
    assert mgr._config.brain.deep_brain == "gemini"

    await mgr.switch("openai")

    assert mgr.active_provider == "openai"
    assert mgr._config.brain.deep_brain == "openai"
    assert mgr._build_fallback_chain("deep")[0][0] == "openai"


@pytest.mark.asyncio
async def test_runtime_switch_promotes_none_deep_brain() -> None:
    # deep_brain=None (no fallback configured, no override at boot) must follow a
    # runtime switch instead of staying stranded at None.
    cfg = _cfg(primary="gemini", provider="gemini", fallback_provider=None)
    mgr = BrainManager.from_tier_config("router", cfg, EventBus())
    assert mgr._config.brain.deep_brain is None
    await mgr.switch("openai")
    assert mgr.active_provider == "openai"
    assert mgr._config.brain.deep_brain == "openai"


@pytest.mark.asyncio
async def test_runtime_switch_preserves_explicit_split() -> None:
    # With an explicit deep split (deep_brain != active), a runtime switch of the
    # fast provider must leave the deep delegation intact.
    cfg = _cfg(primary="gemini", provider="gemini", fallback_provider="claude-api")
    mgr = BrainManager.from_tier_config("router", cfg, EventBus())
    assert mgr._config.brain.deep_brain == "claude-api"

    await mgr.switch("openai")

    assert mgr.active_provider == "openai"
    assert mgr._config.brain.deep_brain == "claude-api"
