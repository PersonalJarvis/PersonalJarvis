"""Integration-Tests für MCPClient + MCPToolAdapter gegen FakeMCPServer.

Der FakeMCPServer (`fake_mcp_server.py`) läuft als echtes Subprocess
und spricht MCP via stdio — so testen wir den tatsächlichen
Protokoll-Pfad inklusive Session-Initialisierung und Tool-Call.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core.protocols import ExecutionContext, Tool
from jarvis.mcp.adapter import MCPToolAdapter
from jarvis.mcp.client import MCPClient
from jarvis.mcp.registry import MCPServerSpec

FAKE_SERVER = Path(__file__).parent / "fake_mcp_server.py"


def _make_spec(mode: str = "ok") -> MCPServerSpec:
    """Baut eine Spec, die den FakeMCPServer als Subprocess startet."""
    return MCPServerSpec(
        name=f"fake-mcp-{mode}",
        display="Fake MCP",
        description="Test-only stdio MCP server",
        install_command=[sys.executable, str(FAKE_SERVER)],
    )


def _client(mode: str = "ok") -> MCPClient:
    spec = _make_spec(mode)
    return MCPClient(spec, env_overrides={"FAKE_MCP_MODE": mode})


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
    )


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_start_and_list_tools() -> None:
    client = _client("ok")
    try:
        await client.start()
        tools = await client.list_tools()
        names = [t["name"] for t in tools]
        assert "echo" in names
        assert client.is_healthy is True
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_client_call_tool_success() -> None:
    client = _client("ok")
    try:
        await client.start()
        result = await client.call_tool("echo", {"msg": "hello"})
        # raw = CallToolResult — serialize via adapter
        adapter = MCPToolAdapter(client, {"name": "echo"})
        out = await adapter.execute({"msg": "hello"}, _ctx())
        assert out.success is True
        # The echo tool returns "echoed:hello"; content serialization gives us
        # that string (FastMCP wraps it as TextContent)
        assert "echoed:hello" in str(out.output)
        assert result is not None
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_3_failures() -> None:
    client = _client("fail")
    try:
        await client.start()
        # Drei Fehler hintereinander provozieren
        for _ in range(3):
            with pytest.raises(Exception):
                await client.call_tool("echo", {"msg": "boom"})
        # Nach 3 Fails muss der Circuit-Breaker offen sein
        assert client.is_healthy is False
        # Weitere calls werden ohne SDK-Kontakt abgewiesen
        with pytest.raises(RuntimeError, match="circuit-breaker"):
            await client.call_tool("echo", {"msg": "still-bad"})
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_adapter_implements_tool_protocol() -> None:
    client = _client("ok")
    try:
        await client.start()
        tools = await client.list_tools()
        adapter = MCPToolAdapter(client, tools[0])
        # runtime_checkable Protocol — structural check
        assert isinstance(adapter, Tool)
        assert adapter.name.startswith("fake-mcp-ok/")
        assert adapter.risk_tier == "monitor"
        assert isinstance(adapter.schema, dict)
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_adapter_execute_returns_toolresult_on_failure() -> None:
    client = _client("fail")
    try:
        await client.start()
        adapter = MCPToolAdapter(client, {"name": "echo"})
        result = await adapter.execute({"msg": "x"}, _ctx())
        assert result.success is False
        assert result.error is not None
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_call_tool_before_start_raises() -> None:
    client = _client("ok")
    with pytest.raises(RuntimeError, match="not started"):
        await client.call_tool("echo", {"msg": "hi"})
