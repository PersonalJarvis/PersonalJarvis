"""Visibility rules for empty voice-session attempts."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.sessions.store import SessionStore
from jarvis.ui.web import sessions_routes


def _finalize_session(
    store: SessionStore,
    *,
    session_id: str,
    started_ms: int,
    user_text: str = "",
    assistant_text: str = "",
) -> None:
    store.upsert_session(
        session_id=session_id,
        started_ms=started_ms,
        language="en",
        voice_mode="realtime",
    )
    turn_count = 0
    if user_text or assistant_text:
        turn_id = f"{session_id}-turn"
        store.upsert_turn(
            turn_id=turn_id,
            session_id=session_id,
            idx=0,
            started_ms=started_ms,
        )
        store.finalize_turn(
            turn_id=turn_id,
            ended_ms=started_ms + 500,
            user_text=user_text,
            user_lang="en" if user_text else "",
            jarvis_text=assistant_text,
            jarvis_lang="en" if assistant_text else "",
            tier="realtime",
            provider="test-live",
            model="test-model",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            latency_total_ms=500,
            tool_calls=[],
        )
        turn_count = 1
    store.finalize_session(
        session_id=session_id,
        ended_ms=started_ms + 1_000,
        hangup_reason="hotkey",
        turn_count=turn_count,
        total_cost_usd=0.0,
        total_tokens_in=0,
        total_tokens_out=0,
        providers_used=[],
    )


def test_transcript_filter_keeps_content_and_open_sessions(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        _finalize_session(
            store,
            session_id="meaningful",
            started_ms=1_000,
            user_text="Hello",
        )
        _finalize_session(
            store,
            session_id="assistant-only",
            started_ms=2_000,
            assistant_text="Microphone access is unavailable.",
        )
        _finalize_session(store, session_id="empty", started_ms=4_000)
        store.upsert_session(
            session_id="running",
            started_ms=3_000,
            language="en",
            voice_mode="unknown",
        )

        visible = store.list_sessions(limit=10, include_empty=False)
        all_attempts = store.list_sessions(limit=10, include_empty=True)

        assert [session.id for session in visible] == [
            "running",
            "assistant-only",
            "meaningful",
        ]
        assert [session.id for session in all_attempts] == [
            "empty",
            "running",
            "assistant-only",
            "meaningful",
        ]
    finally:
        store.close()


def test_empty_attempts_do_not_consume_the_visible_limit(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        _finalize_session(
            store,
            session_id="meaningful",
            started_ms=1_000,
            user_text="Hello",
        )
        for index in range(2, 8):
            _finalize_session(
                store,
                session_id=f"empty-{index}",
                started_ms=index * 1_000,
            )

        visible = store.list_sessions(limit=1, include_empty=False)

        assert [session.id for session in visible] == ["meaningful"]
    finally:
        store.close()


def test_sessions_api_hides_empty_attempts_but_keeps_diagnostics_access(
    tmp_path,
) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        _finalize_session(
            store,
            session_id="meaningful",
            started_ms=1_000,
            user_text="Hello",
        )
        _finalize_session(store, session_id="empty", started_ms=2_000)
        app = FastAPI()
        app.include_router(sessions_routes.router)
        app.state.session_store = store

        with TestClient(app) as client:
            visible = client.get("/api/sessions")
            all_attempts = client.get(
                "/api/sessions",
                params={"include_empty": True},
            )
            empty_detail = client.get("/api/sessions/empty")

        assert visible.status_code == 200
        assert [session["id"] for session in visible.json()] == ["meaningful"]
        assert all_attempts.status_code == 200
        assert [session["id"] for session in all_attempts.json()] == [
            "empty",
            "meaningful",
        ]
        assert empty_detail.status_code == 200
        assert empty_detail.json()["turns"] == []
    finally:
        store.close()
