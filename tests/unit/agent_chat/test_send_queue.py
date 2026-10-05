"""A created agent's chat queues its person's message instead of refusing it."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.agent_chat import send_queue
from jarvis.agent_chat.send_queue import MAX_QUEUED, QueueFull, send_or_queue
from jarvis.agent_chat.service import SessionBusy


class BusyChat:
    """Just enough of the chat service: one running flag per session."""

    def __init__(self, surface: str = "society") -> None:
        self.surface = surface
        self.busy: set[str] = set()
        self.sent: list[tuple[str, str]] = []
        self.notices: list[tuple[str, dict]] = []
        self.fail_next = False
        self.store = SimpleNamespace(
            get_session=lambda sid: SimpleNamespace(session_id=sid, surface=self.surface)
        )

    def is_running(self, session_id: str) -> bool:
        return session_id in self.busy

    async def send(self, session_id, text, attachments=None, *, tool_choices=None) -> str:
        if session_id in self.busy:
            raise SessionBusy(session_id)
        if self.fail_next:
            self.fail_next = False
            raise ValueError("the runner refused it")
        self.sent.append((session_id, text))
        return f"turn-{len(self.sent)}"

    async def post_notice(self, session_id: str, payload: dict) -> None:
        self.notices.append((session_id, dict(payload)))


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(send_queue, "_POLL_S", 0.01)


async def _until(predicate, limit_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit_s
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def test_a_free_chat_sends_at_once():
    chat = BusyChat()
    turn_id, queue_id = await send_or_queue(chat, "society:scout", "hello")
    assert (turn_id, queue_id) == ("turn-1", "")
    assert chat.notices == []


async def test_a_busy_agent_chat_queues_and_runs_the_message_next():
    chat = BusyChat()
    chat.busy.add("society:scout")
    turn_id, queue_id = await send_or_queue(chat, "society:scout", "and also this")
    assert turn_id == "" and queue_id
    assert chat.notices[0][1] == {
        "kind": "message_queued", "queue_id": queue_id, "text": "and also this",
    }
    await asyncio.sleep(0.05)
    assert chat.sent == []  # still waiting for the running turn
    chat.busy.clear()
    await _until(lambda: chat.sent)
    assert chat.sent == [("society:scout", "and also this")]
    await _until(lambda: len(chat.notices) == 2)
    assert chat.notices[1][1] == {
        "kind": "message_dequeued", "queue_id": queue_id, "status": "sent",
    }


async def test_waiting_messages_keep_their_order():
    chat = BusyChat()
    chat.busy.add("society:scout")
    for text in ("one", "two", "three"):
        await send_or_queue(chat, "society:scout", text)
    chat.busy.clear()

    async def finish_each_turn():
        # Every started turn holds the seat briefly, like a real one.
        while len(chat.sent) < 3:
            seen = len(chat.sent)
            await _until(lambda seen=seen: len(chat.sent) > seen)
            chat.busy.add("society:scout")
            await asyncio.sleep(0.03)
            chat.busy.clear()

    await asyncio.wait_for(finish_each_turn(), timeout=3)
    assert [text for _, text in chat.sent] == ["one", "two", "three"]


async def test_a_new_message_never_overtakes_waiting_ones():
    chat = BusyChat()
    chat.busy.add("society:scout")
    await send_or_queue(chat, "society:scout", "first")
    chat.busy.clear()
    # The seat is free now, but "first" still waits in the queue.
    turn_id, queue_id = await send_or_queue(chat, "society:scout", "second")
    assert turn_id == "" and queue_id
    await _until(lambda: len(chat.sent) == 2)
    assert [text for _, text in chat.sent] == ["first", "second"]


async def test_other_chats_still_refuse_while_busy():
    for surface, session_id in (
        ("jarvis", "chat-1"),
        ("society", "society:jarvis"),
        ("society", "society:scout:routine:t1:r1"),
        ("society", "society:scout:with:jarvis"),
    ):
        chat = BusyChat(surface)
        chat.busy.add(session_id)
        with pytest.raises(SessionBusy):
            await send_or_queue(chat, session_id, "hello")


async def test_the_queue_is_bounded():
    chat = BusyChat()
    chat.busy.add("society:scout")
    for index in range(MAX_QUEUED):
        await send_or_queue(chat, "society:scout", f"message {index}")
    with pytest.raises(QueueFull):
        await send_or_queue(chat, "society:scout", "one too many")


async def test_a_message_that_fails_to_start_is_reported_and_the_next_runs():
    chat = BusyChat()
    chat.busy.add("society:scout")
    first = (await send_or_queue(chat, "society:scout", "broken"))[1]
    await send_or_queue(chat, "society:scout", "fine")
    chat.fail_next = True
    chat.busy.clear()
    await _until(lambda: chat.sent)
    assert chat.sent == [("society:scout", "fine")]
    failed = [p for _, p in chat.notices if p.get("status") == "failed"]
    assert failed and failed[0]["queue_id"] == first
    assert "refused" in failed[0]["text"]
