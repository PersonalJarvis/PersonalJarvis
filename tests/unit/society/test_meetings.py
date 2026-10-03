"""Shared context, explicit spend bounds, persistence and stop ownership."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.meetings import Meetings
from jarvis.society.runtime import SocietyRuntime
from tests.fakes.meeting_chat import MeetingChatFake


@pytest.fixture
async def meeting(tmp_path):
    svc = MeetingChatFake(AgentChatStore(tmp_path / "chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    rt = SocietyRuntime(tmp_path, chat_service=lambda: svc, cfg=lambda: cfg)
    await rt.ensure_started()
    for name in ("Scout", "Writer"):
        await rt.roster.create(name=name, provider="ollama", model="fake")
    await rt.store.create_chat_group("team", "Team", ["scout", "writer"])
    try:
        yield rt, svc
    finally:
        await rt.close()
        svc.store.close()


async def test_one_user_message_runs_one_serial_round_with_shared_context(meeting):
    rt, svc = meeting
    await rt.meetings.start("team", "Compare ideas")
    await asyncio.gather(*rt.meetings._tasks.values())
    assert len(svc.sent) == 2
    assert "Contribution from society:scout" in svc.sent[1][1]
    assert all(call[2]["read_only"] and call[2]["tool_choices"] == [] for call in svc.sent)
    snapshot = await rt.meetings.snapshot("team")
    assert [m["speaker"] for m in snapshot["messages"]] == ["user", "scout", "writer"]
    assert snapshot["room"]["settle_reason"] == "user_round_complete"
    assert not snapshot["running"]
    await rt.meetings.snapshot("team")
    assert len(svc.sent) == 2  # Reading never bills or resumes.


async def test_pause_busy_and_member_cap_refuse_before_spend(meeting):
    rt, svc = meeting
    await rt.store.set_kill_switch(True)
    with pytest.raises(ValueError, match="paused"):
        await rt.meetings.start("team", "Question")
    await rt.store.set_kill_switch(False)
    svc.busy.add("society:writer")
    with pytest.raises(ValueError, match="working"):
        await rt.meetings.start("team", "Question")
    svc.busy.clear()
    await rt.store.update_chat_group("team", "Team", list("abcdefg"))
    with pytest.raises(ValueError, match="two to six"):
        await rt.meetings.start("team", "Question")
    assert svc.sent == []


async def test_stop_before_driver_starts_releases_group(meeting):
    rt, svc = meeting
    await rt.meetings.start("team", "Stop now")
    await rt.meetings.stop("team")
    snapshot = await rt.meetings.snapshot("team")
    assert not snapshot["running"]
    assert snapshot["room"]["state"] == "settled"
    assert svc.sent == []


async def test_pair_conversations_reserve_the_agent_before_spend(meeting):
    rt, svc = meeting
    svc.busy.add("society:writer:with:scout")
    with pytest.raises(ValueError, match="working"):
        await rt.meetings.start("team", "Question")
    assert svc.sent == []


async def test_pair_conversation_started_mid_round_prevents_next_contribution(meeting):
    rt, svc = meeting
    svc.hold = True
    await rt.meetings.start("team", "Question")
    await svc.started.wait()
    svc.busy.add("society:writer:with:scout")
    svc.release.set()
    await asyncio.gather(*rt.meetings._tasks.values())
    assert len(svc.sent) == 1
    assert (await rt.meetings.snapshot("team"))["room"]["state"] == "failed"


async def test_stop_during_send_cancels_only_owned_turn(meeting):
    rt, svc = meeting
    svc.hold = True
    await rt.meetings.start("team", "Stop during send")
    await svc.started.wait()
    stop = asyncio.create_task(rt.meetings.stop("team"))
    await asyncio.sleep(0)
    svc.release.set()
    await stop
    assert svc.cancelled == [("society:scout", "turn-0")]
    assert len(svc.sent) == 1
    assert not (await rt.meetings.snapshot("team"))["running"]


async def test_latest_twenty_rounds_are_chronological_and_durable(meeting):
    rt, svc = meeting
    for i in range(22):
        room = await rt.rooms.open(
            opened_by="user", members=["scout", "writer"], topic=str(i), room_id=f"meeting:team:{i}"
        )
        await rt.rooms.settle(room.room_id, reason="user_round_complete")
    snapshot = await rt.meetings.snapshot("team")
    assert [m["text"] for m in snapshot["messages"]] == [str(i) for i in range(2, 22)]
    assert snapshot["room"]["topic"] == "21"
    assert svc.sent == []


async def test_stop_keeps_ownership_until_cancel_reaps_the_turn(meeting):
    rt, svc = meeting
    svc.hold = svc.hold_cancel = True
    await rt.meetings.start("team", "Stop this")
    await svc.started.wait()
    stop = asyncio.create_task(rt.meetings.stop("team"))
    await asyncio.sleep(0)
    svc.release.set()
    await svc.cancel_started.wait()
    assert not stop.done()
    assert rt.meetings.is_running("team")
    svc.cancel_release.set()
    await stop
    assert not rt.meetings.is_running("team")


async def test_close_includes_start_already_waiting_on_storage(meeting):
    rt, svc = meeting
    entered = asyncio.Event()
    release = asyncio.Event()

    class WaitingMeetings(Meetings):
        async def snapshot(self, group_id):
            entered.set()
            await release.wait()
            return await super().snapshot(group_id)

    rt.meetings = WaitingMeetings(rt)
    start = asyncio.create_task(rt.meetings.start("team", "Question"))
    await entered.wait()
    close = asyncio.create_task(rt.meetings.close())
    await asyncio.sleep(0)
    assert not close.done()
    release.set()
    await start
    await close
    assert all(task.done() for task in rt.meetings._tasks.values())
    with pytest.raises(ValueError, match="shutting down"):
        await rt.meetings.start("team", "No new spend")


@pytest.mark.parametrize("silent", ["society:scout", "society:writer", "both"])
async def test_agents_can_pass_without_publishing_or_polluting_context(meeting, silent):
    rt, svc = meeting
    for session in ("society:scout", "society:writer"):
        if silent in (session, "both"):
            svc.replies[session] = "  [[MEETING_PASS]]\n"
    await rt.meetings.start("team", "Who has something useful to add?")
    await asyncio.gather(*rt.meetings._tasks.values())
    snapshot = await rt.meetings.snapshot("team")
    assert len(svc.sent) == 2
    assert not snapshot["running"]
    assert snapshot["room"]["state"] == "settled"
    assert all("[[MEETING_PASS]]" not in m["text"] for m in snapshot["messages"])
    speakers = [m["speaker"] for m in snapshot["messages"]]
    assert speakers == (
        ["user"]
        if silent == "both"
        else ["user", "writer" if silent == "society:scout" else "scout"]
    )
    assert "repeat an answer" in svc.sent[0][1]
    if silent == "society:scout":
        assert "Contribution from society:scout" not in svc.sent[1][1]
    assert snapshot["room"]["settle_reason"] == (
        "silence" if silent == "both" else "user_round_complete"
    )
