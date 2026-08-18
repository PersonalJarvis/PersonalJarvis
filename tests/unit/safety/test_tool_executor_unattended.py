"""An ask-tier tool must never wait for an approval nobody can give (GT-12).

Before this fix ``ToolExecutor.execute`` blocked in ``ApprovalWorkflow.wait()``
for 60 seconds on EVERY surface except voice and realtime, then returned the
timeout as ``approval-denied (timeout)``. A CLI call, a REST call, a scheduled
workflow, and a chat turn therefore all produced a minute of silence followed
by a refusal that no human ever made.

These tests pin the four outcomes apart:

* approved   — somebody (or a pre-authorization bridge) said yes; the tool runs
* denied     — somebody said no
* timed out  — a channel existed, nobody answered in time
* unavailable— there was no channel at all; fail fast and say so

and guard that none of this widened what may run without approval.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID, uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import (
    ActionApprovalRequired,
    ActionApproved,
    ActionDenied,
    ActionExecuted,
)
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.approval_surface import CONVERSATIONAL, INTERACTIVE, UNATTENDED
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import (
    APPROVAL_DENIED_PREFIX,
    APPROVAL_TIMEOUT_PREFIX,
    APPROVAL_UNAVAILABLE_OUTCOME,
    APPROVAL_UNAVAILABLE_PREFIX,
    VOICE_CONFIRM_SENTINEL,
    ToolExecutor,
)

pytestmark = pytest.mark.asyncio


class _AskTool:
    name = "gmail"
    risk_tier = "ask"
    schema: dict[str, Any] = {}

    def __init__(self) -> None:
        self.calls = 0
        self.last_ctx: ExecutionContext | None = None

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.calls += 1
        self.last_ctx = ctx
        return ToolResult(success=True, output="sent")


class _SafeTool(_AskTool):
    name = "search_web"
    risk_tier = "safe"


class _BlockTool(_AskTool):
    name = "wipe_disk"
    risk_tier = "block"


class _WaitSpy(ApprovalWorkflow):
    """Records every blocking wait so a "fail fast" claim is provable."""

    def __init__(self, bus: EventBus, **kwargs: Any) -> None:
        super().__init__(bus, **kwargs)
        self.waits: list[float] = []

    async def wait(  # type: ignore[override]
        self, trace_id: UUID, timeout_s: float | None = None
    ) -> tuple[bool, str]:
        self.waits.append(timeout_s if timeout_s is not None else -1.0)
        return await super().wait(trace_id, timeout_s)


def _executor(
    *, timeout_s: float = 60.0, safety: SafetyConfig | None = None
) -> tuple[ToolExecutor, _WaitSpy, EventBus]:
    bus = EventBus()
    approval = _WaitSpy(bus, timeout_s=timeout_s)
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(safety or SafetyConfig()),
        approval,
        default_timeout_s=timeout_s,
    )
    return executor, approval, bus


def _denials(bus: EventBus) -> list[ActionDenied]:
    seen: list[ActionDenied] = []

    async def _capture(event: ActionDenied) -> None:
        seen.append(event)

    bus.subscribe(ActionDenied, _capture)
    return seen


# ---------------------------------------------------------------- unattended


async def test_unattended_ask_tier_fails_fast_instead_of_stalling() -> None:
    """The whole bug in one test: no channel, no 60-second wait, honest reason."""
    executor, approval, bus = _executor(timeout_s=60.0)
    denials = _denials(bus)
    tool = _AskTool()

    started = time.perf_counter()
    result = await executor.execute(tool, args={"to": "tom"})
    elapsed = time.perf_counter() - started

    # Fast: the blocking waiter was never entered at all.
    assert approval.waits == []
    assert elapsed < 1.0
    # Honest: not run, and not reported as a refusal.
    assert result.success is False
    assert tool.calls == 0
    assert (result.error or "").startswith(APPROVAL_UNAVAILABLE_PREFIX)
    assert not (result.error or "").startswith(APPROVAL_DENIED_PREFIX)
    # Visible: the refusal reaches the timeline with its real reason.
    assert [e.reason for e in denials] == [
        f"{APPROVAL_UNAVAILABLE_OUTCOME}: no approval channel on this surface"
    ]


async def test_unattended_result_states_what_happened() -> None:
    """The caller gets a structured outcome plus a sentence a person can read."""
    executor, _approval, _bus = _executor()
    tid = uuid4()

    result = await executor.execute(
        _AskTool(),
        args={},
        config_snapshot={"output_language": "de"},
        trace_id=tid,
    )

    assert result.output["outcome"] == APPROVAL_UNAVAILABLE_OUTCOME
    assert result.output["tool_name"] == "gmail"
    assert result.output["trace_id"] == str(tid)
    assert result.output["risk_tier"] == "ask"
    message = result.output["message"]
    # Rendered in the turn's already-resolved language, never re-derived here.
    assert "Freigabe" in message  # i18n-allow — quoted runtime German output
    assert message == result.output["message"].strip()


async def test_unattended_message_follows_the_resolved_language() -> None:
    executor, _approval, _bus = _executor()
    result = await executor.execute(
        _AskTool(), args={}, config_snapshot={"output_language": "es"},
    )
    assert "aprobación" in result.output["message"]  # i18n-allow — runtime Spanish


async def test_unattended_still_honours_a_synchronous_preauthorization() -> None:
    """A pre-authorized call must still run — bridges answer on the publish.

    ``TaskAutoApprover`` / ``MissionToolAutoApprover`` reply to
    ``ActionApprovalRequired`` inline, so the verdict is already in the ticket
    by the time the publish returns. Failing fast must not outrun them.
    """
    executor, approval, bus = _executor()
    tool = _AskTool()

    async def _bridge(event: ActionApprovalRequired) -> None:
        await bus.publish(
            ActionApproved(
                trace_id=event.trace_id,
                tool_name=event.tool_name,
                approved_by="scheduled-task:abc",
            )
        )

    bus.subscribe(ActionApprovalRequired, _bridge)

    result = await executor.execute(tool, args={})

    assert result.success is True
    assert tool.calls == 1
    assert tool.last_ctx is not None
    assert tool.last_ctx.approved_by == "scheduled-task:abc"
    # Ran without ever blocking a waiter.
    assert approval.waits == []


async def test_unattended_preauthorization_can_also_refuse() -> None:
    """A bridge that says no is a real denial, not an absent human."""
    executor, _approval, bus = _executor()
    tool = _AskTool()

    async def _bridge(event: ActionApprovalRequired) -> None:
        await bus.publish(
            ActionDenied(
                trace_id=event.trace_id,
                tool_name=event.tool_name,
                reason="policy",
            )
        )

    bus.subscribe(ActionApprovalRequired, _bridge)

    result = await executor.execute(tool, args={})

    assert result.success is False
    assert tool.calls == 0
    assert (result.error or "").startswith(APPROVAL_DENIED_PREFIX)
    assert "policy" in (result.error or "")


async def test_unattended_approval_request_advertises_no_window() -> None:
    """No UI should offer a decision on a call that is already over."""
    executor, _approval, bus = _executor(timeout_s=60.0)
    requests: list[ActionApprovalRequired] = []

    async def _capture(event: ActionApprovalRequired) -> None:
        requests.append(event)

    bus.subscribe(ActionApprovalRequired, _capture)

    await executor.execute(_AskTool(), args={})

    assert len(requests) == 1
    assert requests[0].expires_at_ns <= time.time_ns()


# --------------------------------------------------------------- interactive


async def test_interactive_surface_still_waits_for_the_mission_deck() -> None:
    """A mission call keeps its human channel — this must NOT fail fast."""
    executor, approval, bus = _executor(timeout_s=5.0)
    tool = _AskTool()
    tid = uuid4()

    async def _click_approve() -> None:
        await asyncio.sleep(0.02)
        await bus.publish(ActionApproved(trace_id=tid, approved_by="user"))

    clicker = asyncio.create_task(_click_approve())
    result = await executor.execute(
        tool,
        args={},
        config_snapshot={"mission_id": "m-1", "worker_id": "w-1"},
        trace_id=tid,
    )
    await clicker

    assert approval.waits == [5.0]
    assert result.success is True
    assert tool.calls == 1
    assert tool.last_ctx is not None
    assert tool.last_ctx.approved_by == "user"


async def test_interactive_denial_reads_as_a_denial() -> None:
    executor, _approval, bus = _executor(timeout_s=5.0)
    tool = _AskTool()
    tid = uuid4()

    async def _click_deny() -> None:
        await asyncio.sleep(0.02)
        await bus.publish(ActionDenied(trace_id=tid, reason="user_vetoed"))

    clicker = asyncio.create_task(_click_deny())
    result = await executor.execute(
        tool, args={}, config_snapshot={"mission_id": "m-1"}, trace_id=tid,
    )
    await clicker

    assert result.success is False
    assert tool.calls == 0
    assert result.error == f"{APPROVAL_DENIED_PREFIX} (user_vetoed)"


async def test_interactive_timeout_is_not_reported_as_a_denial() -> None:
    """A silence and a "no" are different facts and must read differently.

    ``WorkerToolBroker`` classifies on ``startswith("approval-denied")``, so
    the old ``approval-denied (timeout)`` filed every unanswered mission call
    as a refusal by the maintainer.
    """
    executor, approval, _bus = _executor(timeout_s=0.05)
    tool = _AskTool()

    result = await executor.execute(
        tool, args={}, config_snapshot={"mission_id": "m-1"},
    )

    assert approval.waits == [0.05]
    assert result.success is False
    assert tool.calls == 0
    assert (result.error or "").startswith(APPROVAL_TIMEOUT_PREFIX)
    assert not (result.error or "").startswith(APPROVAL_DENIED_PREFIX)
    assert not (result.error or "").startswith(APPROVAL_UNAVAILABLE_PREFIX)


# ------------------------------------------------------------- conversational


async def test_conversational_surface_defers_for_a_two_turn_question() -> None:
    """Chat and voice ask instead of blocking, then run on the user's "ja"."""
    executor, approval, _bus = _executor()
    tool = _AskTool()
    tid = uuid4()

    deferred = await executor.execute(
        tool,
        args={"to": "tom"},
        config_snapshot={"approval_surface": CONVERSATIONAL},
        trace_id=tid,
    )

    assert approval.waits == []
    assert tool.calls == 0
    assert deferred.error == VOICE_CONFIRM_SENTINEL
    assert deferred.output["trace_id"] == str(tid)

    resumed = await executor.execute_confirmed(tid)
    assert resumed.success is True
    assert tool.calls == 1
    assert tool.last_ctx is not None
    assert tool.last_ctx.approved_by == "user"


