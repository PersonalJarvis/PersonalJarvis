"""Rejected media sockets never acquire call-registry authority."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.telephony.status import CallRecord, TelephonyManager
from jarvis.ui.web import telephony_routes

pytestmark = pytest.mark.no_auto_web_auth


def make_app():
    app = FastAPI()
    app.include_router(telephony_routes.router)
    app.state.config = SimpleNamespace(integrations=SimpleNamespace(twilio=None))
    app.state.telephony_manager = TelephonyManager()
    return app


def start(call_sid, secret):
    return {
        "event": "start",
        "start": {
            "callSid": call_sid,
            "streamSid": "stream",
            "customParameters": {"secret": secret},
        },
    }


@pytest.mark.parametrize("requested", ["known-active", "known-finished", "unknown"])
def test_invalid_secret_leaves_all_call_state_unchanged(requested, monkeypatch):
    app = make_app()
    mgr = app.state.telephony_manager
    active = object()
    mgr.register_active("known-active", active)
    mgr.register_pending("known-active", "valid-secret")
    pending = mgr.peek_pending("known-active")
    mgr.record_call(CallRecord(call_sid="known-finished", status="completed", turns=3))
    before = mgr.recent_calls()

    def never_build(**kwargs):
        pytest.fail("unauthenticated socket reached session factory")

    monkeypatch.setattr(telephony_routes, "_build_session", never_build)
    with TestClient(app) as client:
        with client.websocket_connect("/api/telephony/media") as ws:
            ws.send_json(start(requested, "invalid"))
            assert ws.receive()["code"] == 1008
    assert mgr.active_session("known-active") is active
    assert mgr.peek_pending("known-active") is pending
    assert mgr.recent_calls() == before


@pytest.mark.parametrize("second_start", [False, True])
def test_valid_socket_records_its_identity_once(monkeypatch, second_start):
    app = make_app()
    mgr = app.state.telephony_manager
    mgr.register_pending("valid-call", "valid-secret", from_number="from", to_number="to")
    mgr.register_pending("other-call", "other-secret")
    ended = []

    class Session:
        ended = False
        status = "completed"
        duration_s = 1
        turns = 2
        from_number = "from"
        to_number = "to"

        async def end(self, **kwargs):
            self.ended = True
            ended.append(1)

        async def speak_intro(self):
            return None

    monkeypatch.setattr(telephony_routes, "_build_session", lambda **kwargs: Session())
    with TestClient(app) as client:
        with client.websocket_connect("/api/telephony/media") as ws:
            ws.send_json(start("valid-call", "valid-secret"))
            ws.send_json(start("other-call", "other-secret") if second_start else {"event": "stop"})
            assert ws.receive()["type"] == "websocket.close"
    assert ended == [1]
    assert mgr.active_calls == 0
    assert mgr.peek_pending("other-call") is not None
    assert [record["call_sid"] for record in mgr.recent_calls()] == ["valid-call"]
