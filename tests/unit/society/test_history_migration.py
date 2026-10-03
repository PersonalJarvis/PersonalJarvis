from __future__ import annotations

from types import SimpleNamespace

from jarvis.society.events import MsgType, SocietyEnvelope
from jarvis.society.history_migration import migrate_legacy_missions
from jarvis.society.store import SocietyStore


class _MissionStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def list_missions(self, *, limit: int = 100):
        self.calls += 1
        return self.rows[:limit]


async def test_legacy_missions_enter_ledger_once_without_live_publish(tmp_path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    published = []

    async def capture(event):
        published.append(event)

    unsubscribe = store.bus.subscribe_all(capture)
    source = _MissionStore(
        [
            {
                "id": "newer",
                "prompt": "Compare suppliers.",
                "state": "APPROVED",
                "created_ms": 200,
                "updated_ms": 250,
                "cost_usd": 0.42,
            },
            {
                "id": "older",
                "prompt": "Inspect the old deployment.",
                "state": "FAILED",
                "created_ms": 100,
                "updated_ms": 150,
                "cost_usd": 0.11,
            },
        ]
    )
    manager = SimpleNamespace(store=source)
    try:
        assert await migrate_legacy_missions(store, manager) == 2
        events = await store.events_since(0)
        assert [event.trace_id for event in events] == [
            "mission:older",
            "mission:newer",
        ]
        assert all(event.msg_type is MsgType.DIGEST for event in events)
        assert all(event.cost_usd == 0 for event in events)
        assert events[0].payload["state"] == "FAILED"
        assert events[1].payload["historic_cost_usd"] == 0.42
        assert "Compare suppliers." in events[1].text
        assert published == []

        assert await migrate_legacy_missions(store, manager) == 0
        assert source.calls == 1
        assert len(await store.events_since(0)) == 2
    finally:
        unsubscribe()
        await store.close()


async def test_legacy_migration_does_not_duplicate_a_mirrored_trace(tmp_path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    existing = SocietyEnvelope(
        msg_type=MsgType.CLAIM,
        from_agent="jarvis",
        trace_id="mission:already",
        payload={"run_id": "already", "text": "already mirrored"},
    )
    await store.append_and_publish(existing)
    source = _MissionStore(
        [
            {
                "id": "already",
                "prompt": "Old task.",
                "state": "APPROVED",
                "created_ms": 100,
                "updated_ms": 200,
                "cost_usd": 1.0,
            }
        ]
    )
    try:
        assert await migrate_legacy_missions(store, SimpleNamespace(store=source)) == 0
        events = await store.events_for_trace("mission:already")
        assert len(events) == 1
        assert events[0].msg_type is MsgType.CLAIM
    finally:
        await store.close()


async def test_history_import_event_is_idempotent(tmp_path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    event = SocietyEnvelope(
        event_id="legacy-fixed",
        msg_type=MsgType.DIGEST,
        from_agent="jarvis",
        trace_id="mission:fixed",
        payload={"kind": "legacy_mission"},
    )
    try:
        first = await store.import_event(event)
        second = await store.import_event(event)
        assert first.seq == second.seq
        assert len(await store.events_since(0)) == 1
    finally:
        await store.close()
