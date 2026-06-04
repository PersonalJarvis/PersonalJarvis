"""Wave 1 — router-tier ``computer_use`` tool.

The router brain must have a first-class, clearly-described tool to drive the
live desktop (open apps, click, type, scroll — anything done with mouse and
keyboard on THIS machine). Before this tool existed the router could only reach
the computer-use harness through the two-level ``dispatch_to_harness`` +
magic-``harness``-string indirection, whose schema description never mentioned
desktop control — so the model picked the wrong tool (or invented one) and the
user heard a refusal for "öffne ein Terminal".

These tests pin the new tool's identity, schema and dispatch behaviour without
an LLM: the tool forwards the goal verbatim to the canonical ``computer-use``
harness.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from jarvis.core.protocols import ExecutionContext, HarnessResult, HarnessTask


class _FakeHarnessManager:
    """Records ``dispatch(name, task)`` calls and yields one success result."""

    def __init__(self) -> None:
        self.dispatched: list[tuple[str, str]] = []

    async def dispatch(self, name: str, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        self.dispatched.append((name, task.prompt))
        yield HarnessResult(stdout="done", exit_code=0, is_final=True)


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid.uuid4(),
        user_utterance="öffne ein Terminal",
        config={},
        memory_read=None,
    )


def test_tool_identity_and_schema() -> None:
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    tool = ComputerUseTool(manager=_FakeHarnessManager())
    # Underscore name = what the LLM sees (hyphenated "computer-use" is the
    # entry-point/harness name, kept separate per the factory convention).
    assert tool.name == "computer_use"
    assert "goal" in tool.schema["properties"]
    assert tool.schema["required"] == ["goal"]
    # The description must clearly signal LIVE-desktop control so the model
    # selects it over spawn_openclaw / dispatch_to_harness.
    assert "desktop" in tool.description.lower()


async def test_dispatches_goal_to_computer_use_harness() -> None:
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    fake = _FakeHarnessManager()
    tool = ComputerUseTool(manager=fake)

    result = await tool.execute({"goal": "open a terminal"}, _ctx())

    assert result.success
    assert fake.dispatched == [("screenshot", "open a terminal")]


async def test_empty_goal_is_rejected_without_dispatch() -> None:
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    fake = _FakeHarnessManager()
    tool = ComputerUseTool(manager=fake)

    result = await tool.execute({"goal": "   "}, _ctx())

    assert not result.success
    assert fake.dispatched == []
