"""REST tests for the Chats conversation manager router (Slice 3).

Isolated: a minimal FastAPI app with only ``chats_router`` and fakes injected
on ``app.state`` (the established pattern), driven via httpx ASGITransport.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from jarvis.core.bus import EventBus
from jarvis.state.chat_store import ChatStore
from jarvis.ui.web.chats_routes import router as chats_router


class _FakeBrain:
    def __init__(self) -> None:
        self.seeded: list[tuple[str, str]] | None = None

    def seed_history(self, turns) -> None:
        self.seeded = list(turns)


class _FakePipeline:
    def __init__(self, armed: bool = True) -> None:
        self.armed = armed
        self.seed_messages = None

    def request_voice_session(self, *, seed_messages) -> bool:
        self.seed_messages = list(seed_messages)
        return self.armed


def _fake_session_store():
    """One voice session 'v1' with a single user+assistant turn."""
    sessions = [
        SimpleNamespace(
            id="v1",
            preview="how is the weather",
            started_ms=1_000,
            ended_ms=2_000,
            turn_count=1,
        )
    ]
    turns = [
        SimpleNamespace(
            user_text="how is the weather",
            jarvis_text="Sunny.",
            started_ms=1_000,
            ended_ms=1_500,
        )
    ]

    return SimpleNamespace(
        list_sessions=lambda *, limit=100, include_empty=True: list(sessions),
        get_session=lambda cid: sessions[0] if cid == "v1" else None,
        get_turns=lambda cid: list(turns) if cid == "v1" else [],
    )


async def _make_app(*, with_session=False, with_brain=False, with_pipeline=False):
    app = FastAPI()
    app.include_router(chats_router)
    store = ChatStore(bus=EventBus())  # in-memory
    app.state.chat_store = store
    app.state.session_store = _fake_session_store() if with_session else None
    app.state.brain = _FakeBrain() if with_brain else None
    app.state.speech_pipeline = _FakePipeline() if with_pipeline else None
    return app, store


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def test_list_merges_text_and_voice_newest_first() -> None:
    app, store = await _make_app(with_session=True)
    await store.add_message(thread_id="t1", role="user", text="hello text")
    async with _client(app) as c:
        r = await c.get("/api/chats")
    assert r.status_code == 200
    rows = r.json()
    kinds = {row["kind"] for row in rows}
    assert kinds == {"text", "voice"}
    # text thread was just updated → newer than the backdated voice session.
    assert rows[0]["kind"] == "text"
    assert rows[0]["preview"] == "hello text"


async def test_list_text_only_without_session_store() -> None:
    app, store = await _make_app(with_session=False)
    await store.add_message(thread_id="t1", role="user", text="solo")
    async with _client(app) as c:
        r = await c.get("/api/chats")
    rows = r.json()
    assert [row["kind"] for row in rows] == ["text"]


async def test_get_text_conversation_detail() -> None:
    app, store = await _make_app()
    await store.add_message(thread_id="t1", role="user", text="q")
    await store.add_message(thread_id="t1", role="assistant", text="a")
    async with _client(app) as c:
        r = await c.get("/api/chats/text/t1")
    assert r.status_code == 200
    body = r.json()
    assert [(m["role"], m["text"]) for m in body["messages"]] == [
        ("user", "q"),
        ("assistant", "a"),
    ]


async def test_get_voice_conversation_flattens_turns() -> None:
    app, _ = await _make_app(with_session=True)
    async with _client(app) as c:
        r = await c.get("/api/chats/voice/v1")
    assert r.status_code == 200
    body = r.json()
    assert [(m["role"], m["text"]) for m in body["messages"]] == [
        ("user", "how is the weather"),
        ("assistant", "Sunny."),
    ]


async def test_get_unknown_conversation_404() -> None:
    app, _ = await _make_app()
    async with _client(app) as c:
        r = await c.get("/api/chats/text/nope")
    assert r.status_code == 404


async def test_create_chat() -> None:
    app, store = await _make_app()
    async with _client(app) as c:
        r = await c.post("/api/chats", json={"title": "Planning"})
    assert r.status_code == 200
    tid = r.json()["id"]
    assert store.get_thread(tid) is not None


async def test_resume_seeds_web_brain() -> None:
    app, store = await _make_app(with_brain=True)
    await store.add_message(thread_id="t1", role="user", text="remember this")
    await store.add_message(thread_id="t1", role="assistant", text="noted")
    async with _client(app) as c:
        r = await c.post("/api/chats/text/t1/resume")
    assert r.status_code == 200
    assert r.json()["seeded_turns"] == 2
    brain: _FakeBrain = app.state.brain
    assert brain.seeded == [("user", "remember this"), ("assistant", "noted")]


async def test_speak_without_pipeline_503() -> None:
    app, store = await _make_app(with_pipeline=False)
    await store.add_message(thread_id="t1", role="user", text="hi")
    async with _client(app) as c:
        r = await c.post("/api/chats/text/t1/speak")
    assert r.status_code == 503


async def test_speak_arms_pipeline_with_seed() -> None:
    app, store = await _make_app(with_pipeline=True)
    await store.add_message(thread_id="t1", role="user", text="continue please")
    await store.add_message(thread_id="t1", role="assistant", text="ok")
    async with _client(app) as c:
        r = await c.post("/api/chats/text/t1/speak")
    assert r.status_code == 200
    assert r.json() == {"armed": True, "seeded_turns": 2}
    pipe: _FakePipeline = app.state.speech_pipeline
    assert pipe.seed_messages == [("user", "continue please"), ("assistant", "ok")]


async def test_delete_text_conversation() -> None:
    app, store = await _make_app()
    await store.add_message(thread_id="t1", role="user", text="bye")
    async with _client(app) as c:
        r = await c.delete("/api/chats/text/t1")
        assert r.status_code == 200
        r2 = await c.get("/api/chats/text/t1")
    assert r2.status_code == 404


async def test_days_filter_excludes_old() -> None:
    app, store = await _make_app()
    await store.add_message(thread_id="old", role="user", text="ancient")
    store._backdate_for_test("old", days=99)
    await store.add_message(thread_id="fresh", role="user", text="recent")
    async with _client(app) as c:
        r = await c.get("/api/chats", params={"days": 10})
    ids = [row["id"] for row in r.json()]
    assert ids == ["fresh"]
