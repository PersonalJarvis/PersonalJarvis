"""Session-Decision-Log capture in the ``ToolExecutor``.

The executor is the one authorized tool chokepoint, so it is where the two
decision-log data points are captured: the tool's output (onto
``ActionExecuted.output_preview``) and the brain's rationale (onto
``ActionProposed.rationale``). Both must be redacted + capped at publish time so
no raw secret rides the bus into the session DB / local diary.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionExecuted, ActionProposed
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor


class _SafeTool:
    name = "cli_gcloud"
    risk_tier = "safe"
    schema: dict[str, Any] = {}

    def __init__(self, output: Any = "ok") -> None:
        self._output = output

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, output=self._output)


def _executor(bus: EventBus) -> ToolExecutor:
    return ToolExecutor(
        bus=bus,
        evaluator=RiskTierEvaluator(SafetyConfig()),
        approval=ApprovalWorkflow(bus),
    )


async def _drain(bus: EventBus) -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_output_preview_is_published_on_action_executed() -> None:
    bus = EventBus()
    seen: list[ActionExecuted] = []

    async def _cap(e: ActionExecuted) -> None:
        seen.append(e)

    bus.subscribe(ActionExecuted, _cap)  # type: ignore[arg-type]
    await _executor(bus).execute(
        _SafeTool(output="Billing for project alpha: 12.40 EUR"), args={}, trace_id=uuid4(),
    )
    await _drain(bus)
    assert seen and seen[0].output_preview == "Billing for project alpha: 12.40 EUR"


@pytest.mark.asyncio
async def test_output_preview_is_redacted() -> None:
    bus = EventBus()
    seen: list[ActionExecuted] = []

    async def _cap(e: ActionExecuted) -> None:
        seen.append(e)

    bus.subscribe(ActionExecuted, _cap)  # type: ignore[arg-type]
    secret = "sk-proj-AbCdEf0123456789ghijKLmnopQRstuv"
    await _executor(bus).execute(
        _SafeTool(output=f"token echoed back: {secret}"), args={}, trace_id=uuid4(),
    )
    await _drain(bus)
    assert seen
    assert secret not in seen[0].output_preview
    assert "<redacted:openai_key>" in seen[0].output_preview


@pytest.mark.asyncio
async def test_output_preview_is_length_capped() -> None:
    bus = EventBus()
    seen: list[ActionExecuted] = []

    async def _cap(e: ActionExecuted) -> None:
        seen.append(e)

    bus.subscribe(ActionExecuted, _cap)  # type: ignore[arg-type]
    # Spaced prose: long, but not one credential-shaped 64+ char run.
    await _executor(bus).execute(
        _SafeTool(output="result row " * 1000), args={}, trace_id=uuid4(),
    )
    await _drain(bus)
    assert seen
    assert len(seen[0].output_preview) < 11_000
    assert "more chars)" in seen[0].output_preview


@pytest.mark.asyncio
async def test_rationale_is_published_on_action_proposed() -> None:
    bus = EventBus()
    seen: list[ActionProposed] = []

    async def _cap(e: ActionProposed) -> None:
        seen.append(e)

    bus.subscribe(ActionProposed, _cap)  # type: ignore[arg-type]
    why = "You asked for your GCP spend, so I call the billing CLI instead of guessing."
    await _executor(bus).execute(
        _SafeTool(), args={}, trace_id=uuid4(), rationale=why,
    )
    await _drain(bus)
    assert seen and seen[0].rationale == why


@pytest.mark.asyncio
async def test_rationale_defaults_empty_when_not_supplied() -> None:
    bus = EventBus()
    seen: list[ActionProposed] = []

    async def _cap(e: ActionProposed) -> None:
        seen.append(e)

    bus.subscribe(ActionProposed, _cap)  # type: ignore[arg-type]
    await _executor(bus).execute(_SafeTool(), args={}, trace_id=uuid4())
    await _drain(bus)
    assert seen and seen[0].rationale == ""
