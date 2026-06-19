"""Two-turn voice/chat confirmation deferral in the ``ToolExecutor``.

Root cause (2026-06-18, session 2995997b): an ``ask``-tier tool on the voice path
blocks in ``ApprovalWorkflow.wait()`` for a UI approval no conversational user can
give; the turn is then beheaded with a misleading "took too long" phrase. Fix: on
a conversational turn (``config_snapshot["voice_confirm"] = True``) the executor
does NOT block — it stashes the pending action and returns a sentinel so the brain
can SPEAK a confirmation question and end the turn. The next "ja" re-runs the
stashed action via ``execute_confirmed``; a "nein" drops it via ``cancel_pending``.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionExecuted
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL, ToolExecutor


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
    name = "safe_tool"
    risk_tier = "safe"


class _BlockingApproval(ApprovalWorkflow):
    """Records whether ``wait()`` was awaited (it must NOT be on a deferral)."""

    def __init__(self, bus: EventBus) -> None:
        super().__init__(bus)
        self.wait_calls = 0

    async def wait(self, trace_id: UUID, timeout_s: float) -> tuple[bool, str]:  # type: ignore[override]
        self.wait_calls += 1
        return True, "auto-test"


def _executor() -> tuple[ToolExecutor, _BlockingApproval, EventBus]:
    bus = EventBus()
    evaluator = RiskTierEvaluator(SafetyConfig())
    approval = _BlockingApproval(bus)
    executor = ToolExecutor(bus=bus, evaluator=evaluator, approval=approval)
    return executor, approval, bus


@pytest.mark.asyncio
async def test_voice_confirm_defers_instead_of_blocking() -> None:
    executor, approval, _bus = _executor()
    tool = _AskTool()
    tid = uuid4()
    result = await executor.execute(
        tool, args={"to": "tom"},
        config_snapshot={"voice_confirm": True},
        trace_id=tid,
    )
    # Deferred: never blocked on approval, never ran the action.
    assert approval.wait_calls == 0
    assert tool.calls == 0
    # Sentinel result carries what the brain needs to phrase + resume.
    assert result.success is False
    assert result.error == VOICE_CONFIRM_SENTINEL
    assert result.output["tool_name"] == "gmail"
    assert result.output["trace_id"] == str(tid)


@pytest.mark.asyncio
async def test_execute_confirmed_runs_the_stashed_action() -> None:
    executor, _approval, bus = _executor()
    seen: list[ActionExecuted] = []
    bus.subscribe(ActionExecuted, lambda e: seen.append(e))  # type: ignore[arg-type]
    tool = _AskTool()
    tid = uuid4()
    await executor.execute(
        tool, args={"to": "tom"},
        config_snapshot={"voice_confirm": True}, trace_id=tid,
    )
    result = await executor.execute_confirmed(tid)
    assert result.success is True
    assert result.output == "sent"
    assert tool.calls == 1
    # Ran with user authority + published an ActionExecuted for the audit trail.
    assert tool.last_ctx is not None and tool.last_ctx.approved_by == "user"
    await _drain(bus)
    assert any(e.tool_name == "gmail" and e.success for e in seen)


@pytest.mark.asyncio
async def test_execute_confirmed_is_single_use() -> None:
    executor, _approval, _bus = _executor()
    tool = _AskTool()
    tid = uuid4()
    await executor.execute(
        tool, args={}, config_snapshot={"voice_confirm": True}, trace_id=tid,
    )
    await executor.execute_confirmed(tid)
    # Second resume must NOT re-run the action (no double-send).
    again = await executor.execute_confirmed(tid)
    assert again.success is False
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_execute_confirmed_unknown_trace_is_expired() -> None:
    executor, _approval, _bus = _executor()
    result = await executor.execute_confirmed(uuid4())
    assert result.success is False


@pytest.mark.asyncio
async def test_cancel_pending_drops_the_action() -> None:
    executor, _approval, _bus = _executor()
    tool = _AskTool()
    tid = uuid4()
    await executor.execute(
        tool, args={}, config_snapshot={"voice_confirm": True}, trace_id=tid,
    )
    assert await executor.cancel_pending(tid) is True
    # After veto the action is gone — a later resume cannot run it.
    result = await executor.execute_confirmed(tid)
    assert result.success is False
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_without_voice_confirm_ask_tier_still_blocks_on_approval() -> None:
    """Regression guard: the non-conversational path is unchanged."""
    executor, approval, _bus = _executor()
    tool = _AskTool()
    result = await executor.execute(tool, args={})  # no voice_confirm
    assert approval.wait_calls == 1
    assert tool.calls == 1
    assert result.success is True


@pytest.mark.asyncio
async def test_safe_tier_is_not_deferred_even_with_voice_confirm() -> None:
    """A tool that needs no confirmation runs immediately, never deferred."""
    executor, approval, _bus = _executor()
    tool = _SafeTool()
    result = await executor.execute(
        tool, args={}, config_snapshot={"voice_confirm": True},
    )
    assert approval.wait_calls == 0
    assert tool.calls == 1
    assert result.success is True
    assert result.error != VOICE_CONFIRM_SENTINEL


@pytest.mark.asyncio
async def test_gmail_read_is_not_deferred_for_voice_confirm() -> None:
    """Repro 2026-06-19 (session dc533e39): a read-only gmail call (the
    morning-routine "check unread mail" step) must NOT trigger the send
    confirmation on a voice turn. Before the per-action risk fix the whole
    gmail tool was ask-tier, so "Was habe ich heute auf dem Plan?" produced
    "Soll ich die E-Mail wirklich senden?"."""
    import httpx

    from jarvis.plugins.tool.gmail_rest import GmailRestTool

    executor, approval, _bus = _executor()
    tool = GmailRestTool(
        access_token_provider=lambda: "at_x",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"messages": []})
        ),
    )
    result = await executor.execute(
        tool,
        args={"action": "list_messages"},
        config_snapshot={"voice_confirm": True},
        trace_id=uuid4(),
    )
    # A read runs straight through — never deferred, never blocked.
    assert result.error != VOICE_CONFIRM_SENTINEL
    assert result.success is True
    assert approval.wait_calls == 0


@pytest.mark.asyncio
async def test_gmail_send_still_defers_for_voice_confirm() -> None:
    """Sending stays consequential: it must still confirm before sending."""
    from jarvis.plugins.tool.gmail_rest import GmailRestTool

    executor, approval, _bus = _executor()
    tool = GmailRestTool(access_token_provider=lambda: "at_x")
    result = await executor.execute(
        tool,
        args={"action": "send_message", "to": "a@b.com", "body": "hi"},
        config_snapshot={"voice_confirm": True},
        trace_id=uuid4(),
    )
    assert result.error == VOICE_CONFIRM_SENTINEL
    assert result.output["tool_name"] == "gmail"
    assert approval.wait_calls == 0  # deferred for two-turn confirm, not blocked


async def _drain(bus: EventBus) -> None:
    import asyncio
    await asyncio.sleep(0)
    await asyncio.sleep(0)
