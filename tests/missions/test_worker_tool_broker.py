"""Contract tests for the mission-scoped supervisor tool broker."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionApprovalRequired, ActionApproved, ActionDenied
from jarvis.core.protocols import ToolResult
from jarvis.mcp.client import MCPClient
from jarvis.mcp.registry import MCPServerSpec
from jarvis.missions.init import _connected_native_worker_tools
from jarvis.missions.workers import broker_stdio
from jarvis.missions.workers.capabilities import WorkerCapabilityInventory
from jarvis.missions.workers.codex_direct_worker import _build_codex_direct_cmd
from jarvis.missions.workers.gemini_worker import _build_isolated_gemini_env
from jarvis.missions.workers.worker_tool_broker import (
    _BROKER,
    BROKER_TOKEN_ENV,
    BROKER_URL_ENV,
)
from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor


@dataclass
class _Tool:
    name: str
    risk_tier: str = "monitor"
    description: str = "Test connector tool."
    schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.schema is None:
            self.schema = {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            }

    async def execute(self, _args: dict[str, Any], _ctx: Any) -> ToolResult:
        raise AssertionError("The broker must never call Tool.execute directly")


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def execute(self, tool: _Tool, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        self.calls.append((tool.name, args, kwargs))
        return ToolResult(success=True, output={"called": tool.name, "args": args})


class _ExecutingAskTool(_Tool):
    def __init__(self) -> None:
        super().__init__("gmail", risk_tier="ask")
        self.calls = 0

    async def execute(self, args: dict[str, Any], _ctx: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, output={"sent": args["value"]})


@pytest.fixture(autouse=True)
def _clean_runtime_refs() -> Any:
    runtime_refs._reset_for_tests()
    _BROKER.reset_for_tests()
    yield
    runtime_refs._reset_for_tests()
    _BROKER.reset_for_tests()


def _wire_manager(executor: _Executor) -> None:
    manager = type(
        "Manager",
        (),
        {
            "_tool_executor": executor,
            "_tools": {
                "github/list_issues": _Tool("github/list_issues"),
                "github/read_secret": _Tool("github/read_secret"),
                "wiki-ingest": _Tool("wiki-ingest"),
                "gmail": _Tool("gmail", risk_tier="ask"),
                "spawn-worker": _Tool("spawn-worker"),
                "unrelated": _Tool("unrelated"),
            },
        },
    )()
    runtime_refs.set_brain_manager(manager)
    runtime_refs.set_supervisor_tool_gateway(BrainSupervisorToolGateway(manager))


def _inventory() -> WorkerCapabilityInventory:
    return WorkerCapabilityInventory.build(
        mcp_servers={
            "github": {
                "command": "github-mcp",
                "env": {"ACCESS_TOKEN": "must-not-leave-supervisor"},
            }
        },
        app_commands=("wiki-ingest",),
        native_tool_names=("gmail",),
        task_text="List the open GitHub issues and read my Gmail inbox.",
    )


@pytest.mark.asyncio
async def test_binding_is_filtered_and_executes_only_through_supervisor() -> None:
    executor = _Executor()
    _wire_manager(executor)

    binding = _inventory().bind_broker(
        ttl_s=30,
        mission_id="mission-123",
        worker_id="worker-456",
    )

    assert binding is not None
    assert set(binding.tool_names) == {"github/list_issues", "wiki-ingest", "gmail"}
    result = await binding.execute("github/list_issues", {"value": "open"})
    assert result["success"] is True
    assert binding.execution_summary.clean is True
    assert executor.calls[0][0] == "github/list_issues"
    assert executor.calls[0][2]["config_snapshot"]["voice_confirm"] is False
    assert executor.calls[0][2]["config_snapshot"]["tool_origin"] == "mission_worker"
    assert executor.calls[0][2]["user_utterance"].startswith("List the open")

    denied = await binding.execute("unrelated", {})
    assert denied["status"] == "denied"
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_ask_tier_waits_then_resumes_the_exact_call_after_approval() -> None:
    bus = EventBus()
    approval = ApprovalWorkflow(bus, timeout_s=1.0)
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(SafetyConfig()),
        approval,
        default_timeout_s=1.0,
    )
    tool = _ExecutingAskTool()
    manager = SimpleNamespace(_tool_executor=executor, _tools={tool.name: tool})
    runtime_refs.set_brain_manager(manager)
    runtime_refs.set_supervisor_tool_gateway(BrainSupervisorToolGateway(manager))
    requested = asyncio.Event()
    request: ActionApprovalRequired | None = None

    async def _capture(event: ActionApprovalRequired) -> None:
        nonlocal request
        request = event
        requested.set()

    bus.subscribe(ActionApprovalRequired, _capture)
    binding = _inventory().bind_broker(
        ttl_s=30,
        mission_id="mission-123",
        worker_id="worker-456",
    )
    assert binding is not None

    call = asyncio.create_task(binding.execute("gmail", {"value": "send"}))
    await asyncio.wait_for(requested.wait(), timeout=1.0)

    assert request is not None
    assert request.mission_id == "mission-123"
    assert request.worker_id == "worker-456"
    assert call.done() is False
    assert tool.calls == 0
    assert binding.execution_summary.clean is False
    assert binding.execution_summary.active_count == 1
    await bus.publish(
        ActionApproved(
            trace_id=request.trace_id,
            tool_name=request.tool_name,
            approved_by="user",
        )
    )
    result = await asyncio.wait_for(call, timeout=1.0)

    assert result["status"] == "ok"
    assert result["success"] is True
    assert result["trace_id"] == str(request.trace_id)
    assert tool.calls == 1
    assert binding.execution_summary.clean is True


@pytest.mark.asyncio
async def test_denied_ask_tier_never_executes_the_tool() -> None:
    bus = EventBus()
    approval = ApprovalWorkflow(bus, timeout_s=1.0)
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(SafetyConfig()),
        approval,
        default_timeout_s=1.0,
    )
    tool = _ExecutingAskTool()
    manager = SimpleNamespace(_tool_executor=executor, _tools={tool.name: tool})
    runtime_refs.set_brain_manager(manager)
    runtime_refs.set_supervisor_tool_gateway(BrainSupervisorToolGateway(manager))
    requested: asyncio.Queue[ActionApprovalRequired] = asyncio.Queue()
    bus.subscribe(ActionApprovalRequired, requested.put)
    binding = _inventory().bind_broker(ttl_s=30)
    assert binding is not None

    call = asyncio.create_task(binding.execute("gmail", {"value": "send"}))
    request = await asyncio.wait_for(requested.get(), timeout=1.0)
    await bus.publish(
        ActionDenied(
            trace_id=request.trace_id,
            tool_name=request.tool_name,
            reason="user_denied",
        )
    )
    result = await asyncio.wait_for(call, timeout=1.0)

    assert result["status"] == "approval_denied"
    assert result["success"] is False
    assert tool.calls == 0
    assert binding.execution_summary.clean is False
    assert binding.execution_summary.calls[-1].status == "denied"


@pytest.mark.asyncio
async def test_loopback_http_requires_env_bearer_and_revocation_is_immediate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _Executor()
    _wire_manager(executor)
    binding = _inventory().bind_broker(ttl_s=30)
    assert binding is not None
    env = binding.apply_environment({})
    monkeypatch.setenv(BROKER_URL_ENV, env[BROKER_URL_ENV])
    monkeypatch.setenv(BROKER_TOKEN_ENV, env[BROKER_TOKEN_ENV])

    listed = await asyncio.to_thread(broker_stdio._request, "GET", "/v1/tools")
    assert {tool["name"] for tool in listed["tools"]} == set(binding.tool_names)
    result = await asyncio.to_thread(
        broker_stdio._request,
        "POST",
        "/v1/execute",
        {"name": "wiki-ingest", "arguments": {"value": "fact"}},
    )
    assert result["success"] is True

    binding.close()
    with pytest.raises(RuntimeError, match="unauthorized"):
        await asyncio.to_thread(broker_stdio._request, "GET", "/v1/tools")


@pytest.mark.asyncio
async def test_cli_configs_contain_only_adapter_while_token_stays_in_env(tmp_path) -> None:
    executor = _Executor()
    _wire_manager(executor)
    inventory = _inventory()
    binding = inventory.bind_broker(ttl_s=30)
    assert binding is not None

    server_config = binding.mcp_server_config()
    serialized = json.dumps(server_config)
    child_env = binding.apply_environment({"PATH": "test"})
    assert "must-not-leave-supervisor" not in serialized
    assert BROKER_TOKEN_ENV not in serialized
    assert child_env[BROKER_TOKEN_ENV]

    codex_cmd = _build_codex_direct_cmd(
        worktree=tmp_path,
        model=None,
        mcp_servers=server_config,
    )
    assert "mcp_servers.jarvis_worker_tools.command" in " ".join(codex_cmd)
    assert child_env[BROKER_TOKEN_ENV] not in " ".join(codex_cmd)

    gemini_env, settings_path, allowed = _build_isolated_gemini_env(
        child_env,
        log_dir=tmp_path,
        mcp_servers=server_config,
    )
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert allowed == "jarvis_worker_tools"
    assert settings["mcpServers"] == server_config
    assert BROKER_TOKEN_ENV not in settings_path.read_text(encoding="utf-8")
    assert gemini_env[BROKER_TOKEN_ENV] == child_env[BROKER_TOKEN_ENV]


@pytest.mark.asyncio
async def test_frozen_bundle_uses_its_internal_stdio_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _Executor()
    _wire_manager(executor)
    binding = _inventory().bind_broker(ttl_s=30)
    assert binding is not None
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    config = binding.mcp_server_config()["jarvis_worker_tools"]

    assert config["command"] == sys.executable
    assert config["args"] == ["--worker-tool-broker-stdio"]


@pytest.mark.asyncio
async def test_stdio_mcp_adapter_lists_and_calls_the_scoped_tools() -> None:
    executor = _Executor()
    _wire_manager(executor)
    binding = _inventory().bind_broker(ttl_s=30)
    assert binding is not None
    env = binding.apply_environment({})
    client = MCPClient(
        MCPServerSpec(
            name="jarvis-worker-tools-test",
            display="Jarvis worker tools",
            description="Test broker adapter",
            install_command=[
                sys.executable,
                "-m",
                "jarvis.missions.workers.broker_stdio",
            ],
        ),
        env_overrides={
            BROKER_URL_ENV: env[BROKER_URL_ENV],
            BROKER_TOKEN_ENV: env[BROKER_TOKEN_ENV],
        },
    )
    try:
        await asyncio.wait_for(client.start(), timeout=10)
        listed = await client.list_tools()
        assert {tool["name"] for tool in listed} == set(binding.tool_names)
        result = await client.call_tool("github/list_issues", {"value": "open"})
        assert result.structuredContent["success"] is True
    finally:
        await client.stop()
        binding.close()


@pytest.mark.asyncio
async def test_missing_supervisor_degrades_to_no_grant() -> None:
    inventory = _inventory()
    assert inventory.bind_broker() is None
    report = inventory.report_for("api:openrouter")
    assert report["broker"]["status"] == "unavailable"
    assert report["mcp"]["status"] == "unavailable"


def test_native_connector_discovery_is_relevance_and_credential_aware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.marketplace import catalog_data, plugin_relevance, token_store

    plugins = [
        SimpleNamespace(id="gmail", native_tool="gmail", description="Email"),
        SimpleNamespace(id="vercel", native_tool="vercel", description="Deployments"),
        SimpleNamespace(id="docs", native_tool=None, description="Docs"),
    ]
    token_by_id = {
        "gmail": SimpleNamespace(access="token", needs_reauth=False),
        "vercel": SimpleNamespace(access="token", needs_reauth=True),
    }
    monkeypatch.setattr(
        catalog_data, "load_catalog", lambda: SimpleNamespace(plugins=plugins)
    )
    monkeypatch.setattr(
        token_store,
        "TokenStore",
        lambda: SimpleNamespace(load=lambda plugin_id: token_by_id.get(plugin_id)),
    )
    monkeypatch.setattr(
        plugin_relevance,
        "plugin_is_relevant",
        lambda text, plugin_id, _tools: plugin_id in text.lower(),
    )

    assert _connected_native_worker_tools("Read my Gmail inbox") == ("gmail",)
    assert _connected_native_worker_tools("Inspect Vercel") == ()
