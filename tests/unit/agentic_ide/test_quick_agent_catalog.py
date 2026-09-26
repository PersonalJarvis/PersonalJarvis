"""Workspace launch choices never wait for CLI version or model subprocesses."""

from __future__ import annotations

import threading

import pytest

from jarvis.ui.web import agentic_ide_routes as routes
from jarvis.workspace import agents, launch_picks


@pytest.mark.parametrize("pty_available", [False, True])
async def test_quick_catalog_resolves_off_loop_without_expensive_probes(
    monkeypatch: pytest.MonkeyPatch,
    pty_available: bool,
) -> None:
    specs = [
        agents.WorkspaceAgent(
            name="example",
            display_name="Example Agent",
            description="A custom coding agent",
            custom=True,
            logo_url="/logo.svg",
            accepts_typed_prompts=False,
        ),
        agents.WorkspaceAgent(name="missing", display_name="Missing Agent"),
    ]
    by_name = {spec.name: spec for spec in specs}
    monkeypatch.setattr(agents, "coding_agents", lambda: specs)
    monkeypatch.setattr(agents, "get_agent", by_name.get)
    monkeypatch.setattr(agents, "pty_available", lambda: pty_available)

    async def forbidden_probe():
        pytest.fail("The quick catalog must not execute CLI version/model probes")

    monkeypatch.setattr(agents, "detect_agents", forbidden_probe)
    monkeypatch.setattr(launch_picks, "live_models", forbidden_probe)
    event_loop_thread = threading.get_ident()
    resolved = []

    def resolve_agent(name):
        assert threading.get_ident() != event_loop_thread
        resolved.append(name)
        return ("example",) if name == "example" else None

    monkeypatch.setattr(routes, "agent_argv", resolve_agent)
    result = await routes.get_agents(quick=True)
    assert resolved == ["example", "missing"]
    assert result.terminal_available is pty_available
    assert result.max_terminals == 8
    assert len(result.suggested_names) == 8
    example, missing = result.agents
    assert example.installed is True
    assert missing.installed is False
    assert example.version is None
    assert example.install_command is None
    assert example.kind == "cli"
    assert example.description == "A custom coding agent"
    assert example.custom is True
    assert example.logo_url == "/logo.svg"
    assert example.accepts_prompts is False
    assert missing.accepts_prompts is True
