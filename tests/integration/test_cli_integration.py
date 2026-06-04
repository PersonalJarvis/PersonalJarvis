"""Integration tests for the CLI-integration backend wiring.

Covers:
- ``cli-tools`` is in ROUTER_TOOLS (router reachability).
- The ``cli-tools`` virtual loader is expanded into ``cli_<name>`` tools when a
  built router brain loads its tier tools, using the SHARED registry.
- Live-reload: a ``BrainToolsChanged`` event re-expands the live brain's tool set.
- ``POST /api/clis/test-run`` drives a real brain turn through the real
  ToolExecutor and reports the chosen tool, command, risk tier, exit code,
  output, and summary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.clis.shared as shared
from jarvis.clis.registry import CliToolRegistry
from jarvis.clis.spec import AuthConfig, CliSpec, CliStatus, InstallMethods, RiskConfig
from jarvis.clis.usage_log import UsageLog
from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.ui.web.cli_routes import router as cli_router

# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class _FakeCatalog:
    def __init__(self, specs: dict[str, CliSpec]) -> None:
        self._specs = specs

    def all(self) -> dict[str, CliSpec]:
        return dict(self._specs)

    def get(self, name: str) -> CliSpec | None:
        return self._specs.get(name)


class _FakeProber:
    def __init__(self, statuses: dict[str, CliStatus]) -> None:
        self.statuses = statuses

    async def probe(self, spec: CliSpec) -> CliStatus:
        return self.statuses.get(spec.name, CliStatus())

    async def probe_all(self, specs: list[CliSpec]) -> dict[str, CliStatus]:
        return {s.name: await self.probe(s) for s in specs}


def _demo_spec() -> CliSpec:
    return CliSpec(
        name="demo",
        display_name="Demo CLI",
        description="echoes things",
        homepage="",
        binary_name="demo",
        check_command=("demo", "--version"),
        version_parse_regex=r"(\d+)",
        install=InstallMethods(manual_url="https://x"),
        auth=AuthConfig(type="none"),
        risk=RiskConfig(default_tier="safe"),
    )


def _fake_registry(tmp_path: Path) -> CliToolRegistry:
    return CliToolRegistry(
        catalog=_FakeCatalog({"demo": _demo_spec()}),  # type: ignore[arg-type]
        prober=_FakeProber({"demo": CliStatus(installed=True, version="1")}),  # type: ignore[arg-type]
        usage_log=UsageLog(db_path=tmp_path / "u.db"),
    )


@pytest.fixture(autouse=True)
def _reset_shared_registry():
    prev = shared.get_active_registry()
    shared.set_active_registry(None)
    yield
    shared.set_active_registry(prev)


# ----------------------------------------------------------------------
# Router reachability
# ----------------------------------------------------------------------


def test_cli_tools_in_router_tools() -> None:
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "cli-tools" in ROUTER_TOOLS
    assert isinstance(ROUTER_TOOLS, frozenset)


# ----------------------------------------------------------------------
# Loader expansion uses the shared registry
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loader_entry_point_expands_shared_registry(tmp_path: Path) -> None:
    """The real ``cli-tools`` entry point, when expanded in the same way the
    brain loader does it, must surface the shared registry's connected CLIs."""
    from importlib.metadata import entry_points

    reg = _fake_registry(tmp_path)
    await reg.bootstrap()
    shared.set_active_registry(reg)

    loader_cls = None
    for ep in entry_points(group="jarvis.tool"):
        if ep.name == "cli-tools":
            loader_cls = ep.load()
            break
    assert loader_cls is not None, "cli-tools entry point must be registered"

    loader = loader_cls()
    assert getattr(loader, "is_virtual_loader", False) is True
    tools = loader.expand()
    assert {t.name for t in tools} == {"cli_demo"}


