"""Phase 3: a user-selectable Computer-Use model per provider.

CU runs on the provider's main ``model`` by default; a pinned ``cu_model`` lets
the user run CU on a different (e.g. stronger) model than chat, with no automatic
escalation. Resolution is provider-agnostic (AP-21): cu_model -> the provider's
main model -> the router-tier default.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager, get_tier_default_model
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, load_config
from jarvis.harness import screenshot_only_loop as sol


# --- config field -----------------------------------------------------------


def test_brain_provider_config_has_cu_model():
    c = BrainProviderConfig(model="gemini-3.5-flash", cu_model="gemini-3.1-pro-preview")
    assert c.cu_model == "gemini-3.1-pro-preview"


def test_brain_provider_config_cu_model_defaults_none():
    assert BrainProviderConfig().cu_model is None


# --- BrainManager._cu_model precedence --------------------------------------


def test_cu_model_prefers_pinned_cu_model():
    cfg = load_config()
    cfg.brain.router.provider = "gemini"
    cfg.brain.providers["gemini"].model = "gemini-3.5-flash"
    cfg.brain.providers["gemini"].cu_model = "gemini-3.1-pro-preview"
    mgr = BrainManager.from_tier_config("router", cfg, EventBus())
    assert mgr._cu_model("gemini") == "gemini-3.1-pro-preview"


def test_cu_model_falls_back_to_main_model_when_unset():
    cfg = load_config()
    cfg.brain.router.provider = "gemini"
    cfg.brain.providers["gemini"].model = "gemini-3.5-flash"
    cfg.brain.providers["gemini"].cu_model = None
    mgr = BrainManager.from_tier_config("router", cfg, EventBus())
    assert mgr._cu_model("gemini") == "gemini-3.5-flash"


def test_cu_model_falls_back_to_router_default_for_absent_provider():
    cfg = load_config()
    cfg.brain.router.provider = "gemini"
    mgr = BrainManager.from_tier_config("router", cfg, EventBus())
    # A provider with no [brain.providers.<p>] block resolves to the router default.
    assert mgr._cu_model("openrouter") == (
        cfg.brain.providers.get("openrouter").cu_model
        or cfg.brain.providers.get("openrouter").model
        or get_tier_default_model("router", "openrouter")
    )


# --- _select_cu_model helper (loop-side, provider-agnostic) ------------------


def test_select_cu_model_prefers_cu_model():
    class _M:
        def _cu_model(self, p):
            return "cu-model-x"

        def _fast_model(self, p):
            return "fast-model-x"

    assert sol._select_cu_model(_M(), "gemini") == "cu-model-x"


def test_select_cu_model_falls_back_to_fast_when_no_cu_model():
    class _M:
        def _fast_model(self, p):
            return "fast-model-x"

    assert sol._select_cu_model(_M(), "gemini") == "fast-model-x"


def test_select_cu_model_falls_back_when_cu_model_returns_empty():
    class _M:
        def _cu_model(self, p):
            return None

        def _fast_model(self, p):
            return "fast-model-x"

    assert sol._select_cu_model(_M(), "gemini") == "fast-model-x"
