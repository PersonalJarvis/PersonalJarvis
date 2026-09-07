"""Termination metadata survives generic event, SQLite and export boundaries."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.events import VoiceSessionEnded, VoiceSessionStarted
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore
from jarvis.ui.web.schema import event_to_ws_envelope
from jarvis.ui.web.sessions_routes import router


@pytest.mark.parametrize(
    "with_detail", [False, True], ids=["legacy-realtime", "pipeline-diagnostics"],
)
@pytest.mark.asyncio
async def test_termination_event_persists_and_exports_without_a_session_schema_change(
    tmp_path, with_detail: bool,
) -> None:
    snapshot = {
        "producer": "speech.pipeline.input_stream_error",
        "hangup_pattern_matched": False,
        "end_call_signal": False,
        "single_turn_mode": False,
        "idle_timeout_s": 0,
        "assistant_work_active": False,
        "input_error_type": "RuntimeError",
        "input_replay_overrun": True,
    }
    kwargs = {"detail": json.dumps(snapshot)} if with_detail else {}
    event = VoiceSessionEnded(
        session_id="termination-contract", hangup_reason="error",
        source_layer=(
            "speech.pipeline.input_stream_error" if with_detail else "realtime.session.end"
        ),
        timestamp_ns=2000_000_000, **kwargs,
    )
    envelope = json.loads(json.dumps(event_to_ws_envelope(event)))
    assert envelope["payload"]["hangup_reason"] == "error"
    assert envelope["payload"]["detail"] == (json.dumps(snapshot) if with_detail else "")
    assert envelope["source_layer"] == event.source_layer

    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(VoiceSessionStarted(
            session_id=event.session_id, timestamp_ns=1000_000_000,
        ))
        await bus.publish(event)
        row = store.get_session(event.session_id)
        assert row is not None and row.hangup_reason == "error"
        recorded = store.get_events(event.session_id)[-1]
        assert recorded.payload["source_layer"] == event.source_layer
        assert recorded.payload["detail"] == event.detail

        app = FastAPI()
        app.state.session_store = store
        app.include_router(router)
        with TestClient(app) as client:
            for export_format in ("plain", "markdown", "json"):
                response = client.get(
                    f"/api/sessions/{event.session_id}/export?format={export_format}"
                )
                assert response.status_code == 200
                assert event.source_layer in response.text
                assert "error" in response.text
                if with_detail and export_format != "json":
                    assert "hangup_pattern_matched: false" in response.text
                    assert "idle_timeout_s: 0" in response.text
                    assert "input_error_type: RuntimeError" in response.text
                    assert "input_replay_overrun: true" in response.text
                    assert "assistant_work_active: false" in response.text
                    assert "computer_use_active: not recorded" in response.text
                if not with_detail and export_format != "json":
                    assert "hangup_pattern_matched: false" not in response.text
    finally:
        store.close()
