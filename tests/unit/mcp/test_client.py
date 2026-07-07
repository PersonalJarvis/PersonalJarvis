"""Unit tests for jarvis/mcp/client.py — MCPClient lifecycle.

AP-23 wave-2 finding 10: a stdio MCP server whose launcher binary (typically
``npx``/``node``) is absent from PATH must fail with an actionable message
("install Node.js 18+ ...") instead of a raw ``FileNotFoundError`` string
reaching the plugin badge (caught generically at ``mcp/registry.py``).
"""
from __future__ import annotations

import shutil

import pytest

from jarvis.mcp.client import MCPClient, _stdio_launcher_missing_message
from jarvis.mcp.registry import MCPServerSpec


def _stdio_spec(command: str = "npx", name: str = "some-plugin") -> MCPServerSpec:
    return MCPServerSpec(
        name=name,
        display=name.title(),
        description="Test stdio MCP server",
        install_command=[command, "-y", "@example/server"],
        transport="stdio",
    )


# --- _stdio_launcher_missing_message (pure message shaping) -----------------


def test_missing_message_names_node_for_npx() -> None:
    msg = _stdio_launcher_missing_message("some-plugin", "npx")
    assert "Node.js" in msg
    assert "npx" in msg
    assert "some-plugin" in msg


def test_missing_message_names_node_for_node() -> None:
    msg = _stdio_launcher_missing_message("some-plugin", "node")
    assert "Node.js" in msg


def test_missing_message_names_launcher_for_non_node_command() -> None:
    msg = _stdio_launcher_missing_message("docker-plugin", "docker")
    assert "docker" in msg
    assert "docker-plugin" in msg
    # No Node.js hint for a non-Node launcher.
    assert "Node.js" not in msg


# --- MCPClient.start() — actionable failure on a missing launcher -----------


@pytest.mark.asyncio
async def test_start_raises_actionable_error_when_npx_missing(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    client = MCPClient(_stdio_spec(command="npx", name="brave-search"))
    with pytest.raises(FileNotFoundError) as excinfo:
        await client.start()
    message = str(excinfo.value)
    # The actionable hint, not a raw errno string like
    # "[Errno 2] No such file or directory: 'npx'".
    assert "Node.js" in message
    assert "18" in message
    assert "brave-search" in message
    assert "Errno" not in message


@pytest.mark.asyncio
async def test_start_actionable_error_is_readable_via_registry_style_format(
    monkeypatch,
) -> None:
    """Mirrors how mcp/registry.py's start_enabled formats the failure:
    ``f"{type(e).__name__}: {e}"``. Must read as an instruction, not a raw
    OS error line.
    """
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    client = MCPClient(_stdio_spec(command="npx", name="brave-search"))
    try:
        await client.start()
        pytest.fail("expected start() to raise")
    except Exception as e:  # noqa: BLE001 — mirrors registry.py's catch-all
        formatted = f"{type(e).__name__}: {e}"
    assert "install Node.js 18+" in formatted


@pytest.mark.asyncio
async def test_start_does_not_which_check_when_launcher_present(monkeypatch) -> None:
    """When the launcher IS on PATH, the which()-check must not block startup
    — this test only proves the guard doesn't misfire on a present binary; the
    real transport handshake is exercised by higher-level/integration tests."""
    monkeypatch.setattr(shutil, "which", lambda _cmd: r"C:\fake\npx.cmd")

    class _BoomStdioClient:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            raise RuntimeError("boom past the which()-check, as expected")

        async def __aexit__(self, *_exc):
            return False

    import mcp.client.stdio as stdio_mod

    monkeypatch.setattr(stdio_mod, "stdio_client", lambda params: _BoomStdioClient())

    client = MCPClient(_stdio_spec(command="npx", name="brave-search"))
    with pytest.raises(RuntimeError, match="boom past the which"):
        await client.start()
