"""Unit tests for jarvis.mcp.loader.McpToolLoader.

TDD: written before the implementation — expect RED until McpToolLoader exists.

Cases:
- expand() returns one MCPToolAdapter per MCP tool in active_clients.
- expand() returns [] when no registry is set (None from get_mcp_registry).
- expand() returns [] when active_clients() raises.
"""
from __future__ import annotations

import pytest

from jarvis.core import runtime_refs

# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------

class _FakeSpec:
    """Minimal stand-in for MCPServerSpec."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeClient:
    """Minimal stand-in for MCPClient — synchronous, no I/O."""

    def __init__(self, name: str, tool_defs: list[dict]) -> None:
        self.spec = _FakeSpec(name)
        self._tools_cache = list(tool_defs)


class _FakeRegistry:
    """Stand-in for MCPRegistry.active_clients()."""

    def __init__(self, clients: dict) -> None:
        self._clients = clients

    def active_clients(self) -> dict:
        return self._clients


class _RaisingRegistry:
    """active_clients() raises to test the safe fallback path."""

    def active_clients(self) -> dict:
        raise RuntimeError("simulated failure")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_refs():
    """Always clear runtime refs and the global CapabilityRegistry before
    and after each test.

    MCPToolAdapter registers a Capability as a side effect of construction.
    Without this teardown the singleton CapabilityRegistry accumulates test
    capabilities across tests and pollutes cross-file routing tests that rely
    on resolve_intent() returning None for generic utterances.
    """
    from jarvis.core.capabilities import _reset_registry_for_tests

    runtime_refs._reset_for_tests()
    yield
    runtime_refs._reset_for_tests()
    _reset_registry_for_tests()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_expand_returns_adapter_for_each_tool() -> None:
    """McpToolLoader.expand() returns one adapter per tool in active_clients."""
    fake_client = _FakeClient(
        "notebooklm-mcp",
        [{"name": "notebook_list", "description": "List notebooks", "inputSchema": {}}],
    )
    fake_registry = _FakeRegistry({"notebooklm-mcp": fake_client})
    runtime_refs.set_mcp_registry(fake_registry)

    from jarvis.mcp.loader import McpToolLoader

    result = McpToolLoader().expand()

    assert len(result) == 1
    assert result[0].name == "notebooklm-mcp/notebook_list"


def test_expand_returns_empty_when_no_registry() -> None:
    """expand() returns [] when runtime_refs holds no MCPRegistry (None)."""
    # _reset_refs fixture ensures get_mcp_registry() returns None.
    from jarvis.mcp.loader import McpToolLoader

    result = McpToolLoader().expand()

    assert result == []


def test_expand_returns_empty_when_active_clients_raises() -> None:
    """expand() returns [] — never propagates — when active_clients() raises."""
    runtime_refs.set_mcp_registry(_RaisingRegistry())

    from jarvis.mcp.loader import McpToolLoader

    result = McpToolLoader().expand()

    assert result == []


def test_expand_multiple_clients_and_tools() -> None:
    """expand() aggregates tools across multiple connected servers."""
    client_a = _FakeClient(
        "server-a",
        [
            {"name": "tool_one", "description": "First", "inputSchema": {}},
            {"name": "tool_two", "description": "Second", "inputSchema": {}},
        ],
    )
    client_b = _FakeClient(
        "server-b",
        [{"name": "only_tool", "description": "Only", "inputSchema": {}}],
    )
    runtime_refs.set_mcp_registry(
        _FakeRegistry({"server-a": client_a, "server-b": client_b})
    )

    from jarvis.mcp.loader import McpToolLoader

    result = McpToolLoader().expand()

    names = {t.name for t in result}
    assert names == {"server-a/tool_one", "server-a/tool_two", "server-b/only_tool"}


def test_expand_empty_active_clients() -> None:
    """expand() returns [] when the registry has no active clients."""
    runtime_refs.set_mcp_registry(_FakeRegistry({}))

    from jarvis.mcp.loader import McpToolLoader

    result = McpToolLoader().expand()

    assert result == []


def test_loader_attributes() -> None:
    """McpToolLoader must declare is_virtual_loader=True and risk_tier='block'."""
    from jarvis.mcp.loader import McpToolLoader

    loader = McpToolLoader()
    assert loader.is_virtual_loader is True
    assert loader.risk_tier == "block"
    assert loader.name == "mcp_tools_loader"
    assert isinstance(loader.schema, dict)
