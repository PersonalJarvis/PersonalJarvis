"""Tool-surface self-heal (jarvis/brain/tool_surface.py).

Live 2026-07-13: the plugin bootstrap wedged on one dead plugin, so the github
MCP's 37 tools only reached the brain when their connect happened to beat the
boot's last unrelated BrainToolsChanged refresh — "tool not available" in one
session, working in the next. The fingerprint reconcile makes the manager's
tool snapshot converge at the next turn even when the upstream event is lost.
"""
from __future__ import annotations

import pytest

from jarvis.brain.tool_surface import (
    live_tool_surface_fingerprint,
    maybe_reconcile_tool_surface,
    stamp_tool_surface,
)


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeToolRegistry:
    """Stands in for CliToolRegistry / PluginToolRegistry (active_tools())."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def active_tools(self) -> list[_NamedTool]:
        return [_NamedTool(n) for n in self._names]


class _FakeMcpClient:
    def __init__(self, tool_names: list[str]) -> None:
        self._tools_cache = [{"name": n} for n in tool_names]


class _FakeMcpRegistry:
    def __init__(self, clients: dict[str, _FakeMcpClient]) -> None:
        self._clients = clients

    def active_clients(self) -> dict[str, _FakeMcpClient]:
        return dict(self._clients)


class _FakeManager:
    """Only what the reconcile touches: _tool_surface_fp + refresh_tools()."""

    def __init__(self) -> None:
        self.refresh_calls = 0

    def refresh_tools(self) -> None:
        self.refresh_calls += 1


def _wire_sources(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cli: object = None,
    plugin: object = None,
    mcp: object = None,
) -> None:
    monkeypatch.setattr("jarvis.clis.shared.get_active_registry", lambda: cli)
    monkeypatch.setattr(
        "jarvis.marketplace.plugin_shared.get_active_plugin_registry", lambda: plugin
    )
    monkeypatch.setattr("jarvis.core.runtime_refs.get_mcp_registry", lambda: mcp)


def test_fingerprint_none_when_no_source_registry_reachable(monkeypatch):
    _wire_sources(monkeypatch)  # all three absent
    assert live_tool_surface_fingerprint() is None


def test_fingerprint_collects_all_three_sources_with_prefixes(monkeypatch):
    _wire_sources(
        monkeypatch,
        cli=_FakeToolRegistry(["cli_gh"]),
        plugin=_FakeToolRegistry(["github/search_issues"]),
        mcp=_FakeMcpRegistry({"notebooklm": _FakeMcpClient(["notebook_query"])}),
    )
    fp = live_tool_surface_fingerprint()
    assert fp == frozenset(
        {
            "cli:cli_gh",
            "plugin:github/search_issues",
            "mcp:notebooklm:notebook_query",
        }
    )


def test_one_broken_source_does_not_blind_the_others(monkeypatch):
    def _boom():
        raise RuntimeError("registry exploded")

    monkeypatch.setattr("jarvis.clis.shared.get_active_registry", _boom)
    monkeypatch.setattr(
        "jarvis.marketplace.plugin_shared.get_active_plugin_registry",
        lambda: _FakeToolRegistry(["github/search_issues"]),
    )
    monkeypatch.setattr("jarvis.core.runtime_refs.get_mcp_registry", lambda: None)
    fp = live_tool_surface_fingerprint()
    assert fp == frozenset({"plugin:github/search_issues"})


def test_reconcile_adopts_first_fingerprint_without_refresh(monkeypatch):
    _wire_sources(monkeypatch, cli=_FakeToolRegistry(["cli_gh"]))
    mgr = _FakeManager()
    maybe_reconcile_tool_surface(mgr)
    assert mgr.refresh_calls == 0
    assert mgr._tool_surface_fp == frozenset({"cli:cli_gh"})


def test_reconcile_noop_when_surface_unchanged(monkeypatch):
    _wire_sources(monkeypatch, cli=_FakeToolRegistry(["cli_gh"]))
    mgr = _FakeManager()
    mgr._tool_surface_fp = frozenset({"cli:cli_gh"})
    maybe_reconcile_tool_surface(mgr)
    assert mgr.refresh_calls == 0


def test_reconcile_refreshes_on_drift_and_stamps(monkeypatch):
    """The github-boot-race shape: a source appears after the last stamp."""
    _wire_sources(
        monkeypatch,
        cli=_FakeToolRegistry(["cli_gh"]),
        plugin=_FakeToolRegistry(["github/search_issues"]),
    )
    mgr = _FakeManager()
    mgr._tool_surface_fp = frozenset({"cli:cli_gh"})  # github missing at load
    maybe_reconcile_tool_surface(mgr)
    assert mgr.refresh_calls == 1
    assert "plugin:github/search_issues" in mgr._tool_surface_fp


def test_reconcile_skips_when_no_sources_reachable(monkeypatch):
    _wire_sources(monkeypatch)
    mgr = _FakeManager()
    maybe_reconcile_tool_surface(mgr)
    assert mgr.refresh_calls == 0
    assert not hasattr(mgr, "_tool_surface_fp")


def test_reconcile_survives_refresh_failure(monkeypatch):
    _wire_sources(monkeypatch, cli=_FakeToolRegistry(["cli_gh", "cli_docker"]))

    class _ExplodingManager(_FakeManager):
        def refresh_tools(self) -> None:
            raise RuntimeError("factory rebuild failed")

    mgr = _ExplodingManager()
    mgr._tool_surface_fp = frozenset({"cli:cli_gh"})
    maybe_reconcile_tool_surface(mgr)  # must not raise (never break a turn)
    # Stamped before the refresh attempt: no warning storm on later turns.
    assert mgr._tool_surface_fp == frozenset({"cli:cli_gh", "cli:cli_docker"})


def test_stamp_records_current_surface(monkeypatch):
    _wire_sources(monkeypatch, cli=_FakeToolRegistry(["cli_gh"]))
    mgr = _FakeManager()
    stamp_tool_surface(mgr)
    assert mgr._tool_surface_fp == frozenset({"cli:cli_gh"})
