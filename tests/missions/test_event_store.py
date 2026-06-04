"""Tests fuer SQLite-WAL Event-Store + persist-vor-publish-Atomicitaet."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from jarvis.missions.event_bus import MissionBus
from jarvis.missions.event_store import MissionEventStore
from jarvis.missions.events import (
    CriticVerdictReady,
    EventEnvelope,
    MissionApproved,
    MissionDispatched,
    MissionStateChanged,
    now_ms,
)
from jarvis.missions.ids import uuid7_str


# --- Fixtures ---


@pytest_asyncio.fixture
async def open_store(tmp_missions_db: Path):
    """Geoeffneter MissionEventStore + Bus-Pair, automatisches close()."""
    bus = MissionBus()
    store = MissionEventStore(tmp_missions_db, bus)
    await store.open()
    try:
        yield store, bus
    finally:
        await store.close()


def _envelope(
    *,
    mission_id: str | None = None,
    prompt: str = "test",
    actor: str = "hauptjarvis",
) -> EventEnvelope:
    return EventEnvelope(
        mission_id=mission_id or uuid7_str(),
        source_actor=actor,  # type: ignore[arg-type]
        ts_ms=now_ms(),
        payload=MissionDispatched(prompt=prompt),
    )


# --- Open / Schema ---


async def test_open_creates_db_file(tmp_missions_db: Path) -> None:
    bus = MissionBus()
    store = MissionEventStore(tmp_missions_db, bus)
    await store.open()
    try:
        assert tmp_missions_db.exists()
    finally:
        await store.close()


async def test_open_is_idempotent(open_store) -> None:
    store, _bus = open_store
    await store.open()  # zweiter Aufruf darf nicht crashen
    assert store.conn is not None


# --- Append + seq monoton ---


async def test_append_and_publish_assigns_seq(open_store) -> None:
    store, _bus = open_store
    seq = await store.append_and_publish(_envelope())
    assert seq == 1


async def test_seq_monotonically_increasing(open_store) -> None:
    store, _bus = open_store
    mid = uuid7_str()
    seqs = [
        await store.append_and_publish(_envelope(mission_id=mid, prompt=str(i)))
        for i in range(5)
    ]
    assert seqs == [1, 2, 3, 4, 5]


async def test_append_rejects_envelope_with_seq_already_set(open_store) -> None:
    store, _bus = open_store
    env = _envelope().model_copy(update={"seq": 99})
    with pytest.raises(ValueError, match="seq muss None sein"):
        await store.append_and_publish(env)


# --- Read-API ---


async def test_events_since_returns_in_order(open_store) -> None:
    store, _bus = open_store
    mid = uuid7_str()
    for i in range(3):
        await store.append_and_publish(_envelope(mission_id=mid, prompt=f"p{i}"))
    events = await store.events_since(0)
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.payload.prompt for e in events] == ["p0", "p1", "p2"]  # type: ignore[union-attr]


async def test_events_since_with_offset(open_store) -> None:
    store, _bus = open_store
    for i in range(5):
        await store.append_and_publish(_envelope(prompt=str(i)))
    events = await store.events_since(2)
    assert [e.seq for e in events] == [3, 4, 5]


async def test_events_for_mission_filter(open_store) -> None:
    store, _bus = open_store
    mid_a, mid_b = uuid7_str(), uuid7_str()
    await store.append_and_publish(_envelope(mission_id=mid_a, prompt="a1"))
    await store.append_and_publish(_envelope(mission_id=mid_b, prompt="b1"))
    await store.append_and_publish(_envelope(mission_id=mid_a, prompt="a2"))

    a_events = await store.events_for_mission(mid_a)
    assert len(a_events) == 2
    assert [e.payload.prompt for e in a_events] == ["a1", "a2"]  # type: ignore[union-attr]


# --- Roundtrip mit verschiedenen Payload-Typen (Discriminated Union) ---


async def test_payload_roundtrip_preserves_critic_verdict(open_store) -> None:
    store, _bus = open_store
    mid = uuid7_str()
    env = EventEnvelope(
        mission_id=mid,
        source_actor="critic",
        ts_ms=now_ms(),
        payload=CriticVerdictReady(
            worker_id="w1",
            verdict="revise",
            summary="Edge-case missing",
            confidence=0.7,
            axes={"correctness": {"status": "fail", "evidence": ["test:foo:42"]}},
            iteration=2,
        ),
    )
    await store.append_and_publish(env)
    [reloaded] = await store.events_for_mission(mid)
    assert reloaded.payload.event_type == "CriticVerdictReady"
    assert isinstance(reloaded.payload, CriticVerdictReady)
    assert reloaded.payload.verdict == "revise"
    assert reloaded.payload.iteration == 2


async def test_payload_roundtrip_preserves_state_change(open_store) -> None:
    store, _bus = open_store
    mid = uuid7_str()
    env = EventEnvelope(
        mission_id=mid,
        source_actor="system",
        ts_ms=now_ms(),
        payload=MissionStateChanged(
            from_state="PENDING", to_state="RUNNING", reason="dispatch"
        ),
    )
    await store.append_and_publish(env)
    [reloaded] = await store.events_for_mission(mid)
    assert isinstance(reloaded.payload, MissionStateChanged)
    assert reloaded.payload.from_state == "PENDING"
    assert reloaded.payload.to_state == "RUNNING"


# --- Mission-Header ---


async def test_upsert_and_list_non_terminal(open_store) -> None:
    store, _bus = open_store
    mid = uuid7_str()
    await store.upsert_mission(
        mission_id=mid, prompt="task", state="RUNNING", language="de", ts_ms=now_ms()
    )
    rows = await store.list_non_terminal_missions()
    assert (mid, "task", "RUNNING") in rows


async def test_upsert_updates_state(open_store) -> None:
    store, _bus = open_store
    mid = uuid7_str()
    await store.upsert_mission(
        mission_id=mid, prompt="t", state="RUNNING", language="de", ts_ms=now_ms()
    )
    await store.upsert_mission(
        mission_id=mid, prompt="t", state="APPROVED", language="de", ts_ms=now_ms()
    )
    state = await store.get_mission_state(mid)
    assert state == "APPROVED"
    rows = await store.list_non_terminal_missions()
    assert mid not in [r[0] for r in rows]


# --- Persist-vor-Publish-Atomicitaet (Risk #9/#10) ---


async def test_persist_survives_when_bus_publish_raises(open_store) -> None:
    """Bus-Crash zwischen Insert und Publish: Insert bleibt persisted."""
    store, bus = open_store
    env = _envelope(prompt="will-survive")

    with patch.object(
        bus, "publish", side_effect=RuntimeError("simulated bus crash")
    ):
        with pytest.raises(RuntimeError, match="bus crash"):
            await store.append_and_publish(env)

    # Insert ist trotzdem persisted
    events = await store.events_since(0)
    assert len(events) == 1
    assert events[0].payload.prompt == "will-survive"  # type: ignore[union-attr]


async def test_recovery_can_replay_unpublished_event(open_store) -> None:
    """Nach simuliertem Crash: events_since liefert das Event zum Re-Broadcast."""
    store, bus = open_store
    mid = uuid7_str()
    env = _envelope(mission_id=mid, prompt="lost-publish")

    with patch.object(bus, "publish", side_effect=RuntimeError("crash")):
        with pytest.raises(RuntimeError):
            await store.append_and_publish(env)

    received: list[EventEnvelope] = []

    async def collector(e: EventEnvelope) -> None:
        received.append(e)

    bus.subscribe_all(collector)

    # Recovery-Lese: alle Events ab seq 0
    recovered = await store.events_since(0)
    for r in recovered:
        await bus.publish(r)

    assert len(received) == 1
    assert received[0].payload.prompt == "lost-publish"  # type: ignore[union-attr]
    assert received[0].seq == 1


# --- Konsequente Bus-Lieferung im Erfolgsfall ---


async def test_successful_append_delivers_to_subscribers(open_store) -> None:
    store, bus = open_store
    received: list[EventEnvelope] = []

    async def collect(e: EventEnvelope) -> None:
        received.append(e)

    bus.subscribe_all(collect)
    await store.append_and_publish(_envelope(prompt="delivered"))
    assert len(received) == 1
    assert received[0].seq == 1
    assert received[0].payload.prompt == "delivered"  # type: ignore[union-attr]
