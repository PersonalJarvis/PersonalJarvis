from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.sessions.store import SessionStore
from jarvis.ui.web import sessions_routes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "read_method"),
    [
        ("/api/sessions", "list_sessions"),
        ("/api/sessions/latest-turn", "get_latest_user_turn"),
        ("/api/sessions/s1", "get_session"),
        ("/api/sessions/s1/export?format=json", "get_session"),
    ],
)
async def test_slow_session_read_does_not_block_other_requests(
    tmp_path, monkeypatch, path, read_method,
) -> None:
    """A held synchronous store read must not own the serving asyncio loop."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    _seed_session(store)
    entered = threading.Event()
    release = threading.Event()
    original = getattr(store, read_method)

    def blocked_read(*args, **kwargs):
        entered.set()
        # The timeout is only a teardown escape for the broken implementation.
        # Assertions test ordering, not a machine-dependent latency budget.
        release.wait(timeout=2)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, read_method, blocked_read)
    app = FastAPI()
    app.include_router(sessions_routes.router)
    app.state.session_store = store

    @app.get("/sentinel")
    async def sentinel() -> dict[str, bool]:
        return {"ok": True}

    request = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            request = asyncio.create_task(client.get(path))
            assert await asyncio.to_thread(entered.wait, 2)
            assert (await client.get("/sentinel")).json() == {"ok": True}
            assert not request.done(), "the sentinel must run while the store read is still held"
            release.set()
            assert (await request).status_code == 200
    finally:
        release.set()
        if request is not None:
            await request
        store.close()


def _seed_session(store: SessionStore) -> None:
    store.upsert_session(
        session_id="s1",
        started_ms=1_000,
        wake_keyword="hey_jarvis",
        language="de",
        voice_mode="pipeline",
    )
    store.upsert_turn(turn_id="t1", session_id="s1", idx=0, started_ms=1_000)
    store.finalize_turn(
        turn_id="t1",
        ended_ms=3_000,
        user_text="What is next?",
        user_lang="en",
        jarvis_text="Final answer.",
        jarvis_lang="en",
        tier="fast",
        provider="fake",
        model="fake-model",
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        latency_total_ms=2_000,
        tool_calls=[],
    )
    store.append_event(
        session_id="s1",
        turn_id="t1",
        ts_ms=1_500,
        kind="SpeechSpoken",
        payload={"text": "Preamble first.", "language": "en", "spoken_kind": "preamble"},
    )
    store.append_event(
        session_id="s1",
        turn_id="t1",
        ts_ms=2_500,
        kind="ResponseGenerated",
        payload={"text": "Final answer.", "language": "en"},
    )
    store.finalize_session(
        session_id="s1",
        ended_ms=4_000,
        hangup_reason="hotkey",
        turn_count=1,
        total_cost_usd=0.0,
        total_tokens_in=0,
        total_tokens_out=0,
        providers_used=["fake"],
    )


def test_save_session_to_downloads_uses_events_for_plain_export(tmp_path, monkeypatch) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        _seed_session(store)
        app = FastAPI()
        app.include_router(sessions_routes.router)
        app.state.session_store = store
        monkeypatch.setattr(sessions_routes.Path, "home", staticmethod(lambda: tmp_path))

        with TestClient(app) as client:
            res = client.post("/api/sessions/s1/save?format=plain")

        assert res.status_code == 200
        saved = tmp_path / "Downloads" / res.json()["filename"]
        content = saved.read_text(encoding="utf-8")
        assert "Modus: Pipeline" in content.splitlines()[0]
        assert "Ended by: hotkey" in content
        assert "Jarvis: Preamble first." in content
        assert content.index("Jarvis: Preamble first.") < content.index(
            "Jarvis: Final answer."
        )
    finally:
        store.close()


def test_copy_exports_include_the_effective_voice_mode(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        _seed_session(store)
        app = FastAPI()
        app.include_router(sessions_routes.router)
        app.state.session_store = store

        with TestClient(app) as client:
            markdown = client.get("/api/sessions/s1/export?format=markdown")
            plain = client.get("/api/sessions/s1/export?format=plain")
            json_export = client.get("/api/sessions/s1/export?format=json")

        assert markdown.status_code == 200
        assert "- **Modus:** Pipeline" in markdown.text
        assert plain.status_code == 200
        assert "Modus: Pipeline" in plain.text.splitlines()[0]
        assert "Ended by: hotkey" in plain.text
        assert json_export.status_code == 200
        assert json_export.json()["session"]["voice_mode"] == "pipeline"
    finally:
        store.close()


def test_latest_turn_returns_newest_persisted_user_transcript(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        _seed_session(store)
        store.upsert_session(session_id="s2", started_ms=5_000, language="en")
        store.upsert_turn(
            turn_id="t2",
            session_id="s2",
            idx=0,
            started_ms=5_100,
        )
        store.finalize_turn(
            turn_id="t2",
            ended_ms=5_500,
            user_text="The newest persisted transcript.",
            user_lang="en",
            jarvis_text="Acknowledged.",
            jarvis_lang="en",
            tier="fast",
            provider="fake",
            model="fake-model",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            latency_total_ms=400,
            tool_calls=[],
        )

        app = FastAPI()
        app.include_router(sessions_routes.router)
        app.state.session_store = store
        with TestClient(app) as client:
            newest = client.get("/api/sessions/latest-turn")
            scoped = client.get(
                "/api/sessions/latest-turn", params={"session_id": "s1"}
            )

        assert newest.status_code == 200
        assert newest.json()["id"] == "t2"
        assert newest.json()["user_text"] == "The newest persisted transcript."
        assert scoped.status_code == 200
        assert scoped.json()["id"] == "t1"
    finally:
        store.close()


def test_latest_turn_returns_404_when_no_user_transcript_exists(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        app = FastAPI()
        app.include_router(sessions_routes.router)
        app.state.session_store = store
        with TestClient(app) as client:
            response = client.get("/api/sessions/latest-turn")

        assert response.status_code == 404
        assert response.json()["detail"] == "user-turn-not-found"
    finally:
        store.close()
