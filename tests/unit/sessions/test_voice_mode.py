"""Durable runtime evidence for voice-session engine mode."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ListeningStarted,
    RealtimeSessionReady,
    VoiceSessionEnded,
    VoiceSessionStarted,
)
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import _SCHEMA_PATH, SessionStore
from jarvis.ui.web import sessions_routes


def _legacy_schema_without_voice_mode() -> str:
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    return schema.replace(
        "    wake_keyword       TEXT NOT NULL DEFAULT '',\n"
        "    voice_mode         TEXT NOT NULL DEFAULT 'unknown' "
        "-- unknown|pipeline|realtime; open string for forward compatibility\n",
        "    wake_keyword       TEXT NOT NULL DEFAULT ''\n",
    )


def test_migration_backfills_runtime_evidence_and_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "legacy-sessions.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_legacy_schema_without_voice_mode())
        conn.executemany(
            "INSERT INTO voice_sessions (id, started_ms) VALUES (?, ?)",
            [
                ("ready", 100),
                ("tier", 200),
                ("pipeline", 300),
                ("fallback", 400),
                ("unknown", 500),
            ],
        )
        conn.execute(
            "INSERT INTO voice_turns "
            "(id, session_id, idx, started_ms, tier) VALUES (?, ?, ?, ?, ?)",
            ("tier-turn", "tier", 0, 200, "realtime"),
        )
        conn.executemany(
            "INSERT INTO voice_events "
            "(session_id, ts_ms, kind, payload_json) VALUES (?, ?, ?, '{}')",
            [
                ("ready", 110, "RealtimeSessionReady"),
                ("pipeline", 310, "ListeningStarted"),
                ("fallback", 410, "RealtimeSessionReady"),
                ("fallback", 420, "ListeningStarted"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    for _ in range(2):
        store = SessionStore(db_path)
        store.open()
        try:
            modes = {item.id: item.voice_mode for item in store.list_sessions()}
            assert modes == {
                "ready": "realtime",
                "tier": "realtime",
                "pipeline": "pipeline",
                "fallback": "pipeline",
                "unknown": "unknown",
            }
        finally:
            store.close()


async def _publish_session(
    bus: EventBus,
    *,
    session_id: str,
    evidence: tuple[str, ...],
) -> None:
    await bus.publish(
        VoiceSessionStarted(
            source_layer="speech.pipeline",
            session_id=session_id,
            wake_keyword="hotkey",
            language="en",
        )
    )
    for mode in evidence:
        if mode == "realtime":
            await bus.publish(
                RealtimeSessionReady(
                    source_layer="realtime.test",
                    session_id=session_id,
                    provider="test-live",
                    model="test-model",
                )
            )
        elif mode == "pipeline":
            await bus.publish(ListeningStarted(source_layer="speech"))
    await bus.publish(
        VoiceSessionEnded(
            source_layer="speech.pipeline",
            session_id=session_id,
            hangup_reason="turn_complete",
        )
    )


@pytest.mark.asyncio
async def test_recorder_persists_pipeline_fallback_and_unknown_modes(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)

        await _publish_session(bus, session_id="pipeline", evidence=("pipeline",))
        await _publish_session(
            bus,
            session_id="fallback",
            evidence=("realtime", "pipeline"),
        )
        await _publish_session(bus, session_id="unknown", evidence=())

        pipeline = store.get_session("pipeline")
        fallback = store.get_session("fallback")
        unknown = store.get_session("unknown")
        assert pipeline is not None and pipeline.voice_mode == "pipeline"
        assert fallback is not None and fallback.voice_mode == "pipeline"
        assert unknown is not None and unknown.voice_mode == "unknown"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_realtime_ready_for_another_session_is_not_evidence(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="current",
                language="en",
            )
        )
        await bus.publish(
            RealtimeSessionReady(
                source_layer="realtime.test",
                session_id="different",
                provider="test-live",
                model="test-model",
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="current",
                hangup_reason="turn_complete",
            )
        )

        session = store.get_session("current")
        assert session is not None
        assert session.voice_mode == "unknown"
        assert all(
            event.kind != "RealtimeSessionReady"
            for event in store.get_events("current")
        )
    finally:
        store.close()


def test_list_and_detail_routes_serialize_open_voice_mode(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        store.upsert_session(
            session_id="future-mode",
            started_ms=1_000,
            language="en",
            voice_mode="future-duplex-v2",
        )
        app = FastAPI()
        app.include_router(sessions_routes.router)
        app.state.session_store = store

        with TestClient(app) as client:
            listed = client.get("/api/sessions")
            detail = client.get("/api/sessions/future-mode")

        assert listed.status_code == 200
        assert listed.json()[0]["voice_mode"] == "future-duplex-v2"
        assert detail.status_code == 200
        assert detail.json()["session"]["voice_mode"] == "future-duplex-v2"
    finally:
        store.close()
