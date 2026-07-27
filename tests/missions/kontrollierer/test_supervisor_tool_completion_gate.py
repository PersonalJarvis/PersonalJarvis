"""Supervisor-tool completion certificate: integrity gates, refusals inform.

Two failure classes, two different consequences (BUG-096 four-facts rule):

* INTEGRITY failures (a call still running when the grant closed /
  ``outcome_unknown``) — the supervisor cannot certify what happened, so the
  iteration is aborted and the critic never runs.
* OBSERVED refusals (denied / timed-out / errored calls) — the worker saw the
  error in-stream and could adapt, so the iteration stays reviewable and the
  critic receives the refusal list as context.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from jarvis.core import runtime_refs
from jarvis.core.protocols import (
    SupervisorToolDescriptor,
    SupervisorToolRequest,
    ToolResult,
)
from jarvis.missions.kontrollierer.decomposer import MissionPlan, Step
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from jarvis.missions.workers.capabilities import WorkerCapabilityInventory

from .test_loop import (
    FakeCriticRunner,
    _FakeWorkerEvent,
    _make_approve_verdict,
    _make_kontrollierer,
)


class _Gateway:
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.calls = 0

    def catalog(self) -> tuple[SupervisorToolDescriptor, ...]:
        return (
            SupervisorToolDescriptor(
                name="external-action",
                description="A consequential external action.",
                input_schema={"type": "object", "properties": {}},
                risk_tier="monitor",
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
        self.calls += 1
        return self.result


class _BlockingGateway(_Gateway):
    def __init__(self) -> None:
        super().__init__(ToolResult(success=True, output="unreachable"))
        self.started = asyncio.Event()

    async def execute(
        self,
        _name: str,
        _arguments: dict[str, Any],
        _request: SupervisorToolRequest,
    ) -> ToolResult:
        self.calls += 1
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("the revoked call must never resume")


class _BrokerCallingWorker:
    cli = "claude"
    last_pid = 1234

    def __init__(self, *, leave_pending: bool = False) -> None:
        self.leave_pending = leave_pending
        self.pending_tasks: list[asyncio.Task[dict[str, Any]]] = []
        self.capability_inventory = WorkerCapabilityInventory.build(
            native_tool_names=("external-action",),
            task_text="Perform the external action.",
        )

    async def spawn(
        self,
        _prompt: str,
        *,
        log_dir: Path,
        _broker_binding: Any,
        **_kwargs: Any,
    ):  # noqa: ANN202 - async-generator protocol is inferred
        log_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - tiny test fixture
        (log_dir / "stream.jsonl").write_text(
            '{"type":"result","subtype":"success"}\n',
            encoding="utf-8",
        )
        call = asyncio.create_task(_broker_binding.execute("external-action", {}))
        if self.leave_pending:
            self.pending_tasks.append(call)
            await asyncio.sleep(0)
        else:
            await call
        yield _FakeWorkerEvent()


@pytest_asyncio.fixture
async def manager(tmp_path: Path):
    active = MissionManager(tmp_path / "missions.db")
    await active.start()
    try:
        yield active
    finally:
        await active.stop()


@pytest.fixture(autouse=True)
def _clean_runtime_refs() -> Any:
    runtime_refs._reset_for_tests()
    yield
    runtime_refs._reset_for_tests()


@pytest.mark.asyncio
async def test_observed_tool_error_reaches_critic_with_refusal_note(
    manager: MissionManager,
    tmp_path: Path,
) -> None:
    """A tool ERROR the worker observed in-stream no longer kills the
    iteration — the critic reviews the work WITH the refusal as context."""
    gateway = _Gateway(
        ToolResult(success=False, output=None, error="connector unavailable")
    )
    runtime_refs.set_supervisor_tool_gateway(gateway)
    worker = _BrokerCallingWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    kontrollierer = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda _step: worker,
    )
    mission_id = await manager.dispatch(prompt="Perform an external action")

    state = await kontrollierer.run_mission(mission_id)

    assert state == MissionState.APPROVED
    assert gateway.calls == 1
    assert len(critic.calls) == 1
    critic_log = critic.calls[0]["worker_log"]
    assert "supervisor-tool refusals" in critic_log
    assert "external-action" in critic_log
    assert "connector unavailable" in critic_log


@pytest.mark.asyncio
async def test_denied_tool_call_reaches_critic_and_is_named(
    manager: MissionManager,
    tmp_path: Path,
) -> None:
    """An approval denial (e.g. the unattended 60 s timeout) is an observed
    refusal: the mission is graded, not auto-failed as supervisor_tool_failed."""
    gateway = _Gateway(
        ToolResult(success=False, output=None, error="approval-denied (timeout)")
    )
    runtime_refs.set_supervisor_tool_gateway(gateway)
    worker = _BrokerCallingWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    kontrollierer = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda _step: worker,
    )
    mission_id = await manager.dispatch(prompt="Perform an external action")

    state = await kontrollierer.run_mission(mission_id)

    assert state == MissionState.APPROVED
    assert len(critic.calls) == 1
    critic_log = critic.calls[0]["worker_log"]
    assert "denied" in critic_log
    events = await manager.store.events_for_mission(mission_id)
    failed = [e for e in events if e.payload.event_type == "MissionFailed"]
    assert failed == []


def test_summary_partitions_refusals_from_integrity_failures() -> None:
    """Every non-success status belongs to exactly one bucket: observed
    refusal (keeps the iteration reviewable) or integrity failure (gates)."""
    from jarvis.missions.workers.worker_tool_broker import (
        WorkerToolCallOutcome,
        WorkerToolExecutionSummary,
    )

    def call(status: str) -> WorkerToolCallOutcome:
        return WorkerToolCallOutcome(trace_id="t", tool_name="x", status=status)  # type: ignore[arg-type]

    refusals = WorkerToolExecutionSummary(
        calls=(call("denied"), call("error"), call("timed_out"), call("cancelled"))
    )
    assert refusals.clean is False
    assert refusals.integrity_compromised is False
    assert "denied" in (refusals.refusal_summary or "")

    for status in ("outcome_unknown", "active"):
        summary = WorkerToolExecutionSummary(calls=(call(status),))
        assert summary.integrity_compromised is True
        assert summary.refusal_summary is None

    clean = WorkerToolExecutionSummary(calls=(call("success"),))
    assert clean.clean is True
    assert clean.integrity_compromised is False
    assert clean.refusal_summary is None


@pytest.mark.asyncio
async def test_pending_tool_call_is_cancelled_and_blocks_critic(
    manager: MissionManager,
    tmp_path: Path,
) -> None:
    gateway = _BlockingGateway()
    runtime_refs.set_supervisor_tool_gateway(gateway)
    worker = _BrokerCallingWorker(leave_pending=True)
    critic = FakeCriticRunner(_make_approve_verdict())
    kontrollierer = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda _step: worker,
    )
    mission_id = await manager.dispatch(prompt="Perform an external action")

    state = await kontrollierer.run_mission(mission_id)
    await asyncio.gather(*worker.pending_tasks, return_exceptions=True)

    assert state == MissionState.FAILED
    assert critic.calls == []
    assert gateway.started.is_set()


@pytest.mark.asyncio
async def test_clean_tool_certificate_allows_normal_critic_approval(
    manager: MissionManager,
    tmp_path: Path,
) -> None:
    gateway = _Gateway(ToolResult(success=True, output={"done": True}))
    runtime_refs.set_supervisor_tool_gateway(gateway)
    worker = _BrokerCallingWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    kontrollierer = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda _step: worker,
    )
    mission_id = await manager.dispatch(prompt="Perform an external action")

    state = await kontrollierer.run_mission(mission_id)

    assert state == MissionState.APPROVED
    assert len(critic.calls) == 1
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_cancelled_state_wins_without_a_late_approved_event(
    manager: MissionManager,
    tmp_path: Path,
) -> None:
    critic = FakeCriticRunner(_make_approve_verdict())
    kontrollierer = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
    )
    mission_id = await manager.dispatch(prompt="Cancel before approval")
    await manager.transition_state(
        mission_id,
        MissionState.CANCELLED,
        reason="user_cancelled",
        source_actor="system",
    )
    plan = MissionPlan(
        steps=[Step(slug="task", prompt="do task")],
        n_workers=1,
        expected_output="artifact",
    )

    await kontrollierer._approve_mission(mission_id, plan)

    view = await manager.mission(mission_id)
    assert view is not None and view.state == MissionState.CANCELLED
    events = await manager.store.events_for_mission(mission_id)
    assert all(event.payload.event_type != "MissionApproved" for event in events)