async def test_conversational_veto_never_runs_the_action() -> None:
    executor, _approval, _bus = _executor()
    tool = _AskTool()
    tid = uuid4()

    await executor.execute(
        tool, args={}, config_snapshot={"voice_confirm": True}, trace_id=tid,
    )
    assert await executor.cancel_pending(tid) is True
    assert (await executor.execute_confirmed(tid)).success is False
    assert tool.calls == 0


# ------------------------------------------------------- nothing was widened


async def test_block_tier_is_still_blocked_on_every_surface() -> None:
    """Fail-fast is about WHO approves, never about what may run."""
    for snapshot in (
        None,
        {"voice_confirm": True},
        {"mission_id": "m-1"},
        {"approval_surface": UNATTENDED},
    ):
        executor, _approval, _bus = _executor()
        tool = _BlockTool()
        result = await executor.execute(tool, args={}, config_snapshot=snapshot)
        assert result.success is False
        assert tool.calls == 0
        assert "Blacklist match" in (result.error or "")


async def test_blacklist_still_beats_everything_when_unattended() -> None:
    safety = SafetyConfig()
    safety.blacklist.commands = ["*rm -rf /*"]
    safety.whitelist.commands = ["*"]
    executor, _approval, _bus = _executor(safety=safety)
    tool = _AskTool()

    result = await executor.execute(tool, args={"command": "rm -rf /"})

    assert result.success is False
    assert tool.calls == 0
    assert "Blacklist match" in (result.error or "")


