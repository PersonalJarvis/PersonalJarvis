"""Provider-parity guards for the restricted mission capability inventory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.capabilities import (
    WorkerCapabilityInventory,
    restricted_worker_app_commands,
    restricted_worker_knowledge_tools,
)
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from jarvis.missions.workers.codex_direct_worker import (
    CodexDirectWorker,
    _build_codex_direct_cmd,
)
from jarvis.missions.workers.gemini_worker import (
    GeminiWorker,
    _build_isolated_gemini_env,
)
from jarvis.missions.workers.google_cli_worker import GoogleCliWorker


def _inventory() -> WorkerCapabilityInventory:
    return WorkerCapabilityInventory.build(
        mcp_servers={
            "notes": {
                "command": "notes-mcp",
                "env": {"ACCESS_TOKEN": "secret-value"},
            }
        },
        app_commands=("session-latest-turn", "wiki-ingest"),
    )


def test_every_backend_receives_the_same_restricted_inventory() -> None:
    inventory = _inventory()
    workers = (
        ClaudeDirectWorker(capability_inventory=inventory),
        CodexDirectWorker(capability_inventory=inventory),
        GeminiWorker(capability_inventory=inventory),
        GoogleCliWorker(capability_inventory=inventory),
        ApiAgentWorker("openrouter", capability_inventory=inventory),
    )

    assert all(worker.capability_inventory is inventory for worker in workers)
    assert workers[3]._gemini_fallback.capability_inventory is inventory
    assert "secret-value" not in repr(inventory)


def test_backend_reports_are_honest_when_supervisor_grant_is_unavailable() -> None:
    inventory = _inventory()

    for backend in (
        "claude-cli",
        "codex-cli",
        "gemini-cli",
        "google-cli",
        "api:openrouter",
    ):
        report = inventory.report_for(backend)
        assert report["broker"]["status"] == "unavailable"
        assert report["mcp"]["status"] == "unavailable"
        assert report["app_commands"]["status"] == "unavailable"
        assert "secret-value" not in json.dumps(report)


def test_recursive_tools_are_rejected_from_worker_inventory() -> None:
    try:
        WorkerCapabilityInventory.build(app_commands=("spawn-worker",))
    except ValueError as exc:
        assert "recursive tools" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("spawn-worker entered a worker capability inventory")


def test_registry_drives_the_restricted_worker_command_surface() -> None:
    commands = set(restricted_worker_app_commands())

    assert {
        "providers-list",
        "provider-test",
        "wake-word-get",
        "audio-devices-list",
        "wiki-ingest",
        "ultrawiki-ask",
        "session-latest-turn",
        "tools-list",
        "missions-list",
        "mission-result",
        "tasks-list",
    } == commands
    assert "brain-switch" not in commands
    assert "wake-word-set" not in commands
    assert "app-restart" not in commands


def test_config_command_is_rejected_from_worker_inventory() -> None:
    with pytest.raises(ValueError, match="not allowed for mission workers"):
        WorkerCapabilityInventory.build(app_commands=("brain-switch",))


def test_worker_knowledge_surface_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """All gates off → the wiki triple plus the always-on research pair
    (ADR-0030). Control surfaces and the excluded tools stay out."""
    from jarvis.missions.workers import capabilities

    monkeypatch.setattr(capabilities, "_awareness_recall_available", lambda: False)
    monkeypatch.setattr(capabilities, "_ultrawiki_worker_tool_available", lambda: False)
    tools = set(restricted_worker_knowledge_tools())

    assert tools == {
        "wiki-list",
        "wiki-recall",
        "wiki-page-read",
        "search_web",
        "contact-lookup",
    }
    assert "computer_use" not in tools
    assert "run_shell" not in tools
    # Deliberate ADR-0030 exclusions: live-desktop read, unattended write.
    assert "awareness-snapshot" not in tools
    assert "contact-upsert" not in tools
    assert not any(name.startswith("cli_") for name in tools)


def test_awareness_gate_adds_session_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.missions.workers import capabilities

    monkeypatch.setattr(capabilities, "_awareness_recall_available", lambda: True)
    monkeypatch.setattr(capabilities, "_ultrawiki_worker_tool_available", lambda: False)

    assert "awareness-recall" in restricted_worker_knowledge_tools()


def test_ultrawiki_gate_requires_mode_and_live_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ultrawiki-search grant is honest: only when UltraWiki mode is ON
    and the service is live does the name enter a grant — a phantom tool
    must never reach a worker (ADR-0030)."""
    from types import SimpleNamespace

    from jarvis.core import runtime_refs
    from jarvis.missions.workers import capabilities

    # Test environment: no live service (and default mode off) → closed.
    assert capabilities._ultrawiki_worker_tool_available() is False
    assert "ultrawiki-search" not in restricted_worker_knowledge_tools()

    # Mode enabled + live service on the web-app state → granted.
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(enabled=True))
    monkeypatch.setattr("jarvis.core.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(
        runtime_refs,
        "get_web_app",
        lambda: SimpleNamespace(state=SimpleNamespace(ultrawiki=object())),
    )
    assert capabilities._ultrawiki_worker_tool_available() is True
    assert "ultrawiki-search" in restricted_worker_knowledge_tools()

    # Mode enabled but the service is absent → still closed.
    monkeypatch.setattr(runtime_refs, "get_web_app", lambda: None)
    assert capabilities._ultrawiki_worker_tool_available() is False


