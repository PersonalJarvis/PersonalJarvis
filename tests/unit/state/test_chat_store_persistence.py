"""ChatStore SQLite persistence (Chats conversation manager, Slice 2).

The store moved from an in-memory dict to a sync sqlite3 backing (mirroring
``jarvis/sessions/store.py``) so typed chats survive a restart and are
segmented into distinct conversations. The public async API is unchanged;
``db_path`` defaults to ``:memory:`` so existing callers/tests keep working
in-process without touching disk.
"""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import MessageSent
from jarvis.state.chat_store import ChatStore


def _db(tmp_path) -> str:
    return str(tmp_path / "chats.db")


async def test_round_trip_survives_new_instance(tmp_path) -> None:
    """A fresh ChatStore on the same file sees prior threads + messages."""
    path = _db(tmp_path)
    s1 = ChatStore(bus=EventBus(), db_path=path)
    await s1.add_message(thread_id="t1", role="user", text="Remember me")
    await s1.add_message(thread_id="t1", role="assistant", text="I will")

    s2 = ChatStore(bus=EventBus(), db_path=path)
    detail = s2.get_thread("t1")
    assert detail is not None
    assert [(m["role"], m["text"]) for m in detail["messages"]] == [
        ("user", "Remember me"),
        ("assistant", "I will"),
    ]


async def test_threads_are_segmented(tmp_path) -> None:
    s = ChatStore(bus=EventBus(), db_path=_db(tmp_path))
    await s.add_message(thread_id="a", role="user", text="alpha")
    await s.add_message(thread_id="b", role="user", text="bravo")
    assert [m["text"] for m in s.get_thread("a")["messages"]] == ["alpha"]
    assert [m["text"] for m in s.get_thread("b")["messages"]] == ["bravo"]


async def test_list_threads_newest_first_with_preview(tmp_path) -> None:
    s = ChatStore(bus=EventBus(), db_path=_db(tmp_path))
    await s.add_message(thread_id="old", role="user", text="first conversation")
    await s.add_message(thread_id="new", role="user", text="second conversation")
    # Touch "old" again so it becomes the most recently updated.
    await s.add_message(thread_id="old", role="assistant", text="reply")

    rows = s.list_threads()
    assert [r["thread_id"] for r in rows] == ["old", "new"]
    old = next(r for r in rows if r["thread_id"] == "old")
    assert old["preview"] == "first conversation"
    assert old["message_count"] == 2
    assert old["updated_at_ns"] >= old["created_at_ns"]


async def test_title_derived_from_first_user_message(tmp_path) -> None:
    s = ChatStore(bus=EventBus(), db_path=_db(tmp_path))
    await s.add_message(
        thread_id="t", role="user", text="How do I deploy to a VPS?"
    )
    row = next(r for r in s.list_threads() if r["thread_id"] == "t")
    assert row["title"] == "How do I deploy to a VPS?"


async def test_explicit_title_not_overwritten(tmp_path) -> None:
    s = ChatStore(bus=EventBus(), db_path=_db(tmp_path))
    await s.create_thread(title="My pinned chat", thread_id="t")
    await s.add_message(thread_id="t", role="user", text="hello")
    row = next(r for r in s.list_threads() if r["thread_id"] == "t")
    assert row["title"] == "My pinned chat"


async def test_add_message_publishes_message_sent(tmp_path) -> None:
    bus = EventBus()
    seen: list[MessageSent] = []

    async def _collect(e: MessageSent) -> None:
        seen.append(e)

    bus.subscribe(MessageSent, _collect)
    s = ChatStore(bus=bus, db_path=_db(tmp_path))
    await s.add_message(thread_id="t", role="user", text="ping")
    assert any(e.text == "ping" and e.thread_id == "t" for e in seen)


async def test_delete_thread(tmp_path) -> None:
    s = ChatStore(bus=EventBus(), db_path=_db(tmp_path))
    await s.add_message(thread_id="t", role="user", text="bye")
    await s.delete_thread("t")
    assert s.get_thread("t") is None
    assert [r["thread_id"] for r in s.list_threads()] == []


async def test_prune_older_than(tmp_path) -> None:
    s = ChatStore(bus=EventBus(), db_path=_db(tmp_path))
    await s.add_message(thread_id="t", role="user", text="ancient")
    # Backdate the thread well beyond the retention window.
    s._backdate_for_test("t", days=99)
    removed = s.prune_older_than(30)
    assert removed == 1
    assert s.get_thread("t") is None


async def test_in_memory_default_still_works() -> None:
    """No db_path → process-local in-memory DB, backward compatible."""
    s = ChatStore(bus=EventBus())
    await s.add_message(thread_id="t", role="user", text="ephemeral")
    assert s.get_thread("t")["messages"][0]["text"] == "ephemeral"
