"""Regression: ``from_tier_config`` must honor the OVERRIDE provider's OWN picked
model, never clobber it with the hardcoded paid TIER default.

The decisive boot-path money leak (live forensic 2026-06-29): ``brain.primary=
"openrouter"`` differs from ``[brain.router].provider``, so ``factory.py`` passes
``provider_override="openrouter"`` into ``from_tier_config``. The builder then set
``explicit_model = None`` and WROTE the hardcoded TIER default
(``anthropic/claude-haiku-4.5`` for router, ``anthropic/claude-opus-4.8`` for deep)
INTO ``[brain.providers.openrouter].model`` — overwriting the user's FREE pick in
the in-memory config BEFORE ``_fast_model``/``_deep_model`` ever read it. So every
router and deep turn billed a paid Anthropic model over OpenRouter despite the free
selection (~5€ key drained). The ``_fast_model``/``_deep_model`` "prefer cfg.model"
fixes do not help on their own — this clobber poisons ``cfg.model`` upstream.

A user-selected provider must run the model the user picked, never a hardcoded
foreign-family default (AP-21/AP-22, open-source single-key §3).
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import (
    BrainProviderConfig,
    BrainTierConfig,
    JarvisConfig,
)

_FREE = "nvidia/nemotron-3-ultra-550b-a55b:free"


def _build_router_with_openrouter_primary() -> BrainManager:
    config = JarvisConfig()
    config.brain.primary = "openrouter"
    config.brain.providers["openrouter"] = BrainProviderConfig(model=_FREE)
    # The router tier points at a DIFFERENT provider — exactly what makes
    # factory.py set provider_override = brain.primary at boot.
    config.brain.router = BrainTierConfig(provider="gemini")
    return BrainManager.from_tier_config(
        "router", config=config, bus=EventBus(), provider_override="openrouter",
    )


def test_override_provider_keeps_its_picked_model_fast() -> None:
    mgr = _build_router_with_openrouter_primary()
    assert mgr._fast_model("openrouter") == _FREE
    assert "anthropic/claude" not in (mgr._fast_model("openrouter") or "")


def test_override_provider_keeps_its_picked_model_deep() -> None:
    mgr = _build_router_with_openrouter_primary()
    assert mgr._deep_model("openrouter") == _FREE
    assert "anthropic/claude" not in (mgr._deep_model("openrouter") or "")


def test_in_memory_config_not_clobbered_to_paid_default() -> None:
    """The in-memory provider config the manager runs from must still hold the
    user's pick — not the hardcoded paid TIER default."""
    mgr = _build_router_with_openrouter_primary()
    assert mgr._config.brain.providers["openrouter"].model == _FREE
