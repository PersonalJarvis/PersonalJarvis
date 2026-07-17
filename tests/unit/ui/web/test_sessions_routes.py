from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.sessions.store import SessionStore
from jarvis.ui.web import sessions_routes


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
