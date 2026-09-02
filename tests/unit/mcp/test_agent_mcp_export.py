"""Connecting the Agent MCP surface to an outside client's own config.

Writing into somebody else's config file is the one operation here that can
destroy work that is not ours, so the properties that matter are: other servers
survive, a half-written file is impossible, and a hand-formatted config is
never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.mcp.agents import export


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory of our own, so no real client config is ever opened."""
    monkeypatch.setattr(export, "_home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


def test_every_target_has_a_path_on_every_os(fake_home: Path) -> None:
    """A client we claim to support must resolve somewhere on this machine."""
    rows = export.targets()
    assert {t.key for t in rows} == {"claude-desktop", "cursor", "claude-code", "codex"}
    for target in rows:
        assert target.config_path is not None, target.key
        assert target.config_path.is_absolute(), target.key


def test_stdio_entry_launches_the_bridge_and_holds_no_key(fake_home: Path) -> None:
    entry = export.stdio_entry()
    assert entry["args"] == ["-m", "jarvis.mcp.agents.bridge"]
    assert entry["command"], "the entry must name an interpreter"
    # AP-12: a key in a config file is a key in a backup.
    assert "JARVIS_CONTROL_KEY" not in entry["env"]
    assert entry["env"]["JARVIS_API_URL"].startswith("http")


def test_http_entry_points_at_the_agent_surface(fake_home: Path) -> None:
    entry = export.http_entry()
    assert entry["type"] == "http"
    assert entry["url"].endswith("/api/control/mcp/agents")
    assert entry["headers"]["Authorization"].startswith("Bearer ")


def test_install_keeps_every_other_server(fake_home: Path) -> None:
    path = fake_home / ".cursor" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"mcpServers": {"blender": {"command": "blender-mcp"}}}),
        encoding="utf-8",
    )

    result = export.install("cursor")

    assert result["ok"] is True
    assert result["replaced"] is False
    written = json.loads(path.read_text(encoding="utf-8"))
    assert set(written["mcpServers"]) == {"blender", "jarvis-agents"}
    assert written["mcpServers"]["blender"] == {"command": "blender-mcp"}


def test_install_is_idempotent(fake_home: Path) -> None:
    (fake_home / ".cursor").mkdir(parents=True)
    first = export.install("cursor")
    second = export.install("cursor")

    assert first["replaced"] is False
    assert second["replaced"] is True
    written = json.loads((fake_home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert list(written["mcpServers"]) == ["jarvis-agents"]
    # No stray temp file left behind by the atomic write.
    assert not list((fake_home / ".cursor").glob("*.jarvis-tmp"))


def test_a_broken_config_is_reported_not_overwritten(fake_home: Path) -> None:
    path = fake_home / ".cursor" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")

    result = export.install("cursor")

    assert result["ok"] is False
    assert "not readable JSON" in result["error"]
    assert path.read_text(encoding="utf-8") == "{ not json", "the file must survive untouched"


def test_a_toml_client_gets_a_snippet_instead_of_a_write(fake_home: Path) -> None:
    (fake_home / ".codex").mkdir(parents=True)
    result = export.install("codex")

    assert result["ok"] is False
    assert "[mcp_servers.jarvis-agents]" in result["snippet"]
    assert not (fake_home / ".codex" / "config.toml").exists()


def test_an_unknown_client_names_the_known_ones(fake_home: Path) -> None:
    result = export.install("emacs")
    assert result["ok"] is False
    assert "cursor" in result["error"]


def test_snippet_is_valid_json_for_nested_clients(fake_home: Path) -> None:
    parsed = json.loads(export.snippet("claude-desktop"))
    assert list(parsed["mcpServers"]) == ["jarvis-agents"]
