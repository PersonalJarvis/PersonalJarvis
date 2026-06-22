"""Antigravity must be a first-class active brain in the fallback chain.

Two regressions guarded here (both the same class as the 2026-06-09 "Gemini
answered while Codex was the active brain" bug):

1. TIER_DEFAULTS_BY_PROVIDER had no "antigravity" row, so `_fast_model` /
   `_deep_model` returned None when no model was pinned and `_build_fallback_chain`
   SILENTLY DROPPED antigravity — the chosen brain never answered.
2. The deep_brain-hijack guard exempted only "codex". With antigravity active and
   a foreign deep_brain (e.g. gemini), deep/code intents led with that other
   provider — antigravity (itself a frontier subscription brain) never answered a
   hard question.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER, BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import load_config


def test_antigravity_has_tier_defaults_for_router_and_deep() -> None:
    assert TIER_DEFAULTS_BY_PROVIDER["router"].get("antigravity")
    assert TIER_DEFAULTS_BY_PROVIDER["deep"].get("antigravity")


def test_antigravity_fast_and_deep_model_not_none_without_pin() -> None:
    cfg = load_config()
    cfg.brain.primary = "antigravity"
    cfg.brain.router.provider = "antigravity"
    cfg.brain.router.fallback_provider = "antigravity"
    if "antigravity" in cfg.brain.providers:
        cfg.brain.providers["antigravity"].model = ""  # unpinned
    mgr = BrainManager.from_tier_config(
        "router", cfg, EventBus(), provider_override="antigravity"
    )
    # Tier default kicks in → antigravity is not dropped from the chain.
    assert mgr._fast_model("antigravity")
    assert mgr._deep_model("antigravity")


def test_antigravity_active_leads_deep_not_foreign_deep_brain(monkeypatch) -> None:
    cfg = load_config()
    cfg.brain.primary = "antigravity"
    cfg.brain.router.provider = "antigravity"
    cfg.brain.router.fallback_provider = "gemini"  # deliberate cross-provider split
    mgr = BrainManager.from_tier_config(
        "router", cfg, EventBus(), provider_override="antigravity"
    )
    # Force both eligible so step-0 (deep_brain) is genuinely in play.
    monkeypatch.setattr(mgr._registry, "available", lambda: ["antigravity", "gemini"])
    assert mgr._config.brain.deep_brain == "gemini"  # explicit split preserved
    deep_chain = mgr._build_fallback_chain("deep")
    # antigravity (a frontier subscription brain) leads deep/code, NOT gemini.
    assert deep_chain[0][0] == "antigravity"
