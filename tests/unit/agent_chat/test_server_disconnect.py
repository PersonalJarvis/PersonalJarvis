"""All runtime dispatches retain approvals, effects and results across UI disconnects."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat import service as service_module
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.ui.web.agent_chat_routes import router
from tests.fakes.fake_disconnect_runner import ApprovalRunner


@pytest.mark.parametrize(("runtime", "runner_name"), [
    ("", "brain"), ("hermes", "hermes-cli"), ("openclaw", "openclaw-cli"),
])
def test_client_disconnect_reconnect_and_completed_restart(
    tmp_path, monkeypatch, runtime, runner_name,
):
    database = tmp_path / "chat.db"
    store = AgentChatStore(database)
    service = AgentChatService(store)
    session = store.create_session(
        session_id="society:server-test", provider="openai", model="fake-model", effort="",
        surface="society", runtime=runtime, permission_mode="ask", cwd=str(tmp_path),
    )

    async def bind(*args, **kwargs):
        return session

    fake = ApprovalRunner()
    monkeypatch.setattr(service, "bind_society_session", bind)
    monkeypatch.setattr(service_module, "run_cli_turn", fake)

    async def brain(handle, prompt, **kwargs):
        return await fake(handle, prompt, "brain")

    monkeypatch.setattr(service_module, "run_brain_turn", brain)
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = service
    path = f"/api/agent-chat/sessions/{session.session_id}"
    with TestClient(app) as client:
        with client.websocket_connect(path + "/ws") as ws:
            ws.receive_json()
            response = client.post(path + "/messages", json={"text": "Write one result"})
            assert response.status_code == 202, response.text
            while True:
                frame = ws.receive_json()
                event = frame.get("event", {})
                if event.get("kind") == "approval_required":
                    approval = event["payload"]["approval_id"]
                    seq = event["seq"]
                    break
                assert event.get("kind") != "turn_finished", frame

        # No UI subscriber remains. Disconnecting must not grant or cancel anything.
        assert service.is_running(session.session_id)
        assert fake.effects == []
        assert fake.starts == [runner_name]
        with client.websocket_connect(path + f"/ws?after={seq}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["session"]["running"]
            assert approval in snapshot["session"]["pending_approvals"]
            assert all(event["seq"] > seq for event in snapshot["events"])
            response = client.post(path + f"/approvals/{approval}", json={"decision": "allow"})
            assert response.status_code == 200, response.text
            while True:
                event = ws.receive_json().get("event", {})
                if event.get("kind") == "turn_finished":
                    assert event["payload"]["status"] == "done"
                    break
        with client.websocket_connect(path + "/ws") as ws:
            events = ws.receive_json()["events"]
            assert sum(e["kind"] == "assistant_text" for e in events) == 1
            assert sum(e["kind"] == "turn_finished" for e in events) == 1
        assert len(fake.effects) == 1
        assert fake.starts == [runner_name]
    store.close()
    reopened = AgentChatStore(database)
    try:
        events = reopened.list_events(session.session_id)
        assert sum(e["kind"] == "tool_result" for e in events) == 1
        assert sum(e["kind"] == "turn_finished" for e in events) == 1
    finally:
        reopened.close()
