"""Opening a manual chat reconciles restart-orphaned queue notices."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.ui.web.agent_chat_routes import router


@pytest.mark.parametrize("transport", ["http", "websocket"])
def test_jarvis_restart_marks_old_waiting_message_failed_before_snapshot(transport):
    store = AgentChatStore(":memory:")
    session = store.create_session(
        provider="fakeprov", model="m", effort="low", cwd=".", surface="jarvis",
    )
    sid = session.session_id
    store.append_event(sid, make_event("notice", {
        "kind": "message_queued", "queue_id": "orphaned", "text": "Please check this too.",
    }))
    # A tool-rich running turn used to push the queued notice outside tail=400.
    for index in range(405):
        store.append_event(sid, make_event("tool_result", {
            "turn_id": "old-turn", "call_id": f"tool-{index}", "output": "Done.",
        }))
    fresh = AgentChatService(store)
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = fresh
    try:
        with TestClient(app) as client:
            if transport == "http":
                response = client.get(f"/api/agent-chat/sessions/{sid}")
                assert response.status_code == 200
                events = response.json()["events"]
            else:
                with client.websocket_connect(f"/api/agent-chat/sessions/{sid}/ws") as ws:
                    snapshot = ws.receive_json()
                    assert snapshot["type"] == "snapshot"
                    events = snapshot["events"]
            closed = [
                event["payload"] for event in events
                if event["kind"] == "notice"
                and event["payload"].get("kind") == "message_dequeued"
            ]
            assert closed == [{
                "kind": "message_dequeued", "queue_id": "orphaned", "status": "failed",
                "text": "Please check this too.",
            }]
            client.get(f"/api/agent-chat/sessions/{sid}")
            assert sum(
                event["payload"].get("kind") == "message_dequeued"
                for event in store.queue_notice_events(sid)
            ) == 1
    finally:
        store.close()