def test_every_granted_knowledge_tool_passes_the_broker_denylist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.missions.workers import capabilities
    from jarvis.missions.workers.worker_tool_broker import worker_tool_name_allowed

    monkeypatch.setattr(capabilities, "_awareness_recall_available", lambda: True)
    monkeypatch.setattr(capabilities, "_ultrawiki_worker_tool_available", lambda: True)

    for name in restricted_worker_knowledge_tools():
        assert worker_tool_name_allowed(name), name


def test_mission_inventory_combines_wiki_reads_with_relevant_connectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.missions import init as missions_init
    from jarvis.missions.workers import capabilities

    monkeypatch.setattr(capabilities, "_awareness_recall_available", lambda: False)
    monkeypatch.setattr(capabilities, "_ultrawiki_worker_tool_available", lambda: False)
    monkeypatch.setattr(
        missions_init,
        "_assemble_worker_mcp_servers",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        missions_init,
        "_connected_native_worker_tools",
        lambda _task_text: ("gmail",),
    )

    inventory = missions_init._assemble_worker_capability_inventory(
        "Review the Wiki and my relevant email."
    )

    assert set(inventory.native_tool_names) == {
        "wiki-list",
        "wiki-recall",
        "wiki-page-read",
        "search_web",
        "contact-lookup",
        "gmail",
    }


def test_codex_worker_ignores_machine_global_config(tmp_path: Path) -> None:
    cmd = _build_codex_direct_cmd(worktree=tmp_path / "worktree", model=None)
    assert "--ignore-user-config" in cmd


def test_gemini_worker_restricts_tools_without_hiding_auth_home(tmp_path: Path) -> None:
    original = {
        "HOME": "/real/home",
        "GEMINI_CLI_HOME": "/real/gemini-auth",
        "GEMINI_API_KEY": "key",
    }
    restricted, settings_path, no_mcp_server = _build_isolated_gemini_env(
        original, log_dir=tmp_path
    )

    assert original["HOME"] == "/real/home"
    assert restricted["HOME"] == "/real/home"
    assert restricted["GEMINI_CLI_HOME"] == "/real/gemini-auth"
    assert restricted["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] == str(settings_path)
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "admin" not in settings
    assert settings["mcp"]["allowed"] == [no_mcp_server]
    assert no_mcp_server.startswith("jarvis-no-mcp-")
    assert settings["security"]["allowedExtensions"] == ["(?!)"]
    assert settings["hooksConfig"]["enabled"] is False
