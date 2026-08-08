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
from jarvis.core.events import ActionApprovalRequired, ActionExecuted
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


class _DescribingTool(_AskTool):
    """Tool with the optional ``describe_args`` impact-summary hook."""

    name = "run_shell"

    def describe_args(self, args: dict[str, Any]) -> dict[str, str]:
        return {"level": "destructive", "commands": "rm"}


class _BrokenDescribeTool(_AskTool):
    name = "run_shell"

    def describe_args(self, args: dict[str, Any]) -> dict[str, str]:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_sentinel_carries_the_impact_summary() -> None:
    # Explain layer (2026-08-08): a tool may summarize WHAT the deferred
    # action would do; the sentinel forwards it for plain-language phrasing.
    executor, _approval, _bus = _executor()
    result = await executor.execute(
        _DescribingTool(), args={"command": "rm -rf x"},
        config_snapshot={"voice_confirm": True}, trace_id=uuid4(),
    )
    assert result.error == VOICE_CONFIRM_SENTINEL
    assert result.output["impact"] == {"level": "destructive", "commands": "rm"}


@pytest.mark.asyncio
async def test_broken_describe_args_never_blocks_the_deferral() -> None:
    executor, _approval, _bus = _executor()
    result = await executor.execute(
        _BrokenDescribeTool(), args={"command": "rm -rf x"},
        config_snapshot={"voice_confirm": True}, trace_id=uuid4(),
    )
    assert result.error == VOICE_CONFIRM_SENTINEL
    assert "impact" not in result.output


class _IntentTool(_AskTool):
    """Ask-tier tool whose ``intent_confirms_args`` mirrors run_shell's."""

    name = "run_shell"

    def intent_confirms_args(self, args: dict[str, Any], utterance: str) -> bool:
        return "lösch" in utterance.lower()  # i18n-allow — speech-input stem


@pytest.mark.asyncio
async def test_explicit_intent_skips_the_confirmation() -> None:
    # Claude-Code permission model: the user asked for the deletion themselves —
    # the ask-tier action runs immediately, recorded as explicit-intent.
    executor, approval, _bus = _executor()
    tool = _IntentTool()
    result = await executor.execute(
        tool, args={"command": "rm -rf x"},
        user_utterance="Lösch den Ordner x.",  # i18n-allow — spoken German turn
        config_snapshot={"voice_confirm": True},
    )
    assert result.success is True
    assert tool.calls == 1
    assert approval.wait_calls == 0
    assert tool.last_ctx is not None
    assert tool.last_ctx.approved_by == "explicit-intent"


@pytest.mark.asyncio
async def test_brain_initiated_destruction_still_defers() -> None:
    # Same tool, but the utterance never asked for a deletion — the two-turn
    # confirmation flow stays.
    executor, _approval, _bus = _executor()
    tool = _IntentTool()
    result = await executor.execute(
        tool, args={"command": "rm -rf x"},
        user_utterance="mach das Projekt startklar",
        config_snapshot={"voice_confirm": True},
    )
    assert result.error == VOICE_CONFIRM_SENTINEL
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_empty_utterance_never_skips_the_confirmation() -> None:
    # API/mission calls carry no spoken turn — explicit intent cannot apply.
    executor, _approval, _bus = _executor()
    tool = _IntentTool()
    result = await executor.execute(
        tool, args={"command": "rm -rf x"},
        config_snapshot={"voice_confirm": True},
    )
    assert result.error == VOICE_CONFIRM_SENTINEL
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_broken_intent_hook_keeps_the_confirmation() -> None:
    class _BrokenIntentTool(_AskTool):
        def intent_confirms_args(self, args: dict[str, Any], utterance: str) -> bool:
            raise RuntimeError("boom")

    executor, _approval, _bus = _executor()
    tool = _BrokenIntentTool()
    result = await executor.execute(
        tool, args={}, user_utterance="lösch alles",  # i18n-allow
        config_snapshot={"voice_confirm": True},
    )
    assert result.error == VOICE_CONFIRM_SENTINEL
    assert tool.calls == 0


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
async def test_non_conversational_approval_request_is_safe_and_correlated() -> None:
    executor, _approval, bus = _executor()
    requests: list[ActionApprovalRequired] = []

    async def _capture(event: ActionApprovalRequired) -> None:
        requests.append(event)

    bus.subscribe(ActionApprovalRequired, _capture)
    tool = _AskTool()
    trace_id = uuid4()

    result = await executor.execute(
        tool,
        args={"to": "person@example.test", "api_key": "sk-secret-value"},
        config_snapshot={
            "voice_confirm": False,
            "mission_id": "mission-123",
            "worker_id": "worker-456",
        },
        trace_id=trace_id,
    )

    assert result.success is True
    assert len(requests) == 1
    request = requests[0]
    assert request.trace_id == trace_id
    assert request.tool_name == tool.name
    assert request.reason == "risk_tier"
    assert request.mission_id == "mission-123"
    assert request.worker_id == "worker-456"
    assert request.expires_at_ns > request.timestamp_ns
    assert "sk-secret-value" not in request.args_preview


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
    gmail tool was ask-tier, so this input and response reproduced it:
    "Was habe ich heute auf dem Plan?"  # i18n-allow: quoted runtime voice input
    "Soll ich die E-Mail wirklich senden?"  # i18n-allow: quoted runtime voice output
    """
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
