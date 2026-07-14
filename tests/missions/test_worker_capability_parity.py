"""Provider-parity guards for the restricted mission capability inventory."""

from __future__ import annotations

import json
from pathlib import Path

from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.capabilities import WorkerCapabilityInventory
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
