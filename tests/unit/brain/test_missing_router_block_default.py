"""A fresh install ships no [brain.router] block — the brain must still build.

Regression (AP-23 "works on my machine"): ``jarvis.toml.example`` has a [brain]
table but NO [brain.router] sub-table, and neither the wizard, the installer,
nor onboarding writes one. ``BrainManager.from_tier_config`` used to RAISE
``BrainConfigError`` when ``config.brain.router`` was ``None``. On the desktop
that left ``app.state.brain = None``, which bricked both voice/chat AND the
provider-switch route — the latter then surfaced a misleading "headless mode"
503 on every "Set active" click. The maintainer never saw it because their own
jarvis.toml HAS the block.

The fix synthesizes a default router tier from the user's real ``brain.primary``
selection, so a fresh install boots a working brain regardless of provider.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import load_config


def test_missing_router_block_does_not_raise_and_builds() -> None:
    # Simulate a fresh install: no [brain.router] block at all.
    cfg = load_config()
    cfg.brain.router = None
    cfg.brain.primary = "claude-api"  # keyed in the test environment

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())

    # It built (no BrainConfigError) and honours the configured primary.
    assert mgr.active_provider == "claude-api"
    assert mgr._config.brain.primary == "claude-api"


def test_synthesized_tier_follows_the_users_primary_provider() -> None:
    # Provider-agnostic: the synthesized tier must follow whatever main provider
    # the fresh user configured, NOT a hardcoded claude-api (AP-6/AP-21).
    cfg = load_config()
    cfg.brain.router = None
    cfg.brain.primary = "gemini"

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())

    assert mgr.active_provider == "gemini"
    assert mgr._config.brain.primary == "gemini"


def test_missing_router_falls_back_to_routing_provider_when_primary_blank() -> None:
    # Defensive: an empty primary must not produce an empty-provider tier — it
    # falls back to routing_provider (which itself defaults to a real provider).
    cfg = load_config()
    cfg.brain.router = None
    cfg.brain.primary = ""
    cfg.brain.routing_provider = "claude-api"

    mgr = BrainManager.from_tier_config("router", cfg, EventBus())

    assert mgr.active_provider == "claude-api"