@pytest.mark.asyncio
async def test_load_tools_for_tier_includes_cli_tool(tmp_path: Path) -> None:
    """``_load_tools_for_tier('router')`` must include the expanded ``cli_demo``
    tool when a shared registry with a usable CLI is published."""
    from jarvis.brain.factory import _load_tools_for_tier

    reg = _fake_registry(tmp_path)
    await reg.bootstrap()
    shared.set_active_registry(reg)

    from jarvis.core.config import JarvisConfig

    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
        mission_manager=None,
        awareness_manager=None,
        recall_store=None,
    )
    assert "cli_demo" in tools, f"cli_demo not loaded; got {sorted(tools)}"


# ----------------------------------------------------------------------
# Live-reload: BrainToolsChanged re-expands the brain
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_reload_reexpands_on_brain_tools_changed(tmp_path: Path) -> None:
    """Connecting a CLI publishes BrainToolsChanged; the live BrainManager's
    refresh_tools() re-runs the factory and picks up the new cli_<name> tool."""
    from jarvis.brain.manager import BrainManager
    from jarvis.core.config import JarvisConfig
    from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor

    bus = EventBus()

    # Start with an empty shared registry (no CLI connected yet).
    reg = CliToolRegistry(
        catalog=_FakeCatalog({"demo": _demo_spec()}),  # type: ignore[arg-type]
        prober=_FakeProber({"demo": CliStatus(installed=False)}),  # type: ignore[arg-type]
        usage_log=UsageLog(db_path=tmp_path / "u.db"),
        bus=bus,
    )
    await reg.bootstrap()
    shared.set_active_registry(reg)

    config = JarvisConfig()
    evaluator = RiskTierEvaluator(config.safety)
    executor = ToolExecutor(bus, evaluator, ApprovalWorkflow(bus))
    manager = BrainManager(
        config=config,
        bus=bus,
        tools={},
        tool_executor=executor,
    )
    manager._tier = "router"
    manager.attach_to_bus(bus)

    assert "cli_demo" not in manager._tools

    # Now the CLI "connects": prober reports installed -> refresh_status adds
    # the tool and publishes BrainToolsChanged -> manager.refresh_tools().
    reg._prober.statuses["demo"] = CliStatus(installed=True, version="1")
    await reg.refresh_status("demo")

    # refresh_tools is synchronous inside the async handler; the event was
    # dispatched during refresh_status's await on bus.publish.
    assert "cli_demo" in manager._tools, (
        f"live brain did not re-expand cli_demo; tools={sorted(manager._tools)}"
    )


# ----------------------------------------------------------------------
# /api/clis/test-run endpoint
# ----------------------------------------------------------------------


class _BusCliTool:
    """A minimal CLI-like tool that returns a structured ToolResult."""

    def __init__(self, name: str, exit_code: int = 0) -> None:
        self.name = name
        self.risk_tier = "safe"
        self.schema = {"type": "object", "properties": {"command": {"type": "string"}}}
        self._exit_code = exit_code

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        return ToolResult(
            success=self._exit_code == 0,
            output={
                "exit_code": self._exit_code,
                "stdout": "project-a\nproject-b\n",
                "stderr": "",
                "duration_ms": 12,
            },
            error=None if self._exit_code == 0 else f"exit {self._exit_code}",
        )


class _FakeBrain:
    """Drives the chosen cli_<name> tool through a REAL ToolExecutor.

    The endpoint subscribes to bus events and wraps the cli tools, so this fake
    exercises the real capture path end-to-end without an LLM.
    """

    def __init__(self, bus: EventBus, executor: Any, tool: Any, summary: str) -> None:
        self._bus = bus
        self._tool_executor = executor
        self._tools = {tool.name: tool}
        self._summary = summary
        self.last_prompt: str | None = None

    async def generate(self, prompt: str, *, use_history: bool = True, **_: Any) -> str:
        self.last_prompt = prompt
        tool = next(iter(self._tools.values()))
        await self._tool_executor.execute(
            tool,
            {"command": "demo projects list --format=json"},
            user_utterance=prompt,
            trace_id=uuid4(),
        )
        return self._summary