async def test_whitelist_downgrade_still_runs_unattended() -> None:
    """A whitelisted tool needs no confirmation, so it has nothing to wait for."""
    safety = SafetyConfig()
    safety.whitelist.commands = ["gmail *"]
    executor, approval, _bus = _executor(safety=safety)
    tool = _AskTool()

    result = await executor.execute(tool, args={"to": "tom"})

    assert result.success is True
    assert tool.calls == 1
    assert approval.waits == []
    assert tool.last_ctx is not None
    assert tool.last_ctx.approved_by == "whitelist"


async def test_safe_tier_is_untouched_by_the_surface() -> None:
    executor, approval, _bus = _executor()
    tool = _SafeTool()
    result = await executor.execute(tool, args={})
    assert result.success is True
    assert tool.calls == 1
    assert approval.waits == []


async def test_the_model_cannot_declare_its_own_surface() -> None:
    """``args`` come from the LLM; the channel is read from the snapshot only.

    A tool argument named like the declaration key must be inert, or a prompt
    could talk the executor into a channel it does not have.
    """
    executor, _approval, _bus = _executor()
    tool = _AskTool()

    result = await executor.execute(
        tool,
        args={"approval_surface": CONVERSATIONAL, "voice_confirm": True},
        config_snapshot={},
    )

    assert result.error is not None
    assert result.error.startswith(APPROVAL_UNAVAILABLE_PREFIX)
    assert result.error != VOICE_CONFIRM_SENTINEL
    assert tool.calls == 0


async def test_unattended_dead_end_publishes_no_execution() -> None:
    """Nothing ran, so nothing may claim it did."""
    executor, _approval, bus = _executor()
    executed: list[ActionExecuted] = []

    async def _capture(event: ActionExecuted) -> None:
        executed.append(event)

    bus.subscribe(ActionExecuted, _capture)

    await executor.execute(_AskTool(), args={})

    assert executed == []


async def test_explicit_interactive_declaration_keeps_the_wait() -> None:
    """What the mission broker now passes, checked without a mission_id."""
    executor, approval, _bus = _executor(timeout_s=0.05)
    tool = _AskTool()

    result = await executor.execute(
        tool, args={}, config_snapshot={"approval_surface": INTERACTIVE},
    )

    assert approval.waits == [0.05]
    assert tool.calls == 0
    assert (result.error or "").startswith(APPROVAL_TIMEOUT_PREFIX)
