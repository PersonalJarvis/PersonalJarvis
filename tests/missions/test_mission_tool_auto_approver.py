"""MissionToolAutoApprover — mission-scoped tool pre-authorization (ADR-0031).

While a mission's broker grant is live, ask-tier calls for tools in
``granted ∩ [phase6.safety].auto_approve_tool_families`` are answered on the
bus (ActionApproved with a ``mission-grant:`` audit label) — the gate is
answered, never bypassed. Everything else keeps today's behavior: block and
deny on timeout. Arming is owned by the WorkerToolBroker and lives exactly as
long as the grant.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.config import Phase6SafetyConfig, SafetyConfig
from jarvis.core.events import ActionApprovalRequired, ActionApproved
from jarvis.core.protocols import (
    ExecutionContext,
    SupervisorToolDescriptor,
    SupervisorToolRequest,
    ToolResult,
)
from jarvis.missions.tool_approvals import MissionToolAutoApprover
from jarvis.missions.workers.capabilities import WorkerCapabilityInventory
from jarvis.missions.workers.worker_tool_broker import _BROKER
from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor


async def _collect_approvals(bus: EventBus) -> list[ActionApproved]:
    got: list[ActionApproved] = []

    async def _cap(ev: object) -> None:
        if isinstance(ev, ActionApproved):
            got.append(ev)

    bus.subscribe_all(_cap)
    return got


def _required(
    *,
    mission_id: str | None,
    tool_name: str,
    risk_tier: str = "ask",
    reason: str = "risk_tier",
) -> ActionApprovalRequired:
    return ActionApprovalRequired(
        trace_id=uuid4(),
        tool_name=tool_name,
        risk_tier=risk_tier,  # type: ignore[arg-type]
        reason=reason,
        mission_id=mission_id,
        worker_id="w1",
    )


# ------------------------------------------------------------- unit level


async def test_approves_armed_tool_for_its_mission() -> None:
    bus = EventBus()
    approver = MissionToolAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    approver.arm("m1", "grant-a", frozenset({"external-action"}))

    await bus.publish(_required(mission_id="m1", tool_name="external-action"))
    await asyncio.sleep(0)

    assert len(approvals) == 1
    assert approvals[0].tool_name == "external-action"
    assert approvals[0].approved_by == "mission-grant:m1"


async def test_ignores_unarmed_mission_and_missing_mission_id() -> None:
    bus = EventBus()
    approver = MissionToolAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    approver.arm("m1", "grant-a", frozenset({"external-action"}))

    await bus.publish(_required(mission_id="other", tool_name="external-action"))
    await bus.publish(_required(mission_id=None, tool_name="external-action"))
    await asyncio.sleep(0)

    assert approvals == []


async def test_ignores_tool_outside_the_armed_set() -> None:
    bus = EventBus()
    approver = MissionToolAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    approver.arm("m1", "grant-a", frozenset({"external-action"}))

    await bus.publish(_required(mission_id="m1", tool_name="gmail/send_message"))
    await asyncio.sleep(0)

    assert approvals == []


async def test_forbidden_broker_names_are_never_approved_even_if_armed() -> None:
    """Defense in depth: the broker denylist is re-checked at approval time,
    so a bad arm can never authorize run-shell / spawn / credential tools."""
    bus = EventBus()
    approver = MissionToolAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    approver.arm(
        "m1",
        "grant-a",
        frozenset({"run-shell", "spawn-worker", "reveal-key-tool"}),
    )

    for name in ("run-shell", "spawn-worker", "reveal-key-tool"):
        await bus.publish(_required(mission_id="m1", tool_name=name))
    await asyncio.sleep(0)

    assert approvals == []


async def test_never_answers_block_tier_or_foreign_reasons() -> None:
    bus = EventBus()
    approver = MissionToolAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    approver.arm("m1", "grant-a", frozenset({"external-action"}))

    await bus.publish(
        _required(mission_id="m1", tool_name="external-action", risk_tier="block")
    )
    await bus.publish(
        _required(mission_id="m1", tool_name="external-action", reason="something-else")
    )
    await asyncio.sleep(0)

    assert approvals == []


async def test_plausibility_reason_is_answered() -> None:
    """The plausibility guard is a voice heuristic — meaningless for an
    unattended worker, so a plausibility-escalated call is answered too."""
    bus = EventBus()
    approver = MissionToolAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    approver.arm("m1", "grant-a", frozenset({"external-action"}))

    await bus.publish(
        _required(
            mission_id="m1",
            tool_name="external-action",
            risk_tier="monitor",
            reason="plausibility",
        )
    )
    await asyncio.sleep(0)

    assert len(approvals) == 1


async def test_server_prefix_grant_covers_namespaced_tool() -> None:
    bus = EventBus()
    approver = MissionToolAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    approver.arm("m1", "grant-a", frozenset({"github"}))

    await bus.publish(_required(mission_id="m1", tool_name="github/list_issues"))
    await asyncio.sleep(0)

    assert len(approvals) == 1


async def test_disarm_is_per_grant_not_per_mission() -> None:
    """Two live grants for one mission: disarming one must not kill the other;
    disarming both must."""
    bus = EventBus()
    approver = MissionToolAutoApprover(bus)
    approvals = await _collect_approvals(bus)
    approver.arm("m1", "grant-a", frozenset({"external-action"}))
    approver.arm("m1", "grant-b", frozenset({"other-tool"}))

    approver.disarm("m1", "grant-b")
    await bus.publish(_required(mission_id="m1", tool_name="external-action"))
    await asyncio.sleep(0)
    assert len(approvals) == 1

    approver.disarm("m1", "grant-a")
    await bus.publish(_required(mission_id="m1", tool_name="external-action"))
    await asyncio.sleep(0)
    assert len(approvals) == 1  # unchanged — fully disarmed


# ------------------------------------------------ end-to-end ToolExecutor


class _AskTool:
    name = "external-action"
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


async def test_armed_mission_call_executes_without_human() -> None:
    bus = EventBus()
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(SafetyConfig()),
        ApprovalWorkflow(bus, timeout_s=0.05),
        default_timeout_s=0.05,
    )
    approver = MissionToolAutoApprover(bus)
    tool = _AskTool()
    approver.arm("m-e2e", "grant-a", frozenset({tool.name}))

    result = await executor.execute(
        tool,
        {},
        config_snapshot={"mission_id": "m-e2e", "worker_id": "w1", "voice_confirm": False},
    )

    assert result.success is True
    assert tool.calls == 1
    assert tool.approved_by == "mission-grant:m-e2e"


async def test_unarmed_mission_call_still_denies_on_timeout() -> None:
    bus = EventBus()
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(SafetyConfig()),
        ApprovalWorkflow(bus, timeout_s=0.05),
        default_timeout_s=0.05,
    )
    MissionToolAutoApprover(bus)  # constructed but never armed
    tool = _AskTool()

    result = await executor.execute(
        tool,
        {},
        config_snapshot={"mission_id": "m-e2e", "worker_id": "w1", "voice_confirm": False},
    )

    assert result.success is False
    assert "approval-denied" in (result.error or "")
    assert tool.calls == 0


# ------------------------------------------------- broker arm/disarm wiring


class _Gateway:
    def catalog(self) -> tuple[SupervisorToolDescriptor, ...]:
        return (
            SupervisorToolDescriptor(
                name="external-action",
                description="A consequential external action.",
                input_schema={"type": "object", "properties": {}},
                risk_tier="ask",
            ),
            SupervisorToolDescriptor(
                name="other-tool",
                description="Another tool, not allowlisted.",
                input_schema={"type": "object", "properties": {}},
                risk_tier="ask",
            ),
        )

    @property
    def catalog_version(self) -> int:
        return 1

    async def execute(
        self,
        _name: str,
        _arguments: dict[str, Any],
        _request: SupervisorToolRequest,
    ) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _RecorderApprover:
    def __init__(self) -> None:
        self.armed: list[tuple[str, str, set[str]]] = []
        self.disarmed: list[tuple[str, str]] = []

    def arm(self, mission_id: str, grant_key: str, names: frozenset[str]) -> None:
        self.armed.append((mission_id, grant_key, set(names)))

    def disarm(self, mission_id: str, grant_key: str) -> None:
        self.disarmed.append((mission_id, grant_key))


@pytest.fixture
def _clean_broker_state(monkeypatch: pytest.MonkeyPatch):
    runtime_refs._reset_for_tests()
    yield
    _BROKER.reset_for_tests()
    runtime_refs._reset_for_tests()


async def test_broker_grant_arms_intersection_and_disarms_on_close(
    monkeypatch: pytest.MonkeyPatch,
    _clean_broker_state: None,
) -> None:
    recorder = _RecorderApprover()
    runtime_refs.set_supervisor_tool_gateway(_Gateway())
    runtime_refs.set_mission_tool_auto_approver(recorder)
    cfg = SimpleNamespace(
        phase6=SimpleNamespace(
            safety=Phase6SafetyConfig(
                worker_tool_auto_approve=True,
                auto_approve_tool_families=["external-action"],
            )
        )
    )
    monkeypatch.setattr("jarvis.core.config.load_config", lambda *a, **k: cfg)

    inventory = WorkerCapabilityInventory.build(
        native_tool_names=("external-action", "other-tool"),
        task_text="Perform the external action.",
    )
    binding = inventory.bind_broker(ttl_s=60.0, mission_id="m1", worker_id="w1")
    assert binding.available

    assert len(recorder.armed) == 1
    mission_id, grant_key, names = recorder.armed[0]
    assert mission_id == "m1"
    # Intersection: granted ∩ allowlist — the non-allowlisted grant member
    # must not be armed.
    assert names == {"external-action"}

    binding.close()
    assert recorder.disarmed == [("m1", grant_key)]


async def test_broker_arm_is_empty_when_feature_disabled(
    monkeypatch: pytest.MonkeyPatch,
    _clean_broker_state: None,
) -> None:
    recorder = _RecorderApprover()
    runtime_refs.set_supervisor_tool_gateway(_Gateway())
    runtime_refs.set_mission_tool_auto_approver(recorder)
    cfg = SimpleNamespace(
        phase6=SimpleNamespace(
            safety=Phase6SafetyConfig(
                worker_tool_auto_approve=False,
                auto_approve_tool_families=["external-action"],
            )
        )
    )
    monkeypatch.setattr("jarvis.core.config.load_config", lambda *a, **k: cfg)

    inventory = WorkerCapabilityInventory.build(
        native_tool_names=("external-action",),
        task_text="Perform the external action.",
    )
    binding = inventory.bind_broker(ttl_s=60.0, mission_id="m1", worker_id="w1")

    assert len(recorder.armed) == 1
    assert recorder.armed[0][2] == set()  # symmetric no-op arm, nothing approved
    binding.close()


# --------------------------------------------------------- config surface


def test_shipped_default_allowlist_is_read_only_knowledge() -> None:
    safety = Phase6SafetyConfig()
    assert safety.worker_tool_auto_approve is True
    assert set(safety.auto_approve_tool_families) == {
        "search_web",
        "wiki-list",
        "wiki-recall",
        "wiki-page-read",
        "wiki-ingest",
        "session-latest-turn",
    }
    # No server-family prefixes in the shipped default (a prefix silently
    # authorizes every future tool that server adds).
    assert not any(entry.endswith("/") for entry in safety.auto_approve_tool_families)


def test_safety_config_carries_approval_timeout() -> None:
    assert SafetyConfig().tool_approval_timeout_s == 60.0