def _app_with_brain(brain: Any, registry: Any) -> FastAPI:
    app = FastAPI()
    app.state.brain = brain
    app.state.cli_registry = registry
    app.include_router(cli_router)
    return app


def test_test_run_reports_tool_command_and_output(tmp_path: Path) -> None:
    from jarvis.core.config import JarvisConfig
    from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor

    bus = EventBus()
    evaluator = RiskTierEvaluator(JarvisConfig().safety)
    executor = ToolExecutor(bus, evaluator, ApprovalWorkflow(bus))
    tool = _BusCliTool("cli_demo", exit_code=0)
    brain = _FakeBrain(bus, executor, tool, summary="You have 2 projects.")
    reg = _fake_registry(tmp_path)

    client = TestClient(_app_with_brain(brain, reg))
    resp = client.post(
        "/api/clis/test-run",
        json={"instruction": "list my demo projects"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["instruction"] == "list my demo projects"
    assert data["tool_called"] == "cli_demo"
    assert data["command"] == "demo projects list --format=json"
    assert data["risk_tier"] == "safe"
    assert data["exit_code"] == 0
    assert "project-a" in data["stdout"]
    assert data["summary"] == "You have 2 projects."
    assert data["error"] is None
    assert len(data["steps"]) == 1
    assert data["steps"][0]["tool"] == "cli_demo"
    assert data["steps"][0]["exit_code"] == 0


def test_test_run_cli_hint_is_appended(tmp_path: Path) -> None:
    from jarvis.core.config import JarvisConfig
    from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor

    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(JarvisConfig().safety), ApprovalWorkflow(bus))
    brain = _FakeBrain(bus, executor, _BusCliTool("cli_demo"), summary="ok")
    client = TestClient(_app_with_brain(brain, _fake_registry(tmp_path)))
    resp = client.post(
        "/api/clis/test-run",
        json={"instruction": "list projects", "cli_hint": "demo"},
    )
    assert resp.status_code == 200
    assert "demo CLI tool" in (brain.last_prompt or "")


def test_test_run_nonzero_exit_marks_not_ok(tmp_path: Path) -> None:
    from jarvis.core.config import JarvisConfig
    from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor

    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(JarvisConfig().safety), ApprovalWorkflow(bus))
    brain = _FakeBrain(bus, executor, _BusCliTool("cli_demo", exit_code=1), summary="hmm")
    client = TestClient(_app_with_brain(brain, _fake_registry(tmp_path)))
    resp = client.post("/api/clis/test-run", json={"instruction": "do a thing"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["exit_code"] == 1
    assert data["tool_called"] == "cli_demo"


def test_test_run_no_cli_tool_called_is_flagged(tmp_path: Path) -> None:
    """When the brain answers without calling a cli_<name> tool, the endpoint
    flags it (ok depends on whether a tool ran)."""

    class _NoToolBrain:
        def __init__(self, bus: EventBus) -> None:
            self._bus = bus
            self._tools = {"cli_demo": _BusCliTool("cli_demo")}
            self._tool_executor = None

        async def generate(self, prompt: str, *, use_history: bool = True, **_: Any) -> str:
            return "I answered from memory."

    bus = EventBus()
    brain = _NoToolBrain(bus)
    client = TestClient(_app_with_brain(brain, _fake_registry(tmp_path)))
    resp = client.post("/api/clis/test-run", json={"instruction": "what is 2+2"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_called"] is None
    assert "No cli_" in (data["error"] or "")
    assert data["summary"] == "I answered from memory."


def test_test_run_empty_instruction_is_422(tmp_path: Path) -> None:
    from jarvis.core.config import JarvisConfig
    from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor

    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(JarvisConfig().safety), ApprovalWorkflow(bus))
    brain = _FakeBrain(bus, executor, _BusCliTool("cli_demo"), summary="ok")
    client = TestClient(_app_with_brain(brain, _fake_registry(tmp_path)))
    resp = client.post("/api/clis/test-run", json={"instruction": ""})
    # Pydantic min_length=1 -> 422 validation error.
    assert resp.status_code == 422
