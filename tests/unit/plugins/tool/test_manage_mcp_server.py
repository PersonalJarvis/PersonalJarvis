"""Unknown-name errors from manage-mcp-server must be actionable.

Forensic 2026-07-13 18:33: "reconnect the GitHub MCP" failed with a bare
"no MCP server named 'github'" — GitHub was a marketplace plugin, not an
mcp.json server — and the spoken outcome gave the user nothing to correct.
"""

from __future__ import annotations

import pytest

from jarvis.plugins.tool.manage_mcp_server import ManageMcpServerTool


class _FakeMcpState:
    def __init__(self, servers: dict | None = None) -> None:
        self._servers = servers or {}

    def load_config(self) -> dict:
        return {"mcpServers": dict(self._servers)}

    def get_server_entry(self, name: str) -> dict | None:
        return self._servers.get(name)

    def remove_server(self, name: str) -> bool:
        return self._servers.pop(name, None) is not None


@pytest.mark.asyncio
async def test_toggle_unknown_name_lists_configured_servers() -> None:
    state = _FakeMcpState({"notebooklm": {"enabled": True}, "blender": {}})
    result = await ManageMcpServerTool()._toggle(
        state, "github", enabled=True, reason="test"
    )
    assert result.success is False
    assert "github" in result.error
    assert "notebooklm" in result.error
    assert "blender" in result.error
    assert "plugin" in result.error.lower()


@pytest.mark.asyncio
async def test_remove_unknown_name_lists_configured_servers() -> None:
    state = _FakeMcpState({"notebooklm": {"enabled": True}})
    result = await ManageMcpServerTool()._remove(state, "github", "test")
    assert result.success is False
    assert "notebooklm" in result.error


@pytest.mark.asyncio
async def test_unknown_name_error_survives_a_broken_state() -> None:
    class _BrokenState:
        def load_config(self):
            raise RuntimeError("boom")

        def get_server_entry(self, _name):
            return None

    result = await ManageMcpServerTool()._toggle(
        _BrokenState(), "github", enabled=True, reason="test"
    )
    assert result.success is False
    assert "github" in result.error
