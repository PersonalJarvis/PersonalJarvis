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
    secret = "sk-proj-AbCdEf0123456789ghijKLmnopQRstuv"  # noqa: S105 — fake fixture key
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


@pytest.mark.asyncio
async def test_mission_attribution_rides_action_events() -> None:
    """The ADR-0025 gateway stamps mission_id/worker_id into config_snapshot;
    the executor must carry both onto every Action* event so the Sub-Agents
    board can attach the call to the right worker row."""
    bus = EventBus()
    proposed: list[ActionProposed] = []
    executed: list[ActionExecuted] = []

    async def _cap_p(e: ActionProposed) -> None:
        proposed.append(e)

    async def _cap_x(e: ActionExecuted) -> None:
        executed.append(e)

    bus.subscribe(ActionProposed, _cap_p)  # type: ignore[arg-type]
    bus.subscribe(ActionExecuted, _cap_x)  # type: ignore[arg-type]
    await _executor(bus).execute(
        _SafeTool(),
        args={},
        trace_id=uuid4(),
        config_snapshot={"mission_id": "m-123", "worker_id": "w-456"},
    )
    await _drain(bus)
    assert proposed and proposed[0].mission_id == "m-123"
    assert proposed[0].worker_id == "w-456"
    assert executed and executed[0].mission_id == "m-123"
    assert executed[0].worker_id == "w-456"


@pytest.mark.asyncio
async def test_mainline_calls_carry_no_attribution() -> None:
    """A normal chat/voice turn has no mission context — both attribution
    fields must stay None so the board's mission gate filters it out."""
    bus = EventBus()
    seen: list[ActionExecuted] = []

    async def _cap(e: ActionExecuted) -> None:
        seen.append(e)

    bus.subscribe(ActionExecuted, _cap)  # type: ignore[arg-type]
    await _executor(bus).execute(_SafeTool(), args={}, trace_id=uuid4())
    await _drain(bus)
    assert seen and seen[0].mission_id is None
    assert seen[0].worker_id is None
