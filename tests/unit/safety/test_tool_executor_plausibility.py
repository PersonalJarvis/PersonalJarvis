"""Integration tests for the plausibility hook in ``ToolExecutor`` (Phase 4).

Not a full end-to-end voice test — just the three critical paths:

  1. Without ``plausibility_context_fn``: the workflow runs as before Phase 4.
  2. With a context provider + low confidence + an ``ask`` tool: approval is
     requested (even if the tier workflow would do so anyway).
  3. Whitelist downgrade: the plausibility check is skipped — otherwise
     the guard would break the whitelist logic.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import BrainPlausibilityConfig, SafetyConfig
from jarvis.core.protocols import ExecutionContext, ToolResult, Transcript
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor


class _FakeTool:
    name = "test_tool"
    risk_tier = "ask"
    schema: dict[str, Any] = {}

    def __init__(self) -> None:
        self.called = False

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.called = True
        return ToolResult(success=True, output="ok")


class _MonitorTool(_FakeTool):
    name = "monitor_tool"
    risk_tier = "monitor"


class _AutoApproval(ApprovalWorkflow):
    """Approval that always auto-approves — isolates the confirmation trigger under test."""

    def __init__(self, bus: EventBus, *, approve: bool = True) -> None:
        super().__init__(bus)
        self._approve = approve
        self.wait_calls = 0

    async def wait(self, trace_id: UUID, timeout_s: float) -> tuple[bool, str]:  # type: ignore[override]
        self.wait_calls += 1
        return self._approve, "auto-test"


def _executor_with_plausibility(
    *,
    context_fn: Any = None,
    safety: SafetyConfig | None = None,
    auto_approval: bool = True,
) -> tuple[ToolExecutor, _AutoApproval]:
    bus = EventBus()
    safety = safety or SafetyConfig()
    evaluator = RiskTierEvaluator(safety)
    approval = _AutoApproval(bus, approve=auto_approval)
    executor = ToolExecutor(
        bus=bus,
        evaluator=evaluator,
        approval=approval,
        plausibility_config=BrainPlausibilityConfig(),
        plausibility_context_fn=context_fn,
    )
    return executor, approval


@pytest.mark.asyncio
async def test_no_context_fn_runs_like_before() -> None:
    """Without a registered ``plausibility_context_fn`` no guard kicks in."""
    executor, approval = _executor_with_plausibility(context_fn=None)
    tool = _FakeTool()
    result = await executor.execute(tool, args={})
    # ``ask`` tier triggers approval anyway -> 1 call via the tier workflow.
    assert approval.wait_calls == 1
    assert result.success is True
    assert tool.called is True


@pytest.mark.asyncio
async def test_low_confidence_monitor_does_not_force_approval() -> None:
    """At ``monitor`` tier + low confidence: plausibility only logs, no approval."""
    transcript = Transcript(text="x", language="de", confidence=0.2)
    executor, approval = _executor_with_plausibility(
        context_fn=lambda: (transcript, 5.0),
    )
    tool = _MonitorTool()
    result = await executor.execute(tool, args={})
    # ``monitor`` tier doesn't trigger approval by itself, and plausibility
    # at monitor doesn't require anything either -> 0 approval calls.
    assert approval.wait_calls == 0
    assert result.success is True
    assert tool.called is True


@pytest.mark.asyncio
async def test_low_confidence_ask_with_normal_tier_workflow() -> None:
    """Low confidence + ask: approval is requested (tier OR plausibility)."""
    transcript = Transcript(text="x", language="de", confidence=0.2)
    executor, approval = _executor_with_plausibility(
        context_fn=lambda: (transcript, 5.0),
    )
    tool = _FakeTool()  # ask tier
    result = await executor.execute(tool, args={})
    assert approval.wait_calls == 1
    assert result.success is True


@pytest.mark.asyncio
async def test_whitelist_downgrade_skips_plausibility() -> None:
    """Whitelist downgrade -> plausibility check skipped.

    Mandate: "Whitelist-downgraded tools keep running without a plausibility
    check (otherwise the whitelist would be pointless)."
    """
    from jarvis.core.config import SafetyWhitelistConfig

    # The whitelist pattern must match against ``"<tool_name> <serialized_args>"``
    # — with empty ``args`` the evaluator strips the trailing
    # space, so we use a non-empty arg.
    safety = SafetyConfig(
        whitelist=SafetyWhitelistConfig(commands=["monitor_tool *"]),
    )
    transcript = Transcript(text="x", language="de", confidence=0.1)

    # We spy on the plausibility call via the context fn — if it's
    # NOT called, the executor took the whitelist-skip branch.
    calls: list[bool] = []

    def fake_context() -> tuple[Transcript, float]:
        calls.append(True)
        return transcript, 5.0

    executor, approval = _executor_with_plausibility(
        context_fn=fake_context,
        safety=safety,
    )
    tool = _MonitorTool()
    result = await executor.execute(tool, args={"target": "foo"})
    # Whitelist downgraded ``monitor_tool`` to ``safe`` -> no approval.
    assert approval.wait_calls == 0
    # Context fn must NOT be called, because of the whitelist skip.
    assert calls == []
    assert result.success is True


@pytest.mark.asyncio
async def test_high_confidence_recent_wake_no_extra_confirmation() -> None:
    """With good plausibility, no extra approval is requested."""
    transcript = Transcript(text="x", language="de", confidence=0.9)
    executor, approval = _executor_with_plausibility(
        context_fn=lambda: (transcript, 2.0),
    )
    tool = _MonitorTool()
    result = await executor.execute(tool, args={})
    # ``monitor`` + plausibility=ok -> no approval.
    assert approval.wait_calls == 0
    assert result.success is True


@pytest.mark.asyncio
async def test_context_fn_exception_is_swallowed() -> None:
    """If the context provider crashes, the executor falls back to "no check"."""
    def broken_fn() -> tuple[Transcript, float]:
        raise RuntimeError("context-provider down")

    executor, approval = _executor_with_plausibility(context_fn=broken_fn)
    tool = _MonitorTool()
    result = await executor.execute(tool, args={})
    # Despite the crash: the tool runs, no approval.
    assert approval.wait_calls == 0
    assert result.success is True
