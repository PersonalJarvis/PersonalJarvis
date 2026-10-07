"""Shared context, explicit spend bounds, persistence and stop ownership."""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.meetings import MEETING_PASS, Meetings, is_pass
from jarvis.society.rooms import MAX_MEMBERS
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
    # A meeting turn is not a message typed into the agent's own chat.
    assert all(call[2]["direct_user"] is False for call in svc.sent)
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


def test_the_personal_chat_hides_the_same_silence_marker():
    root = Path(__file__).resolve().parents[3]
    source = (
        root / "jarvis/ui/web/frontend/src/components/agentchat/meetingPass.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r'export const MEETING_PASS = "([^"]+)"', source)
    assert match is not None and match.group(1) == MEETING_PASS


def test_the_meeting_view_uses_the_same_member_cap():
    root = Path(__file__).resolve().parents[3]
    source = (
        root / "jarvis/ui/web/frontend/src/components/society/chat/MeetingChat.tsx"
    ).read_text(encoding="utf-8")
    match = re.search(r"export const MEETING_MAX_MEMBERS = (\d+);", source)
    assert match is not None and int(match.group(1)) == MAX_MEMBERS


async def test_the_lead_takes_part_under_the_wake_word_name(tmp_path):
    svc = MeetingChatFake(AgentChatStore(tmp_path / "chat.db"))
    cfg = SimpleNamespace(
        memory=SimpleNamespace(data_dir=str(tmp_path)),
        trigger=SimpleNamespace(wake_word=SimpleNamespace(phrase="Hey Athena")),
    )
    rt = SocietyRuntime(tmp_path, chat_service=lambda: svc, cfg=lambda: cfg)
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="ollama", model="fake")
        await rt.roster.update("jarvis", {"provider": "ollama", "model": "fake"})
        await rt.store.create_chat_group("team", "Team", ["jarvis", "scout"])
        await rt.meetings.start("team", "Plan the week")
        await asyncio.gather(*rt.meetings._tasks.values())
        assert len(svc.sent) == 2
        assert "these agents: Athena, Scout" in svc.sent[0][1]
        assert "Athena: Contribution from society:jarvis" in svc.sent[1][1]
    finally:
        await rt.close()
        svc.store.close()


def test_the_pass_marker_tolerates_stray_punctuation_only():
    assert is_pass("  [[MEETING_PASS]].\n")
    assert not is_pass("[[MEETING_PASS]] but I disagree")
    assert not is_pass("")


async def test_meeting_turns_queue_no_review(meeting):
    import json

    from jarvis.core.protocols import ChatCompletion, ChatTurn

    rt, svc = meeting
    reviewed = []
    rt.conversations.queue_review = lambda *args, **kwargs: reviewed.append(args) or True
    original_send = svc.send

    async def send(session_id, text, **kwargs):
        turn_id = await original_send(session_id, text, **kwargs)
        # The service runs the society completion hook before the turn ends.
        events = svc.store.list_events(session_id, tail=10)
        turn = ChatTurn(session_id, turn_id, text, kwargs["direct_user"], turn_id)
        await rt.turn_completed(
            SimpleNamespace(session_id=session_id), ChatCompletion(turn, json.dumps(events))
        )
        return turn_id

    svc.send = send
    await rt.meetings.start("team", "Plan")
    await asyncio.gather(*rt.meetings._tasks.values())
    assert len(svc.sent) == 2
    assert reviewed == []
    assert not rt.meetings.is_contributing("society:scout")
    # The same completion outside a meeting is still reviewed.
    events = svc.store.list_events("society:scout", tail=10)
    turn = ChatTurn("society:scout", "turn-0", "Plan", False, "turn-0")
    await rt.turn_completed(
        SimpleNamespace(session_id="society:scout"), ChatCompletion(turn, json.dumps(events))
    )
    assert len(reviewed) == 1


async def test_a_turn_without_reply_text_fails_the_round(meeting):
    rt, svc = meeting

    async def send(session_id, text, **kwargs):
        svc.sent.append((session_id, text, kwargs, "turn-x"))
        from jarvis.agent_chat.events import make_event

        svc.store.append_event(
            session_id, make_event("turn_finished", {"turn_id": "turn-x", "status": "done"})
        )
        return "turn-x"

    svc.send = send
    await rt.meetings.start("team", "Question")
    await asyncio.gather(*rt.meetings._tasks.values())
    snapshot = await rt.meetings.snapshot("team")
    assert snapshot["room"]["state"] == "failed"
    assert [m["speaker"] for m in snapshot["messages"]] == ["user"]
    assert len(svc.sent) == 1
