"""Integration-Test: BrainManager + ToolExecutor + FakeBrain-Tool-Use-Loop."""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import (
    JarvisConfig,
    SafetyBlacklistConfig,
    SafetyConfig,
    SafetyWhitelistConfig,
)
from jarvis.core.events import ActionExecuted, ActionProposed
from jarvis.core.protocols import BrainDelta, ExecutionContext, ToolResult
from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor
from tests.fixtures.brain.fake_brain import FakeBrain, tool_call_delta


class _DummyTool:
    name = "echo_tool"
    risk_tier = "safe"
    description = "Echo tool for testing"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    def __init__(self):
        self.calls: list[dict] = []

    async def execute(self, args, ctx: ExecutionContext) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, output=f"echo: {args.get('msg', '')}")


@pytest_asyncio.fixture
async def fake_infra(tmp_path, monkeypatch):
    """Baut Bus+Manager mit FakeBrain + DummyTool auf."""
    bus = EventBus()
    config = JarvisConfig()
    config.safety = SafetyConfig(
        default_tier="safe",
        whitelist=SafetyWhitelistConfig(commands=[]),
        blacklist=SafetyBlacklistConfig(commands=[]),
    )
    evaluator = RiskTierEvaluator(config.safety)
    approval = ApprovalWorkflow(bus)
    executor = ToolExecutor(bus, evaluator, approval)

    tool = _DummyTool()

    fake = FakeBrain(script=[
        # 1. Turn: Tool-Call
        [
            tool_call_delta("echo_tool", {"msg": "hello-world"}, call_id="c1"),
            BrainDelta(finish_reason="tool_use"),
        ],
        # 2. Turn: Finale Antwort nach Tool-Result
        [
            BrainDelta(content="Fertig: echo-hello-world"),
            BrainDelta(finish_reason="stop"),
        ],
    ])

    # BrainManager patchen damit er den FakeBrain nutzt
    manager = BrainManager(
        config=config,
        bus=bus,
        core_memory=None,
        recall=None,
        tools={tool.name: tool},
        tool_executor=executor,
    )
    # Fake in den Brain-Cache injizieren (key = (provider-name, model))
    manager._registry._loaded = True
    manager._registry._classes[manager._active_name] = type(fake)
    manager._brain_cache[(manager._active_name, None)] = fake
    # Fallback-Chain für Tests einfrieren — nur den fake nutzen
    manager._build_fallback_chain = lambda level: [(manager._active_name, None)]  # type: ignore[assignment]

    return {"manager": manager, "fake": fake, "tool": tool, "bus": bus}


@pytest.mark.asyncio
async def test_brain_can_use_tool_and_finish(fake_infra):
    captured_events: list = []

    async def on_proposed(e: ActionProposed):
        captured_events.append(("proposed", e.tool_name))

    async def on_executed(e: ActionExecuted):
        captured_events.append(("executed", e.tool_name, e.success))

    fake_infra["bus"].subscribe(ActionProposed, on_proposed)
    fake_infra["bus"].subscribe(ActionExecuted, on_executed)

    result = await fake_infra["manager"].generate("Benutze echo_tool mit msg=hello-world", use_history=False)

    # Tool wurde ausgeführt
    assert len(fake_infra["tool"].calls) == 1
    assert fake_infra["tool"].calls[0] == {"msg": "hello-world"}

    # Events wurden publiziert
    proposed = [e for e in captured_events if e[0] == "proposed"]
    executed = [e for e in captured_events if e[0] == "executed"]
    assert len(proposed) == 1
    assert proposed[0][1] == "echo_tool"
    assert len(executed) == 1
    assert executed[0][1] == "echo_tool"
    assert executed[0][2] is True

    # Fake-Brain wurde 2× aufgerufen (Tool-Call + Follow-up)
    assert len(fake_infra["fake"].calls) == 2

    # Final text
    assert "Fertig" in result


@pytest.mark.asyncio
async def test_no_tool_call_just_text():
    bus = EventBus()
    config = JarvisConfig()
    fake = FakeBrain(text_response="Hallo!")
    manager = BrainManager(config=config, bus=bus, tools={})
    # Keine Tools → Dispatcher nutzt den simple-Path
    manager._active_name = fake.name
    manager._registry._loaded = True
    manager._registry._classes[fake.name] = type(fake)
    manager._brain_cache[(fake.name, None)] = fake
    manager._build_fallback_chain = lambda level: [(fake.name, None)]
    result = await manager.generate("hi", use_history=False)
    assert "Hallo!" in result
