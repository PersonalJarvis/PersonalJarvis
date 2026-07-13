"""TaskAutoApprover — unattended pre-authorization for scheduled-task tools.

A scheduled task pre-authorizes specific plugins (write/full scope) at
creation. While that task's turn runs, the auto-approver answers the
ask-tier approval gate programmatically for exactly those tools — so an
unattended run (a scheduled tweet, a digest email) does not block waiting
for a human, while the full audit trail (ActionProposed -> ActionApproved
-> ActionExecuted) is preserved. Tools that were NOT pre-authorized still
block.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionApproved, ActionProposed
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor
from jarvis.tasks.approval_bridge import TaskAutoApprover

pytestmark = pytest.mark.phase5


class _AskTool:
    name = "buffer"
    risk_tier = "ask"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls = 0
        self.approved_by = ""

    async def execute(
        self, _args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:
        self.calls += 1
        self.approved_by = ctx.approved_by
        return ToolResult(success=True, output="executed")


async def _collect_approvals(bus: EventBus) -> list[ActionApproved]:
    got: list[ActionApproved] = []

    async def _cap(ev: object) -> None:
        if isinstance(ev, ActionApproved):
            got.append(ev)

    bus.subscribe_all(_cap)
    return got


async def test_approves_armed_tool_on_its_trace() -> None:
    bus = EventBus()
    approver = TaskAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    tid = uuid4()
    approver.arm(tid, ["buffer"], approved_by="scheduled-task:abc")

    await bus.publish(ActionProposed(trace_id=tid, tool_name="buffer", risk_tier="ask"))
    await asyncio.sleep(0)

    assert len(approvals) == 1
    assert approvals[0].trace_id == tid
    assert approvals[0].tool_name == "buffer"
    assert approvals[0].approved_by == "scheduled-task:abc"


async def test_pre_authorized_tool_executes_without_approval_race() -> None:
    """An approval published from ActionProposed must reach ToolExecutor.wait."""
    bus = EventBus()
    approval = ApprovalWorkflow(bus, timeout_s=0.05)
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(SafetyConfig()),
        approval,
        default_timeout_s=0.05,
    )
    approver = TaskAutoApprover(bus)
    tool = _AskTool()
    trace_id = uuid4()
    approver.arm(trace_id, [tool.name], approved_by="scheduled-task:abc")

    result = await executor.execute(tool, {}, trace_id=trace_id)

    assert result.success is True
    assert result.output == "executed"
    assert tool.calls == 1
    assert tool.approved_by == "scheduled-task:abc"


async def test_ignores_tool_not_in_grant() -> None:
    bus = EventBus()
    approver = TaskAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    tid = uuid4()
    approver.arm(tid, ["buffer"], approved_by="scheduled-task:abc")

    await bus.publish(ActionProposed(trace_id=tid, tool_name="gmail", risk_tier="ask"))
    await asyncio.sleep(0)

    assert approvals == []


async def test_ignores_other_trace_id() -> None:
    bus = EventBus()
    approver = TaskAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    approver.arm(uuid4(), ["buffer"], approved_by="scheduled-task:abc")

    await bus.publish(ActionProposed(trace_id=uuid4(), tool_name="buffer", risk_tier="ask"))
    await asyncio.sleep(0)

    assert approvals == []


async def test_disarm_stops_approval() -> None:
    bus = EventBus()
    approver = TaskAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    tid = uuid4()
    approver.arm(tid, ["buffer"], approved_by="scheduled-task:abc")
    approver.disarm(tid)

    await bus.publish(ActionProposed(trace_id=tid, tool_name="buffer", risk_tier="ask"))
    await asyncio.sleep(0)

    assert approvals == []


async def test_matches_mcp_namespaced_tool() -> None:
    """A grant on plugin 'gmail' covers an MCP tool named 'gmail/send_message'."""
    bus = EventBus()
    approver = TaskAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    tid = uuid4()
    approver.arm(tid, ["gmail"], approved_by="scheduled-task:abc")

    await bus.publish(
        ActionProposed(trace_id=tid, tool_name="gmail/send_message", risk_tier="ask")
    )
    await asyncio.sleep(0)

    assert len(approvals) == 1
    assert approvals[0].tool_name == "gmail/send_message"


async def test_arm_with_no_tools_is_inert() -> None:
    """A read-only task arms with an empty set → nothing is auto-approved."""
    bus = EventBus()
    approver = TaskAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    tid = uuid4()
    approver.arm(tid, [], approved_by="scheduled-task:abc")

    await bus.publish(ActionProposed(trace_id=tid, tool_name="gmail", risk_tier="ask"))
    await asyncio.sleep(0)

    assert approvals == []
