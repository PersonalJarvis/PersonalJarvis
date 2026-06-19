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
        assert "Jarvis: Preamble first." in content
        assert content.index("Jarvis: Preamble first.") < content.index(
            "Jarvis: Final answer."
        )
    finally:
        store.close()
