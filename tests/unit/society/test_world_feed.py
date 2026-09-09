"""Speech on the board reaches the island -- and nothing else does."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.events import SocietyMessageSent, SocietyRoomChanged
from jarvis.society.events import MsgType
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.world_feed import ROOM_TYPES, VISIBLE_TYPES, WorldFeed, preview
from jarvis.ui.web.schema import event_to_ws_envelope

_REPO = Path(__file__).resolve().parents[3]
_CONVERSATION_TS = _REPO / "jarvis/ui/web/frontend/src/components/society/world/conversation.ts"


@pytest.fixture
async def rt(tmp_path: Path):
    pushed: list[Any] = []
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False, event_publish=pushed.append)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout")
    await runtime.roster.create(name="Archivist")
    pushed.clear()
    try:
        yield runtime, pushed
    finally:
        await runtime.close()


def _messages(pushed: list[Any]) -> list[SocietyMessageSent]:
    return [e for e in pushed if isinstance(e, SocietyMessageSent)]


def _rooms(pushed: list[Any]) -> list[SocietyRoomChanged]:
    return [e for e in pushed if isinstance(e, SocietyRoomChanged)]


# --------------------------------------------------------------- preview


def test_preview_collapses_whitespace_and_keeps_short_text_whole():
    text, chars, cut = preview("  hello\n\n  world  ")
    assert (text, chars, cut) == ("hello world", 18, False)


def test_preview_cuts_on_a_word_boundary_and_reports_the_original():
    body = "word " * 200
    text, chars, cut = preview(body)
    assert cut is True
    assert chars == len(body)
    assert len(text) <= 181  # the limit plus the ellipsis
    assert text.endswith("…")
    assert not text.rstrip("…").endswith("wor")  # never sliced mid-word


def test_preview_takes_one_very_long_word_whole_rather_than_only_an_ellipsis():
    text, _, cut = preview("x" * 400)
    assert cut is True
    assert len(text.rstrip("…")) == 180


# --------------------------------------------------------- direct messages


async def test_a_message_between_two_agents_reaches_the_island_once(rt):
    runtime, pushed = rt
    env = await runtime.say(from_agent="scout", to_agent="archivist", text="Found the manifest.")

    sent = _messages(pushed)
    assert len(sent) == 1
    event = sent[0]
    assert event.from_agent == "scout"
    assert event.to_agent == "archivist"
    assert event.msg_type == "SAY"
    assert event.society_trace == env.trace_id
    assert event.text == "Found the manifest."
    assert event.truncated is False
    assert event.seq > 0
    assert event.event_id
    assert event.room_id == ""
    assert event.source_layer == "society"


async def test_every_talking_type_is_forwarded(rt):
    runtime, pushed = rt
    for kind in (MsgType.QUERY, MsgType.ANSWER, MsgType.PROPOSE):
        await runtime.say(from_agent="scout", to_agent="archivist", text="ping", msg_type=kind)
    assert [e.msg_type for e in _messages(pushed)] == ["QUERY", "ANSWER", "PROPOSE"]


async def test_a_body_is_never_broadcast_in_full(rt):
    runtime, pushed = rt
    body = "sentence. " * 400
    await runtime.say(from_agent="scout", to_agent="archivist", text=body)

    event = _messages(pushed)[0]
    assert event.truncated is True
    assert len(event.text) <= 181
    assert event.text_chars == len(body)


async def test_the_plumbing_types_stay_off_the_island(rt):
    runtime, pushed = rt
    for kind in (MsgType.CLAIM, MsgType.RESULT, MsgType.HOLD, MsgType.VETO, MsgType.DIGEST):
        await runtime.say(from_agent="scheduler", to_agent="scout", text="internal", msg_type=kind)
    assert _messages(pushed) == []
    assert _rooms(pushed) == []


# ------------------------------------------------------------------ rooms


async def test_a_room_opens_speaks_and_settles(rt):
    runtime, pushed = rt
    room = await runtime.rooms.open(
        opened_by="jarvis", members=["scout", "archivist"], topic="Plan the week"
    )
    await runtime.rooms.say(room.room_id, "scout", "I will take the mail.")
    await runtime.rooms.settle(room.room_id, reason="user", by="user")

    opened, settled = _rooms(pushed)
    assert opened.phase == "open"
    assert opened.members == ("scout", "archivist")
    assert opened.topic == "Plan the week"
    assert (opened.max_rounds, opened.max_messages) == (3, 10)
    assert opened.society_trace == f"room:{room.room_id}"

    said = _messages(pushed)
    assert len(said) == 1
    assert said[0].room_id == room.room_id
    assert said[0].to_agent == ""
    assert said[0].room_round == 1
    assert said[0].from_agent == "scout"

    assert settled.phase == "settle"
    assert settled.reason == "user"
    assert settled.messages == 1


# ------------------------------------------------------------- resilience


async def test_attaching_twice_subscribes_once(rt):
    runtime, pushed = rt
    before = runtime.store.bus.active_subs
    runtime.world_feed.attach()
    assert runtime.store.bus.active_subs == before

    await runtime.say(from_agent="scout", to_agent="archivist", text="once")
    assert len(_messages(pushed)) == 1


async def test_a_failing_publisher_never_breaks_the_append(tmp_path: Path):
    def boom(_event: Any) -> None:
        raise RuntimeError("no bus today")

    runtime = SocietyRuntime(tmp_path, seed_starter_team=False, event_publish=boom)
    await runtime.ensure_started()
    try:
        await runtime.roster.create(name="Scout")
        await runtime.roster.create(name="Archivist")
        env = await runtime.say(from_agent="scout", to_agent="archivist", text="still lands")
        assert env.seq is not None
        assert len(await runtime.store.events_for_trace(env.trace_id)) == 1
    finally:
        await runtime.close()


async def test_detach_stops_the_feed(rt):
    runtime, pushed = rt
    runtime.world_feed.detach()
    await runtime.say(from_agent="scout", to_agent="archivist", text="into the void")
    assert _messages(pushed) == []


# ------------------------------------------------------------ the wire


async def test_the_event_survives_the_websocket_sanitizer(rt):
    runtime, pushed = rt
    room = await runtime.rooms.open(opened_by="jarvis", members=["scout", "archivist"])
    env = await runtime.say(from_agent="scout", to_agent="archivist", text="hello")

    message = event_to_ws_envelope(_messages(pushed)[0])
    assert message["event_name"] == "SocietyMessageSent"
    assert message["payload"]["society_trace"] == env.trace_id
    # The base UUID owns `trace_id`; ours must not have been shadowed away.
    assert "trace_id" not in message["payload"]

    opened = event_to_ws_envelope(_rooms(pushed)[0])
    assert opened["event_name"] == "SocietyRoomChanged"
    assert opened["payload"]["members"] == ["scout", "archivist"]
    assert opened["payload"]["room_id"] == room.room_id


# ------------------------------------------------------------- drift guard


def test_the_world_only_reacts_to_types_the_board_can_produce():
    assert (VISIBLE_TYPES | ROOM_TYPES) <= set(MsgType)
    assert VISIBLE_TYPES.isdisjoint(ROOM_TYPES)


@pytest.mark.skipif(not _CONVERSATION_TS.exists(), reason="the world layer is not built yet")
def test_the_typescript_half_lists_the_same_types():
    source = _CONVERSATION_TS.read_text(encoding="utf-8")
    match = re.search(r"WORLD_MSG_TYPES[^=]*=\s*\[(.*?)\]", source, re.S)
    assert match, "conversation.ts must export WORLD_MSG_TYPES"
    listed = set(re.findall(r'"([A-Z_]+)"', match.group(1)))
    assert listed == {str(t) for t in VISIBLE_TYPES | ROOM_TYPES}


def test_the_relay_is_wired_into_the_runtime(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    assert isinstance(runtime.world_feed, WorldFeed)
