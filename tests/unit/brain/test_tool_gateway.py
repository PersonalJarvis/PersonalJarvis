"""Public supervisor tool gateway catalog and execution contracts."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core.protocols import SupervisorToolRequest, ToolResult


@dataclass
class _Tool:
    name: str
    description: str = "Gateway test tool."
    schema: dict[str, Any] | None = None
    risk_tier: str = "monitor"

    def __post_init__(self) -> None:
        if self.schema is None:
            self.schema = {"type": "object", "properties": {}}

    async def execute(self, _args: dict[str, Any], _ctx: Any) -> ToolResult:
        raise AssertionError("The gateway must use ToolExecutor")


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[_Tool, dict[str, Any], dict[str, Any]]] = []
        self.confirmed: list[tuple[Any, dict[str, Any]]] = []
        self.cancelled: list[Any] = []
        self.denied: list[tuple[str, str, Any]] = []

    async def execute(
        self,
        tool: _Tool,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> ToolResult:
        self.calls.append((tool, arguments, kwargs))
        return ToolResult(success=True, output=tool.name)

    async def execute_confirmed(self, trace_id: Any, **kwargs: Any) -> ToolResult:
        self.confirmed.append((trace_id, kwargs))
        return ToolResult(success=True, output="confirmed")

    async def cancel_pending(self, trace_id: Any) -> bool:
        self.cancelled.append(trace_id)
        return True

    async def publish_guard_denied(
        self,
        name: str,
        reason: str,
        *,
        trace_id: Any,
    ) -> None:
        self.denied.append((name, reason, trace_id))


def _request() -> SupervisorToolRequest:
    return SupervisorToolRequest(
        trace_id=uuid4(),
        origin="test",
        user_utterance="Use the connected tool.",
    )


def test_catalog_is_secret_free_dynamic_and_versioned() -> None:
    tools = {"github/list": _Tool("github/list")}
    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools=tools, _tool_executor=_Executor())
    )

    first = gateway.catalog()
    first_version = gateway.catalog_version
    tools["github/get"] = _Tool("github/get")
    second = gateway.catalog()

    assert [item.name for item in first] == ["github/list"]
    assert [item.name for item in second] == ["github/get", "github/list"]
    assert gateway.catalog_version > first_version
    assert not hasattr(first[0], "execute")


@pytest.mark.asyncio
async def test_execute_resolves_the_current_tool_through_executor() -> None:
    executor = _Executor()
    original = _Tool("github/list")
    replacement = _Tool("github/list", description="Replacement")
    tools = {original.name: original}
    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools=tools, _tool_executor=executor)
    )
    tools[original.name] = replacement

    result = await gateway.execute(original.name, {"state": "open"}, _request())

    assert result.success is True
    assert executor.calls[0][0] is replacement
    assert executor.calls[0][1] == {"state": "open"}


@pytest.mark.asyncio
async def test_execute_fails_closed_after_tool_removal() -> None:
    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools={}, _tool_executor=_Executor())
    )

    result = await gateway.execute("github/list", {}, _request())

    assert result.success is False
    assert "no longer available" in str(result.error)


@pytest.mark.asyncio
async def test_confirmation_and_denial_operations_stay_behind_gateway() -> None:
    executor = _Executor()
    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools={}, _tool_executor=executor)
    )
    request = _request()

    confirmed = await gateway.execute_confirmed(request.trace_id, request)
    cancelled = await gateway.cancel_pending(request.trace_id)
    await gateway.publish_guard_denied(
        "gmail",
        "blocked for test",
        trace_id=request.trace_id,
    )

    assert confirmed.success is True
    assert cancelled is True
    assert executor.confirmed[0][0] == request.trace_id
    assert executor.cancelled == [request.trace_id]
    assert executor.denied == [
        ("gmail", "blocked for test", request.trace_id)
    ]
