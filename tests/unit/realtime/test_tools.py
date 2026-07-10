"""Safety and provider-neutrality tests for realtime tool execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.realtime.tools import RealtimeToolBridge
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL


class FakeTool:
    def __init__(self, name: str = "open_app") -> None:
        self.name = name
        self.description = "Open a local application."
        self.schema = {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
            "required": ["app_name"],
        }
        self.risk_tier = "ask"

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("Realtime must never execute a tool directly")


class FakeExecutor:
    def __init__(self, *, confirmation_required: bool = False) -> None:
        self.confirmation_required = confirmation_required
        self.execute_calls = []
        self.confirmed_calls = []
        self.cancelled = []
        self.denied = []

    async def execute(self, tool, arguments, **kwargs):
        self.execute_calls.append((tool, arguments, kwargs))
        if self.confirmation_required:
            return SimpleNamespace(
                success=False,
                output={"tool_name": tool.name},
                error=VOICE_CONFIRM_SENTINEL,
            )
        return SimpleNamespace(success=True, output="opened", error=None)

    async def execute_confirmed(self, trace_id, **kwargs):
        self.confirmed_calls.append((trace_id, kwargs))
        return SimpleNamespace(success=True, output="confirmed", error=None)

    async def cancel_pending(self, trace_id):
        self.cancelled.append(trace_id)
        return True

    async def publish_guard_denied(self, name, reason, *, trace_id):
        self.denied.append((name, reason, trace_id))


def _bridge(*, name: str = "open_app", confirmation_required: bool = False):
    tool = FakeTool(name)
    executor = FakeExecutor(confirmation_required=confirmation_required)
    bridge = RealtimeToolBridge(
        tools={name: tool}, executor=executor, language="en"
    )
    return bridge, tool, executor


def test_declaration_uses_a_cross_provider_safe_wire_name():
    bridge, _tool, _executor = _bridge(name="call-contact")

    declaration = bridge.declarations[0]

    assert declaration["name"].replace("_", "").isalnum()
    assert len(declaration["name"]) <= 64
    assert declaration["parameters"]["required"] == ["app_name"]


@pytest.mark.asyncio
async def test_available_tool_runs_only_through_tool_executor():
    bridge, tool, executor = _bridge()
    await bridge.handle_user_transcript("Open Calculator")

    name, result = await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Calculator"}
    )

    assert name == "open_app"
    assert result == {"success": True, "output": "opened", "error": None}
    assert executor.execute_calls[0][0] is tool
    assert executor.execute_calls[0][1] == {"app_name": "Calculator"}
    assert executor.execute_calls[0][2]["config_snapshot"]["voice_confirm"] is True


@pytest.mark.asyncio
async def test_missing_required_argument_is_denied_before_executor():
    bridge, _tool, executor = _bridge()
    await bridge.handle_user_transcript("Open it")

    _name, result = await bridge.execute(wire_name="open_app", arguments={})

    assert result["success"] is False
    assert "Missing required" in result["error"]
    assert executor.execute_calls == []
    assert executor.denied


@pytest.mark.asyncio
async def test_clear_yes_resumes_the_original_pending_action_once():
    bridge, _tool, executor = _bridge(confirmation_required=True)
    await bridge.handle_user_transcript("Open Calculator")

    _name, first = await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Calculator"}
    )
    await bridge.handle_user_transcript("Yes")
    _name, second = await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Something else"}
    )

    assert first["confirmation_required"] is True
    assert second == {"success": True, "output": "confirmed", "error": None}
    assert len(executor.execute_calls) == 1
    assert len(executor.confirmed_calls) == 1


@pytest.mark.asyncio
async def test_clear_no_cancels_and_blocks_same_turn_retry():
    bridge, _tool, executor = _bridge(confirmation_required=True)
    await bridge.handle_user_transcript("Open Calculator")
    await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Calculator"}
    )

    await bridge.handle_user_transcript("No")
    _name, result = await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Calculator"}
    )

    assert len(executor.cancelled) == 1
    assert result["success"] is False
    assert "declined" in result["error"]
    assert len(executor.execute_calls) == 1
