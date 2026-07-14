"""Race and cleanup guards for the first-wins approval workflow."""

from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.safety.approval import ApprovalWorkflow


@pytest.mark.asyncio
async def test_armed_ticket_keeps_a_decision_that_arrives_before_wait() -> None:
    workflow = ApprovalWorkflow(EventBus())
    trace_id = uuid4()
    workflow.arm(trace_id)

    await workflow.approve(trace_id, who="test-user")
    approved, who = await workflow.wait(trace_id, timeout_s=0.01)

    assert approved is True
    assert who == "test-user"


@pytest.mark.asyncio
async def test_decision_before_arm_never_pre_authorizes_future_action() -> None:
    workflow = ApprovalWorkflow(EventBus())
    trace_id = uuid4()

    await workflow.approve(trace_id, who="too-early")
    approved, reason = await workflow.wait(trace_id, timeout_s=0.01)

    assert approved is False
    assert reason == "timeout"


@pytest.mark.asyncio
async def test_duplicate_arm_is_rejected() -> None:
    workflow = ApprovalWorkflow(EventBus())
    trace_id = uuid4()
    ticket = workflow.arm(trace_id)
    try:
        with pytest.raises(RuntimeError, match="already armed"):
            workflow.arm(trace_id)
    finally:
        ticket.close()
