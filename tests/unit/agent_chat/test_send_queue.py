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
            get_session=lambda sid: SimpleNamespace(session_id=sid, surface=self.surface),
            queue_notice_events=lambda sid: [
                {"kind": "notice", "payload": payload} for s, payload in self.notices if s == sid
            ],
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
        ("agent", "coding-1"),
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
    # The runner's own error text never reaches the chat (AP-34).
    assert "refused" not in failed[0]["text"]


async def test_waiting_for_a_credential_does_not_expire_accepted_messages():
    chat = BusyChat()
    chat.busy.add("society:scout")
    queue_id = (await send_or_queue(chat, "society:scout", "after the credential"))[1]
    await asyncio.sleep(0.08)
    assert chat.sent == []
    assert not any(p.get("status") == "failed" for _, p in chat.notices)
    chat.busy.clear()
    await _until(lambda: any(
        p.get("queue_id") == queue_id and p.get("status") == "sent"
        for _, p in chat.notices
    ))


@pytest.mark.parametrize(
    "surface, session_id", [("jarvis", "chat-1"), ("society", "society:jarvis")]
)
async def test_lead_chat_queues_without_calling_send_on_the_running_goal(surface, session_id):
    class SteeringChat(BusyChat):
        async def send(self, sid, text, attachments=None, *, tool_choices=None):
            assert not self.is_running(sid), "A followup must not cancel a live goal"
            return await super().send(sid, text, attachments, tool_choices=tool_choices)

    chat = SteeringChat(surface)
    chat.busy.add(session_id)
    _, queue_id = await send_or_queue(chat, session_id, "followup")
    assert queue_id and not chat.sent
    chat.busy.clear()
    await _until(lambda: chat.sent)
    assert chat.sent == [(session_id, "followup")]


async def test_followups_do_not_overtake_a_first_send_during_async_setup():
    started, release = asyncio.Event(), asyncio.Event()

    class PreparingChat(BusyChat):
        async def send(self, sid, text, attachments=None, *, tool_choices=None):
            if text == "first":
                started.set()
                await release.wait()
                raise SessionBusy(sid)  # A teammate temporarily owns the shared seat.
            return await super().send(sid, text, attachments, tool_choices=tool_choices)

    chat = PreparingChat()
    first = asyncio.create_task(send_or_queue(chat, "society:scout", "first"))
    await started.wait()
    await send_or_queue(chat, "society:scout", "second")
    await send_or_queue(chat, "society:scout", "third")
    await asyncio.sleep(0.04)
    assert chat.sent == []
    release.set()
    await first
    # Replace the setup-only barrier with a free seat after all admissions.
    chat.send = BusyChat.send.__get__(chat)
    await _until(lambda: len(chat.sent) == 3)
    assert [text for _, text in chat.sent] == ["first", "second", "third"]


async def test_queued_messages_keep_attachments_and_their_own_timezone():
    from jarvis.tasks.context import client_timezone

    delivered = []

    class ContextChat(BusyChat):
        async def send(self, sid, text, attachments=None, *, tool_choices=None):
            turn = await super().send(sid, text, attachments, tool_choices=tool_choices)
            delivered.append((attachments, tool_choices, client_timezone.get()))
            return turn

    chat = ContextChat()
    chat.busy.add("society:scout")
    for timezone in ("Europe/Berlin", "America/New_York"):
        token = client_timezone.set(timezone)
        try:
            await send_or_queue(
                chat, "society:scout", timezone, [{"name": timezone}], tool_choices=[timezone]
            )
        finally:
            client_timezone.reset(token)
    chat.busy.clear()
    await _until(lambda: len(delivered) == 2)
    assert delivered == [
        ([{"name": zone}], [zone], zone)
        for zone in ("Europe/Berlin", "America/New_York")
    ]


async def test_opening_the_chat_closes_waiting_notices_a_restart_orphaned():
    chat = BusyChat()
    chat.notices.append(
        ("society:scout", {"kind": "message_queued", "queue_id": "old", "text": "lost"})
    )
    chat.notices.append(
        ("society:scout", {"kind": "message_queued", "queue_id": "done", "text": "ok"})
    )
    chat.notices.append(
        ("society:scout", {"kind": "message_dequeued", "queue_id": "done", "status": "sent"})
    )
    assert await send_queue.close_orphans(chat, "society:scout") == 1
    closed = chat.notices[-1][1]
    assert closed["queue_id"] == "old" and closed["status"] == "failed"
    assert await send_queue.close_orphans(chat, "society:scout") == 0


