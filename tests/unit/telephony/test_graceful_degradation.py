"""Graceful-degradation tests (AD-T8): twilio SDK absent must not crash routes."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.telephony_routes as routes
from jarvis.core.config import JarvisConfig, TwilioConfig
from jarvis.telephony.status import TelephonyManager
from jarvis.ui.web.telephony_routes import router as telephony_router


@pytest.fixture
def app_unavailable(monkeypatch) -> FastAPI:
    """App where the twilio SDK is reported unavailable."""
    monkeypatch.setattr(routes, "is_available", lambda: False)
    monkeypatch.setattr(routes, "get_secret", lambda *a, **k: None)
    cfg = JarvisConfig()
    cfg.integrations.twilio = TwilioConfig(enabled=False)
    application = FastAPI()
    application.include_router(telephony_router)
    application.state.config = cfg
    application.state.telephony_manager = TelephonyManager()
    return application


def test_status_returns_200_when_unavailable(app_unavailable):
    with TestClient(app_unavailable) as client:
        r = client.get("/api/telephony/status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["configured"] is False


def test_test_endpoint_returns_clear_message_when_unavailable(app_unavailable):
    with TestClient(app_unavailable) as client:
        r = client.post("/api/telephony/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["reachable"] is False
    assert "twilio" in body["error"].lower()


def test_config_and_calls_endpoints_still_serve_when_unavailable(app_unavailable):
    with TestClient(app_unavailable) as client:
        assert client.get("/api/telephony/config").status_code == 200
        assert client.get("/api/telephony/calls").status_code == 200
        assert client.get("/api/telephony/scripts").status_code == 200


def test_voice_webhook_rejects_gracefully_when_disabled(app_unavailable):
    with TestClient(app_unavailable) as client:
        r = client.post("/api/telephony/voice", data={"CallSid": "CA1"})
    # disabled -> 200 with hangup TwiML, no 500
    assert r.status_code == 200
    assert "<Hangup" in r.text


def test_is_available_reflects_import_state():
    # In this env twilio IS installed, so the real check should be True.
    from jarvis.telephony import is_available as real_is_available

    assert real_is_available() is True
