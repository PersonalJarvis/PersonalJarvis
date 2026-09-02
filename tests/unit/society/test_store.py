"""society.db: append → seq → publish, replay, inbox, trace, cost and stats."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.society.events import MsgType, SocietyEnvelope
from jarvis.society.store import SocietyStore


@pytest.fixture
async def store(tmp_path: Path):
    s = SocietyStore(tmp_path / "society.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _say(frm: str, to: str | None, trace: str, text: str = "hi", cost: float = 0.0):
    return SocietyEnvelope(
        msg_type=MsgType.SAY,
        from_agent=frm,
        to_agent=to,
        trace_id=trace,
        cost_usd=cost,
        payload={"text": text},
    )


async def test_append_assigns_seq_and_publishes_after_persist(store: SocietyStore):
    seen: list[SocietyEnvelope] = []

    async def _observer(env: SocietyEnvelope) -> None:
        # The row is already on disk when the observer runs.
        assert await store.last_seq() == env.seq
        seen.append(env)

    store.bus.subscribe_all(_observer)
    stored = await store.append_and_publish(_say("scout", "archivist", "t1"))
    assert stored.seq == 1
    assert [e.seq for e in seen] == [1]


async def test_seq_must_be_server_assigned(store: SocietyStore):
    with pytest.raises(ValueError):
        await store.append_and_publish(_say("a", "b", "t").model_copy(update={"seq": 7}))


async def test_replay_and_reads(store: SocietyStore):
    await store.append_and_publish(_say("scout", "archivist", "t1", "one"))
    await store.append_and_publish(_say("archivist", "scout", "t1", "two"))
    await store.append_and_publish(_say("quill", None, "t2", "all"))

    assert [e.text for e in await store.events_since(0)] == ["one", "two", "all"]
    assert [e.text for e in await store.events_since(1)] == ["two", "all"]
    assert [e.text for e in await store.events_for_trace("t1")] == ["one", "two"]
    assert [e.text for e in await store.inbox_for("scout")] == ["two"]
    # The agent view includes what it sent, what it got, and broadcasts.
    assert [e.text for e in await store.events_for_agent("scout")] == ["one", "two", "all"]
    assert await store.count_in_trace("t1") == 2


async def test_payload_roundtrips_unicode(store: SocietyStore):
    env = _say("scout", "quill", "t", "Grüße — ünïcode ✓")  # i18n-allow: unicode sample
    await store.append_and_publish(env)
    back = (await store.events_since(0))[0]
    assert back.text == "Grüße — ünïcode ✓"  # i18n-allow: unicode sample
    assert back.event_id == env.event_id


async def test_cost_and_stats_are_derived(store: SocietyStore):
    await store.append_and_publish(_say("scout", "quill", "t", cost=0.25))
    await store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent="scout",
            to_agent="jarvis",
            trace_id="t",
            cost_usd=0.5,
            payload={"done": "x", "output": ["file:a"]},
        )
    )
    stats = await store.agent_stats("scout")
    assert stats["runs"] == 1
    assert stats["total_cost_usd"] == pytest.approx(0.75)
    assert stats["last_active_ms"] is not None
    assert await store.cost_since("scout", 0) == pytest.approx(0.75)


async def test_kill_switch_persists(store: SocietyStore, tmp_path: Path):
    assert await store.kill_switch() is False
    await store.set_kill_switch(True)
    await store.close()
    again = SocietyStore(tmp_path / "society.db")
    await again.open()
    try:
        assert await again.kill_switch() is True
    finally:
        await again.close()


async def test_survives_a_crash_between_persist_and_publish(store: SocietyStore, tmp_path: Path):
    async def _boom(env: SocietyEnvelope) -> None:
        raise RuntimeError("observer died")

    store.bus.subscribe_all(_boom)
    stored = await store.append_and_publish(_say("a", "b", "t"))
    assert stored.seq == 1
    # A failing observer never loses the row.
    assert len(await store.events_since(0)) == 1


async def test_open_is_idempotent(tmp_path: Path):
    s = SocietyStore(tmp_path / "society.db")
    await s.open()
    await s.open()
    await s.close()
    await s.close()


async def test_checkpoint_check_is_widened_on_an_old_database(tmp_path):
    """A society.db created before the hub shops carries the five-value CHECK;
    opening it rebuilds the table and keeps every row."""
    import sqlite3

    from jarvis.society.roster import Roster
    from jarvis.society.store import _SCHEMA_PATH, SocietyStore

    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    start = schema.index("CHECK (checkpoint IN (")
    end = schema.index("))", start) + 2
    old_schema = (
        schema[:start]
        + "CHECK (checkpoint IN ('desk', 'meeting', 'archive', 'gate', 'idle'))"
        + schema[end:]
    )
    db = tmp_path / "society.db"
    conn = sqlite3.connect(db)
    conn.executescript(old_schema)
    conn.execute(
        "INSERT INTO society_agents (agent_id, name, tier, created_ms, updated_ms) "
        "VALUES ('scout', 'Scout', 'specialist', 1, 1)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE society_agents SET checkpoint = 'hub:plugins'")
    conn.close()

    store = SocietyStore(db)
    await store.open()
    try:
        roster = Roster(store)
        scout = await roster.get("scout")
        assert scout is not None and scout.name == "Scout"
        await roster.update("scout", {"checkpoint": "hub:plugins"})
        assert str((await roster.get("scout")).checkpoint) == "hub:plugins"
        # Idempotent: a second open leaves the rebuilt table alone.
        await store.close()
        await store.open()
        assert str((await Roster(store).get("scout")).checkpoint) == "hub:plugins"
    finally:
        await store.close()
