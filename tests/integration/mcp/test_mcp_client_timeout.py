"""Per-call and per-start timeout guards for the MCP layer.

A user MCP tool call can hang and block the whole voice turn because there is
no per-call timeout in the chain (tool_use_loop -> tool_executor -> adapter ->
``MCPClient.call_tool``). The circuit breaker only counted COMPLETED failures,
so a true HANG never tripped it. These tests pin two guards:

1. ``MCPClient.call_tool`` wraps the SDK call in a per-call timeout that RAISES
   on expiry; the raise lands in the existing breaker-counting ``except`` so a
   repeatedly-hung server auto-opens the circuit breaker after the threshold.
2. ``MCPRegistry`` starts each server under a per-start timeout (mirroring
   ``bootstrap.verify_server``); a server hanging on ``initialize``/``list_tools``
   is given up on without hanging the start task forever or killing siblings.

We inject fakes whose ``call_tool``/``start`` await forever, then shrink the
timeout constants via monkeypatch so the tests stay fast and deterministic
(no subprocess, no real network).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.mcp.client as client_mod
import jarvis.mcp.registry as registry_mod
from jarvis.mcp.client import MCPClient
from jarvis.mcp.registry import MCPRegistry, MCPServerSpec


def _spec(name: str = "hang-mcp") -> MCPServerSpec:
    return MCPServerSpec(
        name=name,
        display="Hang MCP",
        description="test-only",
        install_command=["python", "-m", "noop"],
    )


class _HangingSession:
    """Stand-in MCP ClientSession whose call_tool never returns."""

    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, name: str, args: dict) -> object:  # noqa: ANN401
        self.calls += 1
        await asyncio.sleep(3600)  # effectively forever -> simulates a hang
        raise AssertionError("unreachable")  # pragma: no cover


class _ServerErrorSession:
    """Return the MCP SDK's non-exceptional server-error shape."""

    async def call_tool(self, name: str, args: dict) -> object:  # noqa: ANN401
        del name, args
        return SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="server rejected the operation")],
        )


# ----------------------------------------------------------------------
# 1. Per-call timeout
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hung_call_tool_raises_after_timeout_and_counts_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Shrink the per-call timeout so the hang is cut almost immediately.
    monkeypatch.setattr(client_mod, "_CALL_TIMEOUT_S", 0.05, raising=False)

    client = MCPClient(_spec())
    client._session = _HangingSession()  # inject without a real transport

    with pytest.raises(RuntimeError) as excinfo:
        await client.call_tool("slow_tool", {"x": 1})

    # The error must be honest/clean (mentions a timeout), not a bare crash.
    assert "timeout" in str(excinfo.value).lower() or "timed out" in str(
        excinfo.value
    ).lower()
    # A hang must be COUNTED as a failure so the breaker can eventually open.
    assert client._circuit_breaker_failures == 1


@pytest.mark.asyncio
async def test_repeated_hangs_open_the_circuit_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_mod, "_CALL_TIMEOUT_S", 0.05, raising=False)

    client = MCPClient(_spec())
    session = _HangingSession()
    client._session = session

    # Threshold consecutive hangs must trip the breaker.
    for _ in range(client_mod._CB_THRESHOLD):
        with pytest.raises(RuntimeError):
            await client.call_tool("slow_tool", {})

    assert client.is_healthy is False
    # Once open, further calls are rejected WITHOUT even touching the session
    # (no new hang, no extra wait).
    calls_before = session.calls
    with pytest.raises(RuntimeError, match="circuit-breaker"):
        await client.call_tool("slow_tool", {})
    assert session.calls == calls_before


@pytest.mark.asyncio
async def test_server_error_result_counts_exactly_once() -> None:
    client = MCPClient(_spec())
    client._session = _ServerErrorSession()

    with pytest.raises(RuntimeError, match="server rejected"):
        await client.call_tool("failing_tool", {})

    assert client._circuit_breaker_failures == 1
    assert client.is_healthy is True


# ----------------------------------------------------------------------
# 2. Per-start timeout (registry)
# ----------------------------------------------------------------------

class _FakeClient:
    """Registry-level stand-in: ``hang`` blocks forever in start(), ``ok`` is fine."""

    instances: list[_FakeClient] = []

    def __init__(self, spec: MCPServerSpec, env_overrides=None) -> None:  # noqa: ANN001
        self.spec = spec
        self.started = False
        self.stopped = False
        _FakeClient.instances.append(self)

    async def start(self) -> None:
        if self.spec.name == "hang":
            await asyncio.sleep(3600)  # hang on initialize/list_tools
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_start_one_gives_up_after_timeout_without_killing_siblings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeClient.instances.clear()
    # Shrink the per-start timeout; patch the client class the local import picks up.
    monkeypatch.setattr(registry_mod, "_START_TIMEOUT_S", 0.05, raising=False)
    monkeypatch.setattr(client_mod, "MCPClient", _FakeClient)
    # Avoid touching mcp.json on disk.
    monkeypatch.setattr(registry_mod, "_env_from_mcp_json", lambda _name: None)

    reg = MCPRegistry()
    reg.register_spec(_spec("hang"))
    reg.register_spec(_spec("ok"))

    # Must RETURN promptly even though "hang" blocks forever.
    await asyncio.wait_for(reg.start_enabled(["hang", "ok"]), timeout=5)

    active = reg.active_clients()
    # The healthy sibling came up...
    assert "ok" in active
    assert active["ok"].started is True
    # ...while the hung server was given up on and recorded as an error.
    assert "hang" not in active
    err = reg.last_error("hang")
    assert err is not None
    assert "timeout" in err.lower() or "timed out" in err.lower()
