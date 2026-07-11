"""Delegated realtime turns run on the Tool-Model pick.

``generate(prefer_tool_model=True)`` hoists the [brain.computer_use] provider
(the Tool Model tab's selection) to the head of the turn's fallback chain —
fresh per call, cross-family fallback intact (AP-21/22), no global state.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainTierConfig, load_config


def _manager(tool_provider: str | None = "gemini") -> BrainManager:
    cfg = load_config()
    cfg.brain.router.provider = "openrouter"
    if tool_provider is None:
        cfg.brain.computer_use = None
    else:
        cfg.brain.computer_use = BrainTierConfig(provider=tool_provider)
    return BrainManager.from_tier_config("router", cfg, EventBus())


_CHAIN: list[tuple[str, str | None]] = [
    ("openrouter", "fast-model"),
    ("groq", None),
    ("gemini", "gemini-3.5-flash"),
]


def test_hoist_puts_tool_model_first_and_filters_exact_duplicate():
    mgr = _manager("gemini")
    mgr._config.brain.providers["gemini"].model = "gemini-3.5-flash"
    mgr._config.brain.providers["gemini"].cu_model = None

    hoisted = mgr._hoist_tool_model(list(_CHAIN))

    assert hoisted[0] == ("gemini", "gemini-3.5-flash")
    # The exact duplicate is gone; everything else keeps its order.
    assert hoisted[1:] == [("openrouter", "fast-model"), ("groq", None)]


def test_hoist_keeps_same_provider_entries_with_other_models():
    mgr = _manager("gemini")
    mgr._config.brain.providers["gemini"].cu_model = "gemini-3.1-pro-preview"

    hoisted = mgr._hoist_tool_model(list(_CHAIN))

    assert hoisted[0] == ("gemini", "gemini-3.1-pro-preview")
    # The chain's own gemini entry differs by model and survives as a
    # legitimate same-provider fallback.
    assert ("gemini", "gemini-3.5-flash") in hoisted[1:]


def test_hoist_is_a_no_op_when_unconfigured():
    mgr = _manager(None)
    assert mgr._hoist_tool_model(list(_CHAIN)) == _CHAIN


def test_hoist_skips_a_dead_tool_provider():
    mgr = _manager("gemini")
    mgr._dead_providers.add("gemini")
    assert mgr._hoist_tool_model(list(_CHAIN)) == _CHAIN


def test_hoist_reads_the_pick_fresh_per_call():
    """/api/computer-use/switch semantics: an in-memory switch takes effect
    on the very next delegated turn, no session rebuild."""
    mgr = _manager("gemini")
    assert mgr._hoist_tool_model(list(_CHAIN))[0][0] == "gemini"

    mgr._config.brain.computer_use = BrainTierConfig(provider="groq")
    assert mgr._hoist_tool_model(list(_CHAIN))[0][0] == "groq"
