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

import asyncio
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


# ---------------------------------------------------------------------------
# Wave 0 (frontier-speed, 2026-06-09): background offload.
#
# Run inline, the mission lives inside the brain turn's task — the speech
# stall guard's task.cancel() (or any turn unwind) beheads a healthy desktop
# mission. With a bus wired (production wiring via factory.py), the tool must
# return an immediate ACK and run the mission as a background task whose
# outcome is ALWAYS announced (AD-OE1/OE5/OE6).
# ---------------------------------------------------------------------------


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class _SlowHarnessManager(_FakeHarnessManager):
    """Dispatch that takes a moment — long enough to prove the ACK returned
    before the mission finished."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def dispatch(self, name: str, task) -> AsyncIterator[HarnessResult]:
        self.started.set()
        await self.release.wait()
        self.dispatched.append((name, task.prompt))
        yield HarnessResult(stdout="[cu] done (verified)", exit_code=0, is_final=True)


async def test_with_bus_returns_immediate_ack_and_announces_completion() -> None:
    import asyncio

    from jarvis.core.events import AnnouncementRequested
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    bus = _FakeBus()
    fake = _SlowHarnessManager()
    tool = ComputerUseTool(bus=bus, manager=fake)

    # wait_for: in the pre-fix inline implementation execute() blocks on the
    # never-released dispatch — the test must FAIL fast, not hang.
    result = await asyncio.wait_for(
        tool.execute({"goal": "open chrome"}, _ctx()), timeout=2.0,
    )

    # Immediate ACK — the mission has NOT finished yet.
    assert result.success
    assert fake.dispatched == []
    await asyncio.wait_for(fake.started.wait(), timeout=2.0)

    # Let the mission finish; its outcome must be announced on the bus.
    fake.release.set()
    for _ in range(200):
        if any(isinstance(e, AnnouncementRequested) for e in bus.events):
            break
        await asyncio.sleep(0.01)
    completions = [
        e for e in bus.events
        if isinstance(e, AnnouncementRequested) and e.kind == "completion"
    ]
    assert len(completions) == 1
    assert fake.dispatched == [("screenshot", "open chrome")]


async def test_with_bus_failure_is_announced_not_silent() -> None:
    import asyncio

    from jarvis.core.events import AnnouncementRequested
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    class _FailingManager(_FakeHarnessManager):
        async def dispatch(self, name: str, task) -> AsyncIterator[HarnessResult]:
            self.dispatched.append((name, task.prompt))
            yield HarnessResult(
                stderr="[cu] goal not verifiably achieved", exit_code=5,
                is_final=True,
            )

    bus = _FakeBus()
    tool = ComputerUseTool(bus=bus, manager=_FailingManager())

    result = await tool.execute({"goal": "open chrome"}, _ctx())
    assert result.success  # ACK — outcome arrives via announcement

    for _ in range(200):
        if any(isinstance(e, AnnouncementRequested) for e in bus.events):
            break
        await asyncio.sleep(0.01)
    completions = [
        e for e in bus.events
        if isinstance(e, AnnouncementRequested) and e.kind == "completion"
    ]
    assert len(completions) == 1  # AD-OE6: zero silent drops


async def test_without_bus_keeps_synchronous_contract() -> None:
    """No bus (tests / minimal wiring) → the old inline behaviour stays."""
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    fake = _FakeHarnessManager()
    tool = ComputerUseTool(manager=fake)

    result = await tool.execute({"goal": "open a terminal"}, _ctx())

    assert result.success
    assert fake.dispatched == [("screenshot", "open a terminal")]
