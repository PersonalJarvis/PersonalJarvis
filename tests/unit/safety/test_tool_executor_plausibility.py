"""Integration-Tests fuer den Plausibility-Hook im ``ToolExecutor`` (Phase 4).

Kein voller End-to-End-Voice-Test — nur die drei kritischen Pfade:

  1. Ohne ``plausibility_context_fn``: Workflow laeuft wie vor Phase 4.
  2. Mit Context-Provider + low confidence + ``ask``-Tool: Approval wird
     angefordert (auch wenn der Tier-Workflow das ohnehin tun wuerde).
  3. Whitelist-Downgrade: Plausibility-Check wird uebersprungen — sonst
     wuerde der Guard die Whitelist-Logik kaputtmachen.
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
    """Approval die immer auto-approved — Test isoliert das Confirmation-Trigger."""

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
    """Ohne registrierte ``plausibility_context_fn`` greift kein Guard."""
    executor, approval = _executor_with_plausibility(context_fn=None)
    tool = _FakeTool()
    result = await executor.execute(tool, args={})
    # ``ask``-Tier triggert ohnehin Approval -> 1 Call durch Tier-Workflow.
    assert approval.wait_calls == 1
    assert result.success is True
    assert tool.called is True


@pytest.mark.asyncio
async def test_low_confidence_monitor_does_not_force_approval() -> None:
    """Bei ``monitor``-Tier + low confidence: Plausibility logged nur, kein Approval."""
    transcript = Transcript(text="x", language="de", confidence=0.2)
    executor, approval = _executor_with_plausibility(
        context_fn=lambda: (transcript, 5.0),
    )
    tool = _MonitorTool()
    result = await executor.execute(tool, args={})
    # ``monitor``-Tier triggert nicht von sich aus Approval, und Plausibility
    # bei monitor verlangt auch nichts -> 0 Approval-Calls.
    assert approval.wait_calls == 0
    assert result.success is True
    assert tool.called is True


@pytest.mark.asyncio
async def test_low_confidence_ask_with_normal_tier_workflow() -> None:
    """Low confidence + ask: Approval wird angefordert (Tier ODER Plausibility)."""
    transcript = Transcript(text="x", language="de", confidence=0.2)
    executor, approval = _executor_with_plausibility(
        context_fn=lambda: (transcript, 5.0),
    )
    tool = _FakeTool()  # ask-Tier
    result = await executor.execute(tool, args={})
    assert approval.wait_calls == 1
    assert result.success is True


@pytest.mark.asyncio
async def test_whitelist_downgrade_skips_plausibility() -> None:
    """Whitelist-Downgrade -> Plausibility-Check uebersprungen.

    Mandat: "Whitelist-downgraded Tools laufen weiter ohne Plausibility-Check
    (sonst ist Whitelist sinnlos)."
    """
    from jarvis.core.config import SafetyWhitelistConfig

    # Whitelist-Pattern muss gegen ``"<tool_name> <serialized_args>"``
    # matchen — bei leerem ``args`` strippt der Evaluator das trailing
    # Space, also nutzen wir ein nicht-leeres Arg.
    safety = SafetyConfig(
        whitelist=SafetyWhitelistConfig(commands=["monitor_tool *"]),
    )
    transcript = Transcript(text="x", language="de", confidence=0.1)

    # Wir spy'en den Plausibility-Aufruf via den Context-Fn — wenn er
    # NICHT aufgerufen wird, hat der Executor das Whitelist-Skip-Branch
    # genommen.
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
    # Whitelist downgraded ``monitor_tool`` zu ``safe`` -> kein Approval.
    assert approval.wait_calls == 0
    # Context-Fn darf NICHT aufgerufen werden, weil Whitelist-Skip.
    assert calls == []
    assert result.success is True


@pytest.mark.asyncio
async def test_high_confidence_recent_wake_no_extra_confirmation() -> None:
    """Bei guter Plausibility wird kein extra Approval angefordert."""
    transcript = Transcript(text="x", language="de", confidence=0.9)
    executor, approval = _executor_with_plausibility(
        context_fn=lambda: (transcript, 2.0),
    )
    tool = _MonitorTool()
    result = await executor.execute(tool, args={})
    # ``monitor`` + plausibility=ok -> kein Approval.
    assert approval.wait_calls == 0
    assert result.success is True


@pytest.mark.asyncio
async def test_context_fn_exception_is_swallowed() -> None:
    """Wenn der Context-Provider crasht, faellt der Executor auf "kein Check" zurueck."""
    def broken_fn() -> tuple[Transcript, float]:
        raise RuntimeError("context-provider down")

    executor, approval = _executor_with_plausibility(context_fn=broken_fn)
    tool = _MonitorTool()
    result = await executor.execute(tool, args={})
    # Trotz crash: Tool laeuft, kein Approval.
    assert approval.wait_calls == 0
    assert result.success is True
