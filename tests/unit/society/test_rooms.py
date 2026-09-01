"""Rooms: 2-6 members, <=3 rounds, <=10 messages, silence allowed, deterministic."""

from __future__ import annotations

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


async def test_manual_settle_and_fail(rooms):
    service, _ = rooms
    room = await service.open(opened_by="jarvis", members=["a", "b"])
    settled = await service.settle(room.room_id, reason="kill_switch")
    assert settled.state is RoomState.SETTLED
    assert (await service.settle(room.room_id, reason="again")).settle_reason == "kill_switch"
    other = await service.open(opened_by="jarvis", members=["c", "d"])
    failed = await service.fail(other.room_id, reason="boom")
    assert failed.state is RoomState.FAILED
    with pytest.raises(RoomError):
        await service.settle("nope", reason="x")
