"""Tests fuer BudgetTracker (Token-Bucket-Cost-Guard)."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.missions.budget import (
    DEFAULT_DAILY_USD,
    DEFAULT_PER_MISSION_USD,
    DEFAULT_WARN_PCT,
    BudgetExceeded,
    BudgetTracker,
)
from jarvis.missions.event_bus import MissionBus
from jarvis.missions.events import (
    EventEnvelope,
    MissionBudgetWarning,
    WorkerDraftReady,
    now_ms,
)


# --- Helper ---


async def _capture_emitter() -> tuple[list[EventEnvelope], "asyncio.Future"]:
    """Erstellt einen Emitter der Envelopes in eine Liste sammelt."""
    captured: list[EventEnvelope] = []

    async def emitter(env: EventEnvelope) -> int:
        captured.append(env)
        return env.seq if env.seq is not None else len(captured)

    fut: asyncio.Future = asyncio.Future()  # placeholder
    return captured, fut


# --- Defaults sane ---


def test_defaults_match_jarvis_toml() -> None:
    assert DEFAULT_PER_MISSION_USD == 5.0
    assert DEFAULT_DAILY_USD == 50.0
    assert DEFAULT_WARN_PCT == (50, 80)


def test_invalid_per_mission_rejected() -> None:
    with pytest.raises(ValueError):
        BudgetTracker(per_mission_usd=0)
    with pytest.raises(ValueError):
        BudgetTracker(per_mission_usd=-1)


def test_invalid_daily_rejected() -> None:
    with pytest.raises(ValueError):
        BudgetTracker(daily_usd=0)


def test_invalid_warn_pct_rejected() -> None:
    with pytest.raises(ValueError):
        BudgetTracker(warn_pct=(0, 50))
    with pytest.raises(ValueError):
        BudgetTracker(warn_pct=(50, 100))


# --- record() Akkumulation ---


@pytest.mark.asyncio
async def test_record_accumulates_per_mission() -> None:
    bt = BudgetTracker()
    await bt.record("m1", 1.0)
    await bt.record("m1", 0.5)
    assert bt.mission_cost("m1") == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_record_isolates_missions() -> None:
    bt = BudgetTracker()
    await bt.record("m1", 1.0)
    await bt.record("m2", 2.0)
    assert bt.mission_cost("m1") == pytest.approx(1.0)
    assert bt.mission_cost("m2") == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_record_zero_cost_is_noop() -> None:
    bt = BudgetTracker()
    await bt.record("m1", 0)
    await bt.record("m1", -1.0)
    assert bt.mission_cost("m1") == 0.0


@pytest.mark.asyncio
async def test_record_increments_daily_total() -> None:
    bt = BudgetTracker()
    await bt.record("m1", 1.0)
    await bt.record("m2", 2.0)
    assert bt.daily_total() == pytest.approx(3.0)


# --- Warn-Emission ---


@pytest.mark.asyncio
async def test_record_emits_warn_at_50_pct() -> None:
    captured, _ = await _capture_emitter()

    async def emitter(env: EventEnvelope) -> int:
        captured.append(env)
        return 1

    bt = BudgetTracker(per_mission_usd=10.0, emitter=emitter)
    await bt.record("m1", 5.0)  # 50%
    assert any(isinstance(e.payload, MissionBudgetWarning) for e in captured)
    warn = [e for e in captured if isinstance(e.payload, MissionBudgetWarning)][0]
    assert warn.payload.pct_used == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_record_emits_warn_at_80_pct_after_50() -> None:
    captured: list[EventEnvelope] = []

    async def emitter(env: EventEnvelope) -> int:
        captured.append(env)
        return 1

    bt = BudgetTracker(per_mission_usd=10.0, emitter=emitter)
    await bt.record("m1", 5.0)  # 50% warn
    await bt.record("m1", 3.0)  # cum 80% -> warn
    warns = [e for e in captured if isinstance(e.payload, MissionBudgetWarning)]
    assert len(warns) == 2
    assert warns[1].payload.pct_used == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_record_warn_only_once_per_threshold() -> None:
    captured: list[EventEnvelope] = []

    async def emitter(env: EventEnvelope) -> int:
        captured.append(env)
        return 1

    bt = BudgetTracker(per_mission_usd=10.0, emitter=emitter)
    await bt.record("m1", 5.0)  # 50%
    await bt.record("m1", 0.1)  # noch immer 50%-Schwelle, kein neuer Warn
    await bt.record("m1", 0.1)
    warns = [e for e in captured if isinstance(e.payload, MissionBudgetWarning)]
    assert len(warns) == 1


@pytest.mark.asyncio
async def test_record_no_emitter_no_warn_no_crash() -> None:
    bt = BudgetTracker(per_mission_usd=10.0, emitter=None)
    # Sollte einfach durchlaufen ohne zu raisen
    await bt.record("m1", 5.0)


# --- Hard-Cap (BudgetExceeded) ---


@pytest.mark.asyncio
async def test_record_raises_at_per_mission_limit() -> None:
    bt = BudgetTracker(per_mission_usd=5.0)
    await bt.record("m1", 4.5)
    with pytest.raises(BudgetExceeded, match="Mission m1"):
        await bt.record("m1", 0.6)
    # Cost wurde trotzdem persistiert (Forensik)
    assert bt.mission_cost("m1") == pytest.approx(5.1)


@pytest.mark.asyncio
async def test_record_raises_at_daily_limit() -> None:
    bt = BudgetTracker(per_mission_usd=100.0, daily_usd=10.0)
    await bt.record("m1", 5.0)
    with pytest.raises(BudgetExceeded, match="Daily Budget"):
        await bt.record("m2", 6.0)


@pytest.mark.asyncio
async def test_assert_under_limit_pre_check() -> None:
    bt = BudgetTracker(per_mission_usd=5.0)
    bt.assert_under_limit("m1")  # frische Mission, kein Crash
    await bt.record("m1", 4.5)
    bt.assert_under_limit("m1")  # noch unter Limit, kein Crash


@pytest.mark.asyncio
async def test_assert_under_limit_blocks_after_exceeded() -> None:
    bt = BudgetTracker(per_mission_usd=5.0)
    with pytest.raises(BudgetExceeded):
        await bt.record("m1", 5.0)
    with pytest.raises(BudgetExceeded, match="Pre-Spawn-Check"):
        bt.assert_under_limit("m1")


# --- enabled=False: budget fully disabled (user mandate 2026-05-31) ---


@pytest.mark.asyncio
async def test_disabled_budget_never_raises_per_mission() -> None:
    """enabled=False turns the tracker into a no-op: record() far past the
    nominal cap must NOT raise BudgetExceeded — a mission is never aborted for
    cost (frontier-quality-over-cost mandate)."""
    bt = BudgetTracker(per_mission_usd=5.0, enabled=False)
    await bt.record("m1", 1000.0)  # 200x the nominal cap — must not raise


@pytest.mark.asyncio
async def test_disabled_budget_never_raises_daily() -> None:
    bt = BudgetTracker(per_mission_usd=100.0, daily_usd=10.0, enabled=False)
    await bt.record("m1", 5.0)
    await bt.record("m2", 9999.0)  # blows the daily cap — must not raise


@pytest.mark.asyncio
async def test_disabled_budget_assert_under_limit_never_blocks() -> None:
    bt = BudgetTracker(per_mission_usd=5.0, enabled=False)
    await bt.record("m1", 1000.0)
    bt.assert_under_limit("m1")  # must not raise despite being far over nominal


@pytest.mark.asyncio
async def test_disabled_budget_emits_no_warnings() -> None:
    captured: list[EventEnvelope] = []

    async def emitter(env: EventEnvelope) -> int:
        captured.append(env)
        return 1

    bt = BudgetTracker(per_mission_usd=10.0, emitter=emitter, enabled=False)
    await bt.record("m1", 9.9)  # would be 99% if enabled
    assert captured == [], "disabled budget must emit no warnings"


def test_disabled_budget_skips_cap_validation() -> None:
    """When disabled, the positive-cap validation is skipped — the caps are
    inert, so a 0 cap must not raise at construction (the enabled-default path
    still rejects 0, see test_invalid_per_mission_rejected)."""
    bt = BudgetTracker(per_mission_usd=0, daily_usd=0, enabled=False)
    assert bt is not None


# --- bind_to_event_bus ---


@pytest.mark.asyncio
async def test_bind_to_event_bus_tracks_worker_draft_ready() -> None:
    bus = MissionBus()
    bt = BudgetTracker()
    bt.bind_to_event_bus(bus)

    env = EventEnvelope(
        mission_id="m1",
        source_actor="worker",
        ts_ms=now_ms(),
        payload=WorkerDraftReady(
            worker_id="w1",
            artifact_uri="file:///tmp/x.diff",
            diff="d",
            tokens_used=100,
            cost_usd=1.5,
            session_id="s1",
        ),
    )
    await bus.publish(env)
    # Bus drained subscribers im Background — kurz warten.
    await asyncio.sleep(0.05)
    assert bt.mission_cost("m1") == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_bind_to_event_bus_ignores_non_draft_events() -> None:
    bus = MissionBus()
    bt = BudgetTracker()
    bt.bind_to_event_bus(bus)

    from jarvis.missions.events import MissionDispatched

    env = EventEnvelope(
        mission_id="m1",
        source_actor="hauptjarvis",
        ts_ms=now_ms(),
        payload=MissionDispatched(prompt="x"),
    )
    await bus.publish(env)
    await asyncio.sleep(0.05)
    assert bt.mission_cost("m1") == 0.0


# --- Concurrency-Safety ---


@pytest.mark.asyncio
async def test_record_concurrent_workers_serialize_via_lock() -> None:
    """Parallele record() von 5 Workern duerfen keine Race-Condition haben."""
    bt = BudgetTracker(per_mission_usd=100.0)

    async def worker(amount: float) -> None:
        await bt.record("m1", amount)

    await asyncio.gather(*(worker(0.5) for _ in range(20)))
    # 20 * 0.5 = 10.0 — exakt
    assert bt.mission_cost("m1") == pytest.approx(10.0)
