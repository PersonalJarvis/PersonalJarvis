"""Regression: router tier must resolve to the FAST model, not the deep fallback.

Root-cause bug (jarvis/brain/manager.py from_tier_config): when
[brain.router].provider == fallback_provider (both "gemini"), the fallback-model
assignment clobbered the primary-model assignment on the shared
providers["gemini"] config entry (last-write-wins). The router then silently ran
on the slow deep model (gemini-3.1-pro-preview, ~9 s "thinking") instead of the
configured fast model (gemini-3.5-flash). Live evidence: voice_turns row for
"Was geht ab?" recorded model=gemini-3.1-pro-preview, think_ms=8900.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainTierConfig, JarvisConfig, load_config

# One representative non-default frontier model per configured brain provider.
# Gemini is only one row — model selection must resolve for EVERY provider.
PROVIDER_FAST_DEEP_CASES = [
    ("claude-api", "claude-haiku-4-5-20251001", "claude-opus-4-8"),
    ("openrouter", "anthropic/claude-sonnet-4.6", "anthropic/claude-opus-4.8"),
    ("openai", "gpt-5.5", "gpt-5.5-pro"),
    ("gemini", "gemini-3.5-flash", "gemini-3.1-pro-preview"),
    ("grok", "grok-4.3", "grok-4.3"),
]


def test_router_keeps_fast_model_when_fallback_is_same_provider() -> None:
    cfg = load_config()
    # Real-world shape: gemini FAST primary + gemini PRO fallback (same provider).
    cfg.brain.router.provider = "gemini"
    cfg.brain.router.model = "gemini-3.5-flash"
    cfg.brain.router.fallback_provider = "gemini"
    cfg.brain.router.fallback_model = "gemini-3.1-pro-preview"

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())

    # The fast/router model MUST stay flash — the fallback must not clobber it.
    assert mgr._fast_model("gemini") == "gemini-3.5-flash"


def test_router_fallback_chain_still_offers_pro_as_failover() -> None:
    # Fixing the clobber must NOT drop the pro failover from the chain — flash
    # is primary, pro remains the gemini-tier failover.
    cfg = load_config()
    cfg.brain.router.provider = "gemini"
    cfg.brain.router.model = "gemini-3.5-flash"
    cfg.brain.router.fallback_provider = "gemini"
    cfg.brain.router.fallback_model = "gemini-3.1-pro-preview"

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())
    chain = mgr._build_fallback_chain("fast")

    assert chain[0] == ("gemini", "gemini-3.5-flash")  # primary = flash
    assert ("gemini", "gemini-3.1-pro-preview") in chain  # pro still a failover


def test_router_caps_thinking_budget_without_touching_global_config() -> None:
    # The router (dispatcher) must not "think" for seconds. The cap lives on the
    # deep-copied router config only — the global config (used by workers/critic)
    # keeps its full-reasoning default.
    cfg = load_config()
    cfg.brain.router.provider = "gemini"
    cfg.brain.router.model = "gemini-3.5-flash"

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())

    assert mgr._config.brain.providers["gemini"].thinking_budget == 0  # router capped
    assert cfg.brain.providers["gemini"].thinking_budget is None  # global untouched


def test_codex_chatgpt_login_does_not_make_codex_a_main_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex OAuth unlocks the subagent only; the main router falls back."""
    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda *_a, **_k: None)

    class _ConnectedCodexAuth:
        def status(self):
            class _Status:
                connected = True
                mode = "chatgpt"

            return _Status()

    monkeypatch.setattr("jarvis.codex_auth.CodexAuthService", _ConnectedCodexAuth)

    cfg = JarvisConfig()
    cfg.brain.router = BrainTierConfig(provider="codex", fallback_provider="gemini")

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())

    assert mgr.active_provider == "gemini"
    assert mgr._config.brain.primary == "gemini"
    assert "codex" not in [provider for provider, _model in mgr._build_fallback_chain("fast")]


def test_user_selected_deep_model_drives_the_deep_chain() -> None:
    """Per-user model selection (2026-06-20): the model the user picks for the
    DEEP tier of a provider must be the one a deep/code turn actually runs on —
    so it is the model published on the brain turn and shown in the transcript.

    Guards the deep axis specifically: the existing tests above only cover the
    fast/router model. The fast-tier resolver is steered by [brain.router].model;
    the deep-tier model is steered by [brain.providers.<p>].deep_model, and the
    router-config build must NOT clobber that selection.
    """
    cfg = load_config()
    cfg.brain.router.provider = "gemini"
    cfg.brain.router.model = "gemini-3.5-flash"
    # The user explicitly picks a non-default deep model for Gemini.
    cfg.brain.providers["gemini"].deep_model = "gemini-3.1-pro-preview"

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())

    assert mgr._deep_model("gemini") == "gemini-3.1-pro-preview"
    # A deep/code turn leads with the user-selected deep model on the active
    # provider — exactly the (provider, model) pair that ends up on
    # BrainTurnCompleted and therefore in VoiceTurnRow.model.
    chain = mgr._build_fallback_chain("deep")
    assert chain[0] == ("gemini", "gemini-3.1-pro-preview")


@pytest.mark.parametrize("provider,fast_pick,_deep", PROVIDER_FAST_DEEP_CASES)
def test_user_selected_fast_model_resolves_for_every_provider(
    provider, fast_pick, _deep
) -> None:
    """Gemini is only the example: the fast-tier model the user picks is honoured
    for EVERY brain provider when that provider is the active router brain. The
    fast/router model is steered by [brain.router].model and must survive the
    same-provider-fallback clobber for all of them."""
    cfg = load_config()
    cfg.brain.router.provider = provider
    cfg.brain.router.model = fast_pick
    cfg.brain.router.fallback_provider = provider
    cfg.brain.router.fallback_model = fast_pick

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())

    assert mgr._fast_model(provider) == fast_pick


@pytest.mark.parametrize("provider,_fast,deep_pick", PROVIDER_FAST_DEEP_CASES)
def test_user_selected_deep_model_resolves_for_every_provider(
    provider, _fast, deep_pick
) -> None:
    """Gemini is only the example: the deep-tier model selection is honoured for
    EVERY brain provider. _deep_model reads [brain.providers.<p>].deep_model, so
    whatever model the user picks per provider is the one a deep turn runs on
    (and thus the one published on the brain turn and shown in the transcript).
    The router-config build must not clobber deep_model for any provider."""
    cfg = load_config()
    cfg.brain.providers[provider].deep_model = deep_pick

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())

    assert mgr._deep_model(provider) == deep_pick
