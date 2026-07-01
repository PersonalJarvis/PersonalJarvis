"""Unit tests for RecallStore (FTS5 + KV)."""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio

from jarvis.memory import RecallStore


@pytest_asyncio.fixture
async def store(tmp_path):
    db = tmp_path / "test.db"
    s = RecallStore(db)
    await s.open()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_schema_created(store):
    # Schema-Tabellen vorhanden
    conn = store._require_conn()
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    names = [r["name"] for r in await cur.fetchall()]
    assert "messages" in names
    assert "kv_store" in names
    assert any(n.startswith("messages_fts") for n in names)


@pytest.mark.asyncio
async def test_record_and_search(store):
    tid = str(uuid4())
    await store.record_message(trace_id=tid, role="user", text="Hallo Welt")
    await store.record_message(trace_id=tid, role="user", text="Python Programming")
    await store.record_message(trace_id=tid, role="user", text="Fish recipes")

    results = await store.search_messages("python", k=5)
    assert len(results) == 1
    assert "Python" in results[0]["text"]


@pytest.mark.asyncio
async def test_bm25_ranking(store):
    tid = str(uuid4())
    await store.record_message(trace_id=tid, role="user", text="apple banana cherry")
    await store.record_message(trace_id=tid, role="user", text="apple apple apple")
    results = await store.search_messages("apple", k=5)
    # The message with more "apple" occurrences should rank better (lower bm25 score)
    assert len(results) == 2
    ranks = [r["rank"] for r in results]
    assert ranks[0] <= ranks[1]


@pytest.mark.asyncio
async def test_kv_put_get_forget(store):
    await store.put("prefs", "theme", {"dark": True, "accent": "blue"})
    value = await store.get("prefs", "theme")
    assert value == {"dark": True, "accent": "blue"}

    await store.forget("prefs", "theme")
    assert await store.get("prefs", "theme") is None


@pytest.mark.asyncio
async def test_kv_overwrite(store):
    await store.put("prefs", "theme", {"dark": True})
    await store.put("prefs", "theme", {"dark": False})
    v = await store.get("prefs", "theme")
    assert v == {"dark": False}


@pytest.mark.asyncio
async def test_recent_messages(store):
    tid = str(uuid4())
    for i in range(5):
        await store.record_message(trace_id=tid, role="user", text=f"msg-{i}")
    recent = await store.recent_messages(limit=3)
    assert len(recent) == 3
    texts = [r["text"] for r in recent]
    assert texts == ["msg-4", "msg-3", "msg-2"]


@pytest.mark.asyncio
async def test_search_filters_role(store):
    tid = str(uuid4())
    await store.record_message(trace_id=tid, role="user", text="hello world")
    await store.record_message(trace_id=tid, role="assistant", text="hello there")
    results = await store.search_messages("hello", k=5, role="assistant")
    assert len(results) == 1
    assert results[0]["role"] == "assistant"
