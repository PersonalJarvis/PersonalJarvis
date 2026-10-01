from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.missions.supervisor import MissionSupervisor
from jarvis.missions.state_machine import MissionState


class _Store:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.last_limit: int | None = None

    async def list_missions(self, *, limit: int = 100) -> list[dict[str, object]]:
        self.last_limit = limit
        return self.rows[:limit]


def _row(
    mission_id: str,
    state: MissionState,
    *,
    updated_ms: int,
    cost: float = 0.0,
    language: str = "en",
) -> dict[str, object]:
    return {
        "id": mission_id,
        "prompt": f"prompt-{mission_id}",
        "state": state.value,
        "language": language,
        "created_ms": 1,
        "updated_ms": updated_ms,
        "iteration": 0,
        "cost_usd": cost,
    }


@pytest.mark.asyncio
async def test_snapshot_groups_active_stale_and_cost() -> None:
    store = _Store(
        [
            _row("running-stale", MissionState.RUNNING, updated_ms=100, cost=1.25),
            _row("approved", MissionState.APPROVED, updated_ms=900, cost=2.0),
            _row("pending-fresh", MissionState.PENDING, updated_ms=950, cost=0.25),
        ]
    )
    manager = SimpleNamespace(store=store)
    supervisor = MissionSupervisor(manager, stale_after_ms=500)

    snapshot = await supervisor.snapshot(at_ms=1000)

    assert [m.mission_id for m in snapshot.active] == [
        "running-stale",
        "pending-fresh",
    ]
    assert [m.mission_id for m in snapshot.stale] == ["running-stale"]
    assert snapshot.focus is not None
    assert snapshot.focus.mission_id == "running-stale"
    assert snapshot.counts[MissionState.RUNNING.value] == 1
    assert snapshot.counts[MissionState.APPROVED.value] == 1
    assert snapshot.total_cost_usd == pytest.approx(3.5)


@pytest.mark.asyncio
async def test_focus_prefers_stale_then_attention_state_then_age() -> None:
    store = _Store(
        [
            _row("pending-old", MissionState.PENDING, updated_ms=100),
            _row("running-new", MissionState.RUNNING, updated_ms=800),
            _row("critic", MissionState.CRITIQUING, updated_ms=700),
            _row("looping", MissionState.LOOPING, updated_ms=600),
        ]
    )
    manager = SimpleNamespace(store=store)
    supervisor = MissionSupervisor(manager, stale_after_ms=10_000)

    snapshot = await supervisor.snapshot(at_ms=1000)

    assert snapshot.focus is not None
    assert snapshot.focus.mission_id == "critic"


@pytest.mark.asyncio
async def test_terminal_missions_are_never_marked_stale() -> None:
    store = _Store([_row("failed", MissionState.FAILED, updated_ms=1)])
    manager = SimpleNamespace(store=store)
    supervisor = MissionSupervisor(manager, stale_after_ms=10)

    snapshot = await supervisor.snapshot(at_ms=10_000)

    assert snapshot.active == ()
    assert snapshot.stale == ()
    assert snapshot.focus is None
    assert snapshot.missions[0].terminal is True
    assert snapshot.missions[0].stale is False


@pytest.mark.asyncio
async def test_snapshot_forwards_limit() -> None:
    store = _Store([_row("one", MissionState.RUNNING, updated_ms=1)])
    manager = SimpleNamespace(store=store)
    supervisor = MissionSupervisor(manager)

    await supervisor.snapshot(limit=7, at_ms=2)

    assert store.last_limit == 7


def test_invalid_limits_and_stale_window_fail_closed() -> None:
    manager = SimpleNamespace(store=_Store([]))

    with pytest.raises(ValueError):
        MissionSupervisor(manager, stale_after_ms=0)