async def test_orphan_recovery_keeps_a_message_admitted_during_history_read(monkeypatch):
    chat = BusyChat(surface="jarvis")
    chat.busy.add("main-chat")
    original_read = chat.store.queue_notice_events
    admitted = []
    loop = asyncio.get_running_loop()

    def read_with_admission(sid):
        if not admitted:
            admitted.append(asyncio.run_coroutine_threadsafe(
                send_or_queue(chat, sid, "arrived during recovery"), loop,
            ).result(timeout=2))
        return original_read(sid)

    monkeypatch.setattr(chat.store, "queue_notice_events", read_with_admission)
    assert await send_queue.close_orphans(chat, "main-chat") == 0
    assert len(admitted) == 1 and admitted[0][1]
    assert not any(payload.get("kind") == "message_dequeued" for _, payload in chat.notices)
    chat._society_send_queues["main-chat"].drain.cancel()


async def test_orphan_recovery_does_not_fail_a_message_sent_during_history_read(monkeypatch):
    chat = BusyChat(surface="jarvis")
    chat.busy.add("main-chat")
    original_read = chat.store.queue_notice_events
    loop = asyncio.get_running_loop()
    reads = []

    async def finish_message():
        chat.busy.clear()
        await _until(lambda: bool(chat.sent))
        await _until(lambda: any(
            payload.get("status") == "sent" for _, payload in chat.notices
        ))

    def read_while_message_completes(sid):
        reads.append(sid)
        if len(reads) == 1:
            asyncio.run_coroutine_threadsafe(
                send_or_queue(chat, sid, "arrived and finished during recovery"), loop,
            ).result(timeout=2)
            stale_snapshot = original_read(sid)
            asyncio.run_coroutine_threadsafe(finish_message(), loop).result(timeout=2)
            return stale_snapshot
        return original_read(sid)

    monkeypatch.setattr(chat.store, "queue_notice_events", read_while_message_completes)
    assert await send_queue.close_orphans(chat, "main-chat") == 0
    assert len(reads) == 2
    assert chat.sent == [("main-chat", "arrived and finished during recovery")]
    assert [
        payload.get("status") for _, payload in chat.notices
        if payload.get("kind") == "message_dequeued"
    ] == ["sent"]


async def test_orphan_recovery_retries_at_most_once_during_continuous_admissions(monkeypatch):
    chat = BusyChat(surface="jarvis")
    chat.notices.append(
        ("main-chat", {"kind": "message_queued", "queue_id": "old", "text": "lost"}),
    )
    original_read = chat.store.queue_notice_events
    reads = []

    def unstable_read(sid):
        reads.append(sid)
        chat._society_send_generation = getattr(chat, "_society_send_generation", 0) + 1
        return original_read(sid)

    monkeypatch.setattr(chat.store, "queue_notice_events", unstable_read)
    assert await send_queue.close_orphans(chat, "main-chat") == 0
    assert len(reads) == 2
    assert not any(payload.get("kind") == "message_dequeued" for _, payload in chat.notices)


async def test_concurrent_reopens_publish_one_orphan_receipt(monkeypatch):
    chat = BusyChat(surface="jarvis")
    chat.notices.append(
        ("main-chat", {"kind": "message_queued", "queue_id": "old", "text": "lost"}),
    )
    original_post = chat.post_notice

    async def slow_post(sid, payload):
        await asyncio.sleep(0.01)
        await original_post(sid, payload)

    monkeypatch.setattr(chat, "post_notice", slow_post)
    results = await asyncio.gather(
        send_queue.close_orphans(chat, "main-chat"),
        send_queue.close_orphans(chat, "main-chat"),
    )
    assert sum(results) == 1
    assert sum(
        payload.get("kind") == "message_dequeued" for _, payload in chat.notices
    ) == 1
