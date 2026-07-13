"""Lifecycle regressions for mission-scoped worker tool grants."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.core.protocols import ToolResult
from jarvis.missions.workers import worker_tool_broker as broker_module
from jarvis.missions.workers.worker_tool_broker import WorkerToolBroker
from jarvis.safety import tool_executor as _tool_executor  # noqa: F401


@dataclass
class _Tool:
    name: str
    description: str = "Lifecycle test tool."
    schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.schema is None:
            self.schema = {"type": "object", "properties": {}}

    async def execute(self, _args: dict[str, Any], _ctx: Any) -> ToolResult:
        raise AssertionError("The broker must execute tools only through ToolExecutor")


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(
        self,
        tool: _Tool,
        _arguments: dict[str, Any],
        **_kwargs: Any,
    ) -> ToolResult:
        self.calls.append(tool.name)
        return ToolResult(success=True, output={"called": tool.name})


class _BlockingExecutor(_RecordingExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(
        self,
        tool: _Tool,
        _arguments: dict[str, Any],
        **_kwargs: Any,
    ) -> ToolResult:
        self.calls.append(tool.name)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ToolResult(success=True, output={"called": tool.name})


@pytest.fixture(autouse=True)
def _clean_runtime_refs() -> Any:
    runtime_refs._reset_for_tests()
    yield
    runtime_refs._reset_for_tests()


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> WorkerToolBroker:
    """Build a broker without opening a real loopback listener."""
    instance = WorkerToolBroker()
    server = SimpleNamespace(server_address=("127.0.0.1", 1))
    monkeypatch.setattr(instance, "_ensure_server", lambda: server)
    return instance


def _wire_manager(
    executor: _RecordingExecutor,
    tools: dict[str, _Tool],
) -> SimpleNamespace:
    manager = SimpleNamespace(_tool_executor=executor, _tools=tools)
    runtime_refs.set_brain_manager(manager)
    runtime_refs.set_supervisor_tool_gateway(BrainSupervisorToolGateway(manager))
    return manager


def _issue(
    broker: WorkerToolBroker,
    *,
    ttl_s: float = 30.0,
    executor: _RecordingExecutor | None = None,
    tools: dict[str, _Tool] | None = None,
):  # noqa: ANN202 - the concrete binding type is an implementation detail
    active_executor = executor or _RecordingExecutor()
    active_tools = tools or {"github/list_issues": _Tool("github/list_issues")}
    manager = _wire_manager(active_executor, active_tools)
    binding = broker.issue(
        task_text="Inspect the connected GitHub project.",
        mcp_server_ids=("github",),
        app_commands=(),
        native_tool_names=(),
        ttl_s=ttl_s,
    )
    assert binding is not None
    return binding, manager, active_executor


@pytest.mark.asyncio
async def test_binding_available_becomes_false_after_ttl_without_registry_lookup(
    broker: WorkerToolBroker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(
        broker_module,
        "time",
        SimpleNamespace(monotonic=lambda: now[0]),
    )
    binding, _manager, _executor = _issue(broker, ttl_s=1.0)

    now[0] = 102.0

    assert binding.available is False
    assert binding.mcp_server_config() == {}
    assert broker_module.BROKER_TOKEN_ENV not in binding.apply_environment({})


@pytest.mark.asyncio
async def test_direct_binding_execution_rejects_an_expired_grant(
    broker: WorkerToolBroker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [200.0]
    monkeypatch.setattr(
        broker_module,
        "time",
        SimpleNamespace(monotonic=lambda: now[0]),
    )
    binding, _manager, executor = _issue(broker, ttl_s=1.0)

    now[0] = 202.0
    result = await binding.execute("github/list_issues", {})

    assert result["status"] == "denied"
    assert result["success"] is False
    assert executor.calls == []


@pytest.mark.asyncio
async def test_scope_resolved_before_revoke_cannot_start_a_tool_call(
    broker: WorkerToolBroker,
) -> None:
    binding, _manager, executor = _issue(broker)
    resolved_scope = broker.lookup(binding._token)
    assert resolved_scope is not None

    broker.revoke(binding._token)
    result = await resolved_scope.execute("github/list_issues", {})

    assert result["status"] == "denied"
    assert result["success"] is False
    assert executor.calls == []


@pytest.mark.asyncio
async def test_closing_binding_cancels_or_rejects_an_in_flight_call(
    broker: WorkerToolBroker,
) -> None:
    executor = _BlockingExecutor()
    binding, _manager, _executor = _issue(broker, executor=executor)
    call = asyncio.create_task(binding.execute("github/list_issues", {}))
    await asyncio.wait_for(executor.started.wait(), timeout=1.0)

    try:
        aclose = getattr(binding, "aclose", None)
        if callable(aclose):
            await aclose()
        else:
            binding.close()
            await asyncio.sleep(0)

        if not call.done():
            executor.release.set()
        try:
            result = await asyncio.wait_for(call, timeout=1.0)
        except asyncio.CancelledError:
            result = None

        assert executor.cancelled.is_set() or (
            result is not None
            and result["success"] is False
            and result["status"] == "denied"
        )
        assert binding.execution_summary.clean is False
        assert binding.execution_summary.calls[-1].status == "outcome_unknown"
    finally:
        executor.release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)


@pytest.mark.asyncio
async def test_catalog_and_execution_drop_a_tool_removed_from_live_manager(
    broker: WorkerToolBroker,
) -> None:
    tools = {
        "github/list_issues": _Tool("github/list_issues"),
        "github/get_issue": _Tool("github/get_issue"),
    }
    binding, manager, executor = _issue(broker, tools=tools)
    assert set(binding.tool_names) == set(tools)

    del manager._tools["github/get_issue"]

    assert set(binding.tool_names) == {"github/list_issues"}
    result = await binding.execute("github/get_issue", {})
    assert result["status"] == "denied"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_catalog_discovers_new_live_tool_matching_original_source_grant(
    broker: WorkerToolBroker,
) -> None:
    binding, manager, executor = _issue(broker)

    manager._tools["github/get_issue"] = _Tool("github/get_issue")

    assert set(binding.tool_names) == {"github/list_issues", "github/get_issue"}
    result = await binding.execute("github/get_issue", {})
    assert result["success"] is True
    assert executor.calls == ["github/get_issue"]


@pytest.mark.asyncio
async def test_namespaced_recursive_tools_never_enter_a_worker_grant(
    broker: WorkerToolBroker,
) -> None:
    recursive = {
        "github/dispatch-with-review",
        "github/multi_spawn",
        "github/run-skill",
        "github/spawn_worker",
    }
    tools = {name: _Tool(name) for name in recursive}
    tools["github/list_issues"] = _Tool("github/list_issues")

    binding, _manager, _executor = _issue(broker, tools=tools)

    assert set(binding.tool_names) == {"github/list_issues"}
    for name in recursive:
        result = await binding.execute(name, {})
        assert result["status"] == "denied"


@pytest.mark.asyncio
async def test_dynamic_catalog_filters_recursive_tools_added_after_issue(
    broker: WorkerToolBroker,
) -> None:
    binding, manager, _executor = _issue(broker)

    manager._tools["github/get_issue"] = _Tool("github/get_issue")
    manager._tools["github/spawn-worker"] = _Tool("github/spawn-worker")

    assert set(binding.tool_names) == {"github/list_issues", "github/get_issue"}
