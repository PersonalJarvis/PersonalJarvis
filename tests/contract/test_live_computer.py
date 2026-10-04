"""Live sessions declare ``computer`` directly and never reach a second CU model (ADR-0039)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.protocols import SupervisorToolDescriptor, ToolResult
from jarvis.cu.direct import TOOL_DESCRIPTION, tool_schema
from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools


class ComputerGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def catalog(self):
        return (
            SupervisorToolDescriptor("computer", TOOL_DESCRIPTION, tool_schema(), "monitor"),
            SupervisorToolDescriptor("open_app", "Open an app", {"type": "object"}, "monitor"),
        )

    async def execute(self, name, args, request):
        self.calls.append((name, args))
        return ToolResult(
            True,
            {"executed": ["pressed enter"], "_image": {"mime": "image/jpeg", "data": "AAAA"}},
        )

    async def cancel_pending(self, *_args, **_kwargs):
        return True


@pytest.fixture
def ledger(tmp_path):
    store = LiveLedger(tmp_path / "live.sqlite3")
    yield store
    store.close()


@pytest.mark.parametrize("defer", [True, False])
def test_computer_is_declared_under_its_own_name(ledger, defer):
    runtime = LiveTools(ComputerGateway(), ledger, "s", language="en", backend_model="m")
    declared = {d["name"]: d for d in runtime.declarations(defer_catalog=defer)}
    assert declared["computer"]["parameters"] == tool_schema()
    # Exactly one declaration: never a second hashed alias for the same tool.
    aliases = [n for n in declared if runtime._names.get(n) == "computer"]
    assert aliases == []


@pytest.mark.asyncio
async def test_a_direct_computer_call_runs_and_returns_its_screenshot(ledger):
    gateway = ComputerGateway()
    runtime = LiveTools(gateway, ledger, "s", language="en", backend_model="m")
    runtime.declarations(defer_catalog=True)
    runtime.user_text = "Open Chrome"
    result = await runtime.execute("c1", "computer", {"steps": [{"action": "screenshot"}]}, 0)
    assert result["success"] is True
    assert gateway.calls == [("computer", {"steps": [{"action": "screenshot"}]})]
    assert result["artifacts"] == [{"mime": "image/jpeg", "data": "AAAA", "type": "image"}]


def test_voice_catalog_drops_the_second_model_vehicles(monkeypatch):
    from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
    from jarvis.harness import computer_use_context

    def tool(name):
        return SimpleNamespace(
            name=name,
            execute=lambda *args: None,
            schema={"type": "object"},
            description=name,
            risk_tier="monitor",
        )

    monkeypatch.setattr(
        computer_use_context,
        "peek_computer_use_context",
        lambda: SimpleNamespace(tools={n: tool(n) for n in ("click", "type_text", "open_app")}),
    )
    tools = {n: tool(n) for n in ("computer_use", "dispatch_to_harness", "search_web")}
    manager = SimpleNamespace(
        _tools=tools, _config=SimpleNamespace(computer_use=SimpleNamespace(enabled=True))
    )
    gateway = BrainSupervisorToolGateway(manager)
    voice = {d.name for d in gateway.voice_catalog()}
    assert {"computer", "open_app", "search_web", "screen_snapshot"} <= voice
    assert not {"computer_use", "dispatch_to_harness", "click", "type_text"} & voice
    # Text chat keeps its own tool set, including the mission vehicle.
    assert "computer_use" in {d.name for d in gateway.catalog()}


def test_backend_instructions_hand_the_screen_to_the_thinking_model():
    from jarvis.live.config import LiveConfig

    config = LiveConfig(configured=True, backend_model="thinking-model")
    instructions = config.session_config(language="en", tools=[])["delegation"]["responses"][
        "instructions"
    ]
    assert "computer tool" in instructions
    assert "no separate computer-use agent" in instructions
