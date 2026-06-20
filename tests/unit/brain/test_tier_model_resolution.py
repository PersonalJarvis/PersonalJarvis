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

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import load_config


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
