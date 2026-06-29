"""B6 (open-source AP-22): when no sub-agent provider is explicitly configured,
the heavy mission worker must default to the user's ACTIVE brain provider — NOT
the legacy Claude CLI. A fresh openrouter/gemini/codex install (which never sets
[brain.sub_jarvis].provider) otherwise routes every mission to a non-existent
`claude` binary and fails.
"""
from __future__ import annotations

from types import SimpleNamespace

import jarvis.core.config as cfg_mod


def _stub_config(monkeypatch, *, sub_provider, primary):
    stub = SimpleNamespace(
        brain=SimpleNamespace(
            sub_jarvis=SimpleNamespace(provider=sub_provider),
            primary=primary,
        )
    )
    monkeypatch.setattr(cfg_mod, "load_config", lambda: stub)
    monkeypatch.setattr(
        cfg_mod, "refresh_persisted_env_from_user_registry", lambda: None, raising=False
    )


def test_unset_subagent_provider_defaults_to_brain_primary(monkeypatch):
    _stub_config(monkeypatch, sub_provider=None, primary="openrouter")
    from jarvis.missions.init import _live_subagent_provider

    assert _live_subagent_provider(None) == "openrouter"


def test_explicit_subagent_provider_still_wins(monkeypatch):
    _stub_config(monkeypatch, sub_provider="antigravity", primary="openrouter")
    from jarvis.missions.init import _live_subagent_provider

    assert _live_subagent_provider(None) == "antigravity"


def test_defaulted_provider_routes_to_a_non_claude_worker_kind():
    # Once defaulted to the active provider, the worker kind must be that
    # provider's own worker, not the legacy "subjarvis" (= ClaudeDirectWorker).
    from jarvis.missions.init import _select_subagent_worker_kind

    assert _select_subagent_worker_kind("openrouter", "") == "api_agent"
    assert _select_subagent_worker_kind("openai", "") == "api_agent"
