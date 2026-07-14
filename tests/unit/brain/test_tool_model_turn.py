"""Capability-aware Tool Model resolution for delegated turns."""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import (
    BrainProviderConfig,
    BrainTierConfig,
    JarvisConfig,
)


def _manager(tool_provider: str | None = "gemini") -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "openrouter"
    cfg.brain.router = BrainTierConfig(provider="openrouter")
    cfg.brain.providers = {
        "openrouter": BrainProviderConfig(model="fast-model"),
        "gemini": BrainProviderConfig(model="gemini-3.5-flash"),
    }
    cfg.brain.tool_model = (
        BrainTierConfig(provider=tool_provider) if tool_provider is not None else None
    )
    return BrainManager(cfg, EventBus())


def _stub_ready(mgr: BrainManager, ready: set[str]) -> None:
    """Make chain tests hermetic; candidate-probe behavior is tested below."""
    def _status(provider: str, model: str | None = None):
        is_ready = provider in ready and provider not in mgr._dead_providers
        return {
            "provider": provider,
            "model": model,
            "ready": is_ready,
            "reason": "ready" if is_ready else "unavailable",
            "tools": is_ready,
            "vision": None,
        }

    mgr.tool_model_candidate_status = _status  # type: ignore[method-assign]


_CHAIN: list[tuple[str, str | None]] = [
    ("openrouter", "fast-model"),
    ("grok", "grok-4.3"),
    ("gemini", "gemini-3.5-flash"),
]


def test_hoist_puts_tool_model_first_and_filters_exact_duplicate():
    mgr = _manager("gemini")
    _stub_ready(mgr, {"gemini", "openrouter", "grok"})
    mgr._config.brain.providers["gemini"].model = "gemini-3.5-flash"
    mgr._config.brain.providers["gemini"].tool_model = None

    hoisted = mgr._hoist_tool_model(list(_CHAIN))

    assert hoisted[0] == ("gemini", "gemini-3.5-flash")
    assert hoisted[1:] == [("openrouter", "fast-model"), ("grok", "grok-4.3")]


def test_hoist_deduplicates_same_provider_family():
    mgr = _manager("gemini")
    _stub_ready(mgr, {"gemini", "openrouter", "grok"})
    mgr._config.brain.providers["gemini"].tool_model = "gemini-3.1-pro-preview"

    hoisted = mgr._hoist_tool_model(list(_CHAIN))

    assert hoisted[0] == ("gemini", "gemini-3.1-pro-preview")
    assert ("gemini", "gemini-3.5-flash") not in hoisted[1:]


def test_hoist_auto_selection_preserves_ready_cross_family_chain():
    mgr = _manager(None)
    _stub_ready(mgr, {"gemini", "openrouter", "grok"})
    assert mgr._hoist_tool_model(list(_CHAIN)) == _CHAIN


def test_hoist_auto_selection_honors_a_per_provider_tool_model_pin():
    mgr = _manager(None)
    _stub_ready(mgr, {"gemini", "openrouter", "grok"})
    mgr._config.brain.providers["openrouter"].tool_model = "tool-pinned"

    assert mgr._hoist_tool_model(list(_CHAIN))[0] == (
        "openrouter",
        "tool-pinned",
    )


def test_hoist_skips_a_dead_tool_provider_and_crosses_family():
    mgr = _manager("gemini")
    _stub_ready(mgr, {"gemini", "openrouter", "grok"})
    mgr._dead_providers.add("gemini")

    assert mgr._hoist_tool_model(list(_CHAIN)) == [
        ("openrouter", "fast-model"),
        ("grok", "grok-4.3"),
    ]


def test_hoist_reads_the_canonical_pick_fresh_per_call():
    mgr = _manager("gemini")
    _stub_ready(mgr, {"gemini", "openrouter", "grok"})
    assert mgr._hoist_tool_model(list(_CHAIN))[0][0] == "gemini"

    mgr._config.brain.tool_model = BrainTierConfig(provider="grok")
    assert mgr._hoist_tool_model(list(_CHAIN))[0][0] == "grok"


def test_candidate_status_rejects_missing_credential(monkeypatch):
    mgr = _manager("gemini")
    monkeypatch.setattr(mgr._registry, "available", lambda: ["gemini"])
    monkeypatch.setattr(mgr, "_tool_model_credential_ready", lambda _p: False)

    status = mgr.tool_model_candidate_status("gemini", "gemini-3.5-flash")

    assert status["ready"] is False
    assert status["reason"] == "missing_credential"


def test_candidate_status_rejects_runtime_toolless_model(monkeypatch):
    mgr = _manager("gemini")
    monkeypatch.setattr(mgr._registry, "available", lambda: ["gemini"])
    monkeypatch.setattr(mgr, "_tool_model_credential_ready", lambda _p: True)
    monkeypatch.setattr(
        mgr,
        "_get_brain",
        lambda _p, _m: type(
            "ToollessBrain",
            (),
            {"supports_tools": False, "supports_vision": True},
        )(),
    )

    status = mgr.tool_model_candidate_status("gemini", "text-only")

    assert status["ready"] is False
    assert status["reason"] == "tools_unsupported"
    assert status["vision"] is True


def test_resolution_reports_cross_family_fallback_reason():
    mgr = _manager("gemini")

    def _status(provider: str, model: str | None = None):
        ready = provider == "openrouter"
        return {
            "provider": provider,
            "model": model,
            "ready": ready,
            "reason": "ready" if ready else "tools_unsupported",
            "tools": ready,
            "vision": None,
        }

    mgr.tool_model_candidate_status = _status  # type: ignore[method-assign]

    result = mgr.resolve_tool_model(
        [("gemini", "gemini-fast"), ("openrouter", "router-tool")]
    )

    assert result["state"] == "fallback"
    assert result["effective_provider"] == "openrouter"
    assert result["reason"] == "configured_tools_unsupported"


def test_hoist_returns_empty_when_no_tool_capable_family_exists():
    mgr = _manager("gemini")
    _stub_ready(mgr, set())

    assert mgr._hoist_tool_model(list(_CHAIN)) == []
