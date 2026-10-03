"""Rooms: 2-6 members, <=3 rounds, <=10 messages, silence allowed, deterministic."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.society.events import MsgType, RoomState
from jarvis.society.failure_reasons import FailureReason
from jarvis.society.rooms import MAX_MESSAGES, MAX_ROUNDS, RoomError, Rooms
from jarvis.society.store import SocietyStore


@pytest.fixture
async def rooms(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    try:
        yield Rooms(store), store
    finally:
        await store.close()


async def test_member_bounds(rooms):
    service, _ = rooms
    with pytest.raises(RoomError) as exc:
        await service.open(opened_by="jarvis", members=["a"])
    assert exc.value.reason is FailureReason.BLOCKED_BY_POLICY
    with pytest.raises(RoomError):
        await service.open(opened_by="jarvis", members=list("abcdefg"))
    room = await service.open(opened_by="jarvis", members=["a", "b", "a"], topic="plan")
    assert room.members == ["a", "b"]
    assert room.state is RoomState.RUNNING
    assert room.next_speaker == "a"


async def test_open_writes_room_open_event(rooms):
    service, store = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"], topic="t")
    events = await store.events_for_trace(room.trace_id)
    assert [e.msg_type for e in events] == [MsgType.ROOM_OPEN]
    assert events[0].payload["members"] == ["a", "b"]


async def test_open_generates_distinct_ids_for_same_millisecond(rooms, monkeypatch):
    service, _ = rooms
    monkeypatch.setattr("jarvis.society.rooms.now_ms", lambda: 1_800_000_000_000)

    first = await service.open(opened_by="jarvis", members=["a", "b"])
    second = await service.open(opened_by="jarvis", members=["a", "b"])

    assert first.created_ms == second.created_ms
    assert first.room_id != second.room_id


async def test_turn_order_is_enforced(rooms):
    service, _ = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    with pytest.raises(RoomError):
        await service.say(room.room_id, "b", "me first")
    with pytest.raises(RoomError) as exc:
        await service.say(room.room_id, "zed", "hi")
    assert exc.value.reason is FailureReason.TARGET_UNKNOWN
    room = await service.say(room.room_id, "a", "hello")
    assert room.next_speaker == "b"
    assert room.message_count == 1


async def test_concurrent_replies_cannot_consume_the_same_turn(rooms, monkeypatch):
    service, store = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    original = store.transition_room

    async def blocked_transition(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(store, "transition_room", blocked_transition)
    first = asyncio.create_task(service.say(room.room_id, "a", "first"))
    await entered.wait()
    duplicate = asyncio.create_task(service.say(room.room_id, "a", "duplicate"))
    await asyncio.sleep(0)
    assert calls == 1
    release.set()

    results = await asyncio.gather(first, duplicate, return_exceptions=True)
    assert sum(isinstance(result, RoomError) for result in results) == 1
    loaded = await service.get(room.room_id)
    assert loaded is not None
    assert loaded.next_speaker == "b"
    assert loaded.message_count == 1
    events = await store.events_for_trace(room.trace_id)
    assert sum(event.msg_type is MsgType.SAY for event in events) == 1


async def test_open_rolls_back_if_room_open_event_fails(rooms, monkeypatch):
    service, store = rooms
    original = getattr(store, "_insert_event")

    async def fail_open(conn, event):
        if event.msg_type is MsgType.ROOM_OPEN:
            raise RuntimeError("simulated crash")
        return await original(conn, event)

    monkeypatch.setattr(store, "_insert_event", fail_open)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await service.open(
            opened_by="jarvis",
            members=["a", "b"],
            room_id="atomic-open",
        )

    assert await store.get_room_row("atomic-open") is None
    assert await store.events_for_trace("room:atomic-open") == []


async def test_say_rolls_back_state_if_event_insert_fails(rooms, monkeypatch):
    service, store = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    original = getattr(store, "_insert_event")

    async def fail_say(conn, event):
        if event.msg_type is MsgType.SAY:
            raise RuntimeError("simulated crash")
        return await original(conn, event)

    monkeypatch.setattr(store, "_insert_event", fail_say)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await service.say(room.room_id, "a", "not committed")

    loaded = await service.get(room.room_id)
    assert loaded is not None
    assert loaded.message_count == 0
    assert loaded.next_speaker == "a"
    events = await store.events_for_trace(room.trace_id)
    assert [event.msg_type for event in events] == [MsgType.ROOM_OPEN]


async def test_terminal_say_rolls_back_say_and_settle_together(rooms, monkeypatch):
    service, store = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    for _ in range(MAX_ROUNDS - 1):
        room = await service.say(room.room_id, "a", "x")
        room = await service.say(room.room_id, "b", "y")
    room = await service.say(room.room_id, "a", "last round")
    assert room.next_speaker == "b"
    original = getattr(store, "_insert_event")

    async def fail_settle(conn, event):
        if event.msg_type is MsgType.ROOM_SETTLE:
            raise RuntimeError("simulated crash")
        return await original(conn, event)

    monkeypatch.setattr(store, "_insert_event", fail_settle)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await service.say(room.room_id, "b", "would settle")

    loaded = await service.get(room.room_id)
    assert loaded is not None
    assert loaded.state is RoomState.RUNNING
    assert loaded.message_count == 5
    assert loaded.next_speaker == "b"
    events = await store.events_for_trace(room.trace_id)
    assert sum(event.msg_type is MsgType.SAY for event in events) == 5
    assert all(event.msg_type is not MsgType.ROOM_SETTLE for event in events)


async def test_manual_settle_rolls_back_if_event_insert_fails(rooms, monkeypatch):
    service, store = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    original = getattr(store, "_insert_event")

    async def fail_settle(conn, event):
        if event.msg_type is MsgType.ROOM_SETTLE:
            raise RuntimeError("simulated crash")
        return await original(conn, event)

    monkeypatch.setattr(store, "_insert_event", fail_settle)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await service.settle(room.room_id, reason="done")

    loaded = await service.get(room.room_id)
    assert loaded is not None
    assert loaded.state is RoomState.RUNNING
    assert loaded.settle_reason == ""
    events = await store.events_for_trace(room.trace_id)
    assert [event.msg_type for event in events] == [MsgType.ROOM_OPEN]


async def test_round_cap_settles(rooms):
    service, store = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    for _ in range(MAX_ROUNDS):
        room = await service.say(room.room_id, "a", "x")
        room = await service.say(room.room_id, "b", "y")
    assert room.state is RoomState.SETTLED
    assert room.settle_reason == "round_cap"
    assert room.round == MAX_ROUNDS
    events = await store.events_for_trace(room.trace_id)
    assert events[-1].msg_type is MsgType.ROOM_SETTLE
    with pytest.raises(RoomError):
        await service.say(room.room_id, "a", "more")


async def test_message_cap_settles(rooms):
    service, _ = rooms
    room = await service.open(opened_by="jarvis", members=list("abcdef"))
    speakers = room.members
    i = 0
    while room.state is RoomState.RUNNING:
        room = await service.say(room.room_id, speakers[i % 6], f"m{i}")
        i += 1
    assert room.settle_reason == "message_cap"
    assert room.message_count == MAX_MESSAGES


async def test_silence_settles(rooms):
    service, _ = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    room = await service.say(room.room_id, "a", "one thing")
    room = await service.pass_turn(room.room_id, "b")
    assert room.round == 2 and room.state is RoomState.RUNNING
    room = await service.pass_turn(room.room_id, "a")
    room = await service.say(room.room_id, "b", "")  # empty = silence
    assert room.state is RoomState.SETTLED
    assert room.settle_reason == "silence"


async def test_room_survives_reopen(rooms, tmp_path: Path):
    service, store = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    await service.say(room.room_id, "a", "hi")
    again = Rooms(store)
    loaded = await again.get(room.room_id)
    assert loaded is not None
    assert loaded.next_speaker == "b"
    assert loaded.message_count == 1
    assert (await again.list(state=RoomState.RUNNING))[0].room_id == room.room_id


async def test_concurrent_terminal_transitions_emit_once(rooms):
    service, store = rooms
    other_process_view = Rooms(store)
    room = await service.open(opened_by="jarvis", members=["a", "b"])

    settled, failed = await asyncio.gather(
        service.settle(room.room_id, reason="done"),
        other_process_view.fail(room.room_id, reason="boom"),
    )

    loaded = await service.get(room.room_id)
    assert loaded is not None
    assert loaded.state in (RoomState.SETTLED, RoomState.FAILED)
    assert settled.state is loaded.state
    assert failed.state is loaded.state
    events = await store.events_for_trace(room.trace_id)
    terminal = [event for event in events if event.msg_type is MsgType.ROOM_SETTLE]
    assert len(terminal) == 1


async def test_manual_settle_and_fail(rooms):
    service, store = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    settled = await service.settle(room.room_id, reason="kill_switch")
    assert settled.state is RoomState.SETTLED
    assert (await service.settle(room.room_id, reason="again")).settle_reason == "kill_switch"
    other = await service.open(opened_by="jarvis", members=["c", "d"])
    failed = await service.fail(other.room_id, reason="boom")
    assert failed.state is RoomState.FAILED
    assert failed.settle_reason == "boom"
    failed_again = await service.fail(other.room_id, reason="different")
    assert failed_again.settle_reason == "boom"
    events = await store.events_for_trace(other.trace_id)
    assert [event.msg_type for event in events] == [MsgType.ROOM_OPEN, MsgType.ROOM_SETTLE]
    assert events[-1].payload["reason"] == "boom"
    assert events[-1].payload["failed"] is True
    with pytest.raises(RoomError):
        await service.settle("nope", reason="x")
