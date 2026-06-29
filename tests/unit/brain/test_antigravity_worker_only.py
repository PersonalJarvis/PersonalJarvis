"""Screenshot-blind CLI providers are subagent-only, not the main Brain."""
from __future__ import annotations

import asyncio

from jarvis.brain.manager import (
    SUBAGENT_ONLY_BRAIN_PROVIDERS,
    TIER_DEFAULTS_BY_PROVIDER,
    BrainManager,
)
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainTierConfig, JarvisConfig


def test_antigravity_is_not_a_main_brain_tier_default() -> None:
    assert "antigravity" in SUBAGENT_ONLY_BRAIN_PROVIDERS
    assert "antigravity" not in TIER_DEFAULTS_BY_PROVIDER["router"]
    assert "antigravity" not in TIER_DEFAULTS_BY_PROVIDER["deep"]


def test_codex_is_not_a_main_brain_tier_default() -> None:
    assert "codex" in SUBAGENT_ONLY_BRAIN_PROVIDERS
    assert "openai-codex" in SUBAGENT_ONLY_BRAIN_PROVIDERS
    assert "codex" not in TIER_DEFAULTS_BY_PROVIDER["router"]
    assert "codex" not in TIER_DEFAULTS_BY_PROVIDER["deep"]


def test_antigravity_startup_override_falls_back_to_router_provider() -> None:
    cfg = JarvisConfig()
    cfg.brain.primary = "antigravity"
    cfg.brain.router = BrainTierConfig(
        provider="openai",
        fallback_provider="gemini",
    )

    mgr = BrainManager.from_tier_config(
        "router",
        cfg,
        EventBus(),
        provider_override="antigravity",
    )

    assert mgr.active_provider == "openai"
    assert mgr._config.brain.primary == "openai"


def test_codex_startup_override_falls_back_to_router_provider() -> None:
    cfg = JarvisConfig()
    cfg.brain.primary = "codex"
    cfg.brain.router = BrainTierConfig(
        provider="openai",
        fallback_provider="gemini",
    )

    mgr = BrainManager.from_tier_config(
        "router",
        cfg,
        EventBus(),
        provider_override="codex",
    )

    assert mgr.active_provider == "openai"
    assert mgr._config.brain.primary == "openai"


def test_antigravity_direct_manager_config_falls_back_to_router_provider() -> None:
    cfg = JarvisConfig()
    cfg.brain.primary = "antigravity"
    cfg.brain.router = BrainTierConfig(
        provider="gemini",
        fallback_provider="openai",
    )

    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})

    assert mgr.active_provider == "gemini"
    assert cfg.brain.primary == "gemini"


def test_codex_direct_manager_config_falls_back_to_router_provider() -> None:
    cfg = JarvisConfig()
    cfg.brain.primary = "codex"
    cfg.brain.router = BrainTierConfig(
        provider="gemini",
        fallback_provider="openai",
    )

    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})

    assert mgr.active_provider == "gemini"
    assert cfg.brain.primary == "gemini"


def test_runtime_switch_rejects_subagent_only_provider() -> None:
    cfg = JarvisConfig()
    cfg.brain.primary = "gemini"
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})

    asyncio.run(mgr.switch("codex", persist=True))
    asyncio.run(mgr.switch("openai-codex", persist=True))
    asyncio.run(mgr.switch("antigravity", persist=True))

    assert mgr.active_provider == "gemini"
    assert cfg.brain.primary == "gemini"
    assert mgr.last_persist_ok is False
