"""MissionEventBridge — terminal MissionBus events become global MissionCompleted.

Guards the When-Then seam: a finished mission must surface as exactly one flat
``MissionCompleted`` on the global bus (so the Tasks scheduler can match it), and
non-terminal events must NOT leak through.
"""
from __future__ import annotations

from jarvis.core.bus import EventBus
from jarvis.core.events import MissionCompleted
from jarvis.missions.event_bus import MissionBus
from jarvis.missions.events import (
    EventEnvelope,
    MissionApproved,
    MissionCancelled,
    MissionFailed,
    MissionTimedOut,
    WorkerProgress,
    now_ms,
)
from jarvis.missions.task_bridge import MissionEventBridge


def _env(payload, *, mission_id: str = "m1", source_actor: str = "kontrollierer"):
    return EventEnvelope(
        mission_id=mission_id,
        source_actor=source_actor,  # type: ignore[arg-type]
        ts_ms=now_ms(),
        payload=payload,
    )


async def _bridge_with_collector() -> tuple[MissionBus, list[MissionCompleted]]:
    mbus = MissionBus()
    gbus = EventBus()
    got: list[MissionCompleted] = []

    async def collect(e: MissionCompleted) -> None:
        got.append(e)

    gbus.subscribe(MissionCompleted, collect)
    bridge = MissionEventBridge(bus=mbus, global_bus=gbus)
    await bridge.start()
    return mbus, got


async def test_approved_becomes_mission_completed() -> None:
    mbus, got = await _bridge_with_collector()
    await mbus.publish(
        _env(
            MissionApproved(
                result_uri="/out/result.md",
                tokens_used=100,
                cost_usd=0.01,
                wall_ms=5000,
                summary_de="Fertig.",  # i18n-allow: German summary_de voice-readback field under test
                summary_en="Done.",
            ),
            mission_id="abc",
        )
    )
    assert len(got) == 1
    sig = got[0]
    assert sig.mission_id == "abc"
    assert sig.status == "approved"
    assert sig.result_uri == "/out/result.md"
    assert sig.summary_de == "Fertig."  # i18n-allow: asserts the German summary_de voice-readback field
    assert sig.summary_en == "Done."


async def test_failed_carries_reason() -> None:
    mbus, got = await _bridge_with_collector()
    await mbus.publish(
        _env(MissionFailed(reason="timeout: worker", last_state="running"))
    )
    assert len(got) == 1
    assert got[0].status == "failed"
    assert got[0].reason == "timeout: worker"


async def test_cancelled_and_timed_out_map() -> None:
    mbus, got = await _bridge_with_collector()
    await mbus.publish(_env(MissionCancelled(cascade=False, reason="user")))
    await mbus.publish(_env(MissionTimedOut(deadline_ms=1000, last_progress_ms=500)))
    statuses = [g.status for g in got]
    assert statuses == ["cancelled", "timed_out"]


async def test_non_terminal_event_does_not_bridge() -> None:
    mbus, got = await _bridge_with_collector()
    await mbus.publish(_env(WorkerProgress(worker_id="w1", note="step 2")))
    assert got == []


async def test_completed_signal_matches_status_filter() -> None:
    """The flat ``status`` field must satisfy the scheduler's filter_expr AST."""
    from jarvis.tasks.scheduler import _match_filter

    sig = MissionCompleted(mission_id="m1", status="approved")
    assert _match_filter(sig, "status == 'approved'") is True
    assert _match_filter(sig, "status == 'failed'") is False
