"""REST-route tests for the telephony contract (section 4).

These run against a bare FastAPI app with only the telephony router mounted, an
injected fake config, and monkeypatched secrets/config-writer so nothing
touches the real ``jarvis.toml`` or Credential Manager.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.telephony_routes as routes
from jarvis.core.config import JarvisConfig, TwilioConfig
from jarvis.telephony.status import TelephonyManager
from jarvis.ui.web.telephony_routes import router as telephony_router


@pytest.fixture
def fake_cfg() -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.integrations.twilio = TwilioConfig(
        enabled=True,
        account_sid="AC0123456789abcdef0123456789abcdef",
        phone_number="+49301234567",
        public_base_url="https://jarvis.example.com",
        language_code="de-DE",
        max_call_seconds=600,
    )
    return cfg


@pytest.fixture
def app(fake_cfg: JarvisConfig) -> FastAPI:
    application = FastAPI()
    application.include_router(telephony_router)
    application.state.config = fake_cfg
    application.state.telephony_manager = TelephonyManager()
    application.state.bus = None
    return application


@pytest.fixture
def secret_store(monkeypatch):
    store: dict[str, str] = {"twilio_auth_token": "supersecrettoken"}

    def fake_get(key, env_fallback=None):
        return store.get(key)

    def fake_set(key, value):
        store[key] = value
        return True

    monkeypatch.setattr(routes, "get_secret", fake_get)
    monkeypatch.setattr(routes, "set_secret", fake_set)
    return store


def test_status_shape(app, secret_store):
    with TestClient(app) as client:
        r = client.get("/api/telephony/status")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "available",
        "configured",
        "enabled",
        "account_sid_masked",
        "phone_number",
        "public_base_url",
        "webhook_url",
        "auth_token_set",
        "twilio_reachable",
        "twilio_error",
        "tts_provider",
        "tts_voice",
        "active_calls",
        "max_call_seconds",
    ):
        assert key in body, f"missing {key}"
    assert body["configured"] is True
    assert body["webhook_url"] == "https://jarvis.example.com/api/telephony/voice"
    assert body["account_sid_masked"].startswith("AC")
    assert "0123456789abcdef0123456789abcdef" not in body["account_sid_masked"]
    assert body["tts_voice"] == "Charon"
    assert body["auth_token_set"] is True


def test_status_not_configured_without_token(app, monkeypatch):
    monkeypatch.setattr(routes, "get_secret", lambda *a, **k: None)
    with TestClient(app) as client:
        r = client.get("/api/telephony/status")
    assert r.status_code == 200
    assert r.json()["configured"] is False
    assert r.json()["auth_token_set"] is False


def test_config_get_returns_non_secret_fields(app, secret_store):
    with TestClient(app) as client:
        r = client.get("/api/telephony/config")
    assert r.status_code == 200
    body = r.json()
    assert body["phone_number"] == "+49301234567"
    assert body["auth_token_set"] is True
    assert "auth_token" not in body  # never leak the secret value


def test_config_post_validates_e164(app, secret_store, monkeypatch):
    monkeypatch.setattr(routes, "cfg_mod", routes.cfg_mod)
    with TestClient(app) as client:
        r = client.post("/api/telephony/config", json={"phone_number": "0301234"})
    assert r.status_code == 422
    assert "E.164" in r.json()["error"]


def test_config_post_validates_url(app, secret_store):
    with TestClient(app) as client:
        r = client.post("/api/telephony/config", json={"public_base_url": "not a url"})
    assert r.status_code == 422


def test_config_post_persists_via_writer(app, secret_store, fake_cfg, monkeypatch):
    written: dict[str, object] = {}

    def fake_writer(values, **kw):
        written.update(values)
        # Reflect into the fake config so the re-read returns the new value.
        for k, v in values.items():
            setattr(fake_cfg.integrations.twilio, k, v)

    monkeypatch.setattr("jarvis.core.config_writer.set_telephony_config", fake_writer)
    monkeypatch.setattr(routes.cfg_mod, "load_config", lambda *a, **k: fake_cfg)

    with TestClient(app) as client:
        r = client.post(
            "/api/telephony/config",
            json={"greeting": "Hallo!", "max_call_seconds": 300},
        )
    assert r.status_code == 200
    assert written["greeting"] == "Hallo!"
    assert written["max_call_seconds"] == 300
    assert r.json()["greeting"] == "Hallo!"


def test_credentials_stores_token_and_sid(app, secret_store, fake_cfg, monkeypatch):
    written: dict[str, object] = {}
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_telephony_config",
        lambda values, **kw: written.update(values),
    )
    monkeypatch.setattr(routes.cfg_mod, "load_config", lambda *a, **k: fake_cfg)

    with TestClient(app) as client:
        r = client.post(
            "/api/telephony/credentials",
            json={
                "account_sid": "AC" + "f" * 32,
                "auth_token": "newtoken",
            },
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert secret_store["twilio_auth_token"] == "newtoken"
    assert written["account_sid"] == "AC" + "f" * 32


def test_credentials_rejects_bad_sid(app, secret_store):
    with TestClient(app) as client:
        r = client.post("/api/telephony/credentials", json={"account_sid": "XX123"})
    assert r.status_code == 422


def test_calls_endpoint_returns_ring_buffer(app, secret_store):
    from jarvis.telephony.status import CallRecord

    mgr: TelephonyManager = app.state.telephony_manager
    mgr.record_call(
        CallRecord(call_sid="CA1", from_number="+49", to_number="+1", status="completed", turns=2)
    )
    with TestClient(app) as client:
        r = client.get("/api/telephony/calls?limit=10")
    assert r.status_code == 200
    calls = r.json()["calls"]
    assert len(calls) == 1
    assert calls[0]["call_sid"] == "CA1"
    assert calls[0]["from"] == "+49"
    assert calls[0]["status"] == "completed"


def test_scripts_endpoint_lists_helpers(app, secret_store):
    with TestClient(app) as client:
        r = client.get("/api/telephony/scripts")
    assert r.status_code == 200
    scripts = r.json()["scripts"]
    names = {s["name"] for s in scripts}
    assert "Public tunnel (dev)" in names
    assert "Provision number" in names
    for s in scripts:
        assert s["command"]
        assert s["description"]


def test_voice_webhook_rejects_when_disabled(app, secret_store, fake_cfg):
    fake_cfg.integrations.twilio.enabled = False
    with TestClient(app) as client:
        r = client.post("/api/telephony/voice", data={"CallSid": "CA1"})
    assert r.status_code == 200
    assert "text/xml" in r.headers["content-type"]
    assert "<Hangup" in r.text


def test_voice_webhook_returns_connect_stream_with_valid_signature(app, secret_store, fake_cfg):
    from twilio.request_validator import RequestValidator

    url = "https://jarvis.example.com/api/telephony/voice"
    params = {"CallSid": "CA42", "From": "+4930", "To": "+4940"}
    sig = RequestValidator(secret_store["twilio_auth_token"]).compute_signature(url, params)
    with TestClient(app) as client:
        r = client.post(
            "/api/telephony/voice",
            data=params,
            headers={"X-Twilio-Signature": sig},
        )
    assert r.status_code == 200
    assert "<Connect>" in r.text
    assert "<Stream" in r.text
    assert "wss://jarvis.example.com/api/telephony/media" in r.text
    # A per-call secret was minted and registered.
    pending = app.state.telephony_manager.peek_pending("CA42")
    assert pending is not None


def test_voice_webhook_rejects_bad_signature(app, secret_store):
    with TestClient(app) as client:
        r = client.post(
            "/api/telephony/voice",
            data={"CallSid": "CA1"},
            headers={"X-Twilio-Signature": "garbage"},
        )
    assert r.status_code == 403
    assert "<Hangup" in r.text


def test_voice_webhook_skips_signature_with_test_flag(app, secret_store):
    app.state.telephony_skip_signature = True
    with TestClient(app) as client:
        r = client.post("/api/telephony/voice", data={"CallSid": "CA1"})
    assert r.status_code == 200
    assert "<Connect>" in r.text


# ---------------------------------------------------------------------------
# Chunk C — outbound calling (POST /outbound + the /voice outbound branch)
# ---------------------------------------------------------------------------


def test_outbound_route_places_call(app, secret_store, fake_cfg, monkeypatch):
    captured: dict = {}

    def fake_place_call(**kwargs):
        captured.update(kwargs)
        return "CA_OUT_42"

    monkeypatch.setattr("jarvis.telephony.outbound.place_call", fake_place_call)
    with TestClient(app) as client:
        r = client.post(
            "/api/telephony/outbound",
            json={"to": "+4915112345678", "opening": "Hallo Christoph."},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["call_sid"] == "CA_OUT_42"
    # The route resolved config + secret and dialed a raw number.
    assert captured["to"] == "+4915112345678"
    assert captured["opening"] == "Hallo Christoph."
    assert captured["from_number"] == "+49301234567"
    assert captured["public_base_url"] == "https://jarvis.example.com"
    assert captured["account_sid"] == "AC0123456789abcdef0123456789abcdef"
    assert captured["auth_token"] == "supersecrettoken"


def test_outbound_route_rejects_non_e164(app, secret_store):
    with TestClient(app) as client:
        r = client.post("/api/telephony/outbound", json={"to": "0151 123"})
    assert r.status_code == 422
    assert "E.164" in r.json()["error"]


def test_outbound_route_409_when_disabled(app, secret_store, fake_cfg):
    fake_cfg.integrations.twilio.enabled = False
    with TestClient(app) as client:
        r = client.post("/api/telephony/outbound", json={"to": "+4915112345678"})
    assert r.status_code == 409
    assert "error" in r.json()


def test_outbound_route_409_when_no_token(app, monkeypatch):
    monkeypatch.setattr(routes, "get_secret", lambda *a, **k: None)
    with TestClient(app) as client:
        r = client.post("/api/telephony/outbound", json={"to": "+4915112345678"})
    assert r.status_code == 409


def test_outbound_route_graceful_when_twilio_missing(app, secret_store, monkeypatch):
    monkeypatch.setattr(routes, "is_available", lambda: False)
    with TestClient(app) as client:
        r = client.post("/api/telephony/outbound", json={"to": "+4915112345678"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "twilio" in body["error"].lower()


def test_outbound_route_surfaces_provision_error(app, secret_store, monkeypatch):
    from jarvis.telephony.provisioning import TelephonyProvisionError

    def boom(**kwargs):
        raise TelephonyProvisionError("Number purchase failed: nope")

    monkeypatch.setattr("jarvis.telephony.outbound.place_call", boom)
    with TestClient(app) as client:
        r = client.post("/api/telephony/outbound", json={"to": "+4915112345678", "opening": "Hi"})
    assert r.status_code == 409
    assert r.json()["ok"] is False
    assert "nope" in r.json()["error"]


def test_voice_webhook_outbound_branch_returns_opening(app, secret_store, fake_cfg):
    from twilio.request_validator import RequestValidator

    query = "opening=Servus"
    url = f"https://jarvis.example.com/api/telephony/voice?{query}"
    params = {
        "CallSid": "CAOUT9",
        "From": "+49301234567",
        "To": "+4915112345678",
        "Direction": "outbound-api",
    }
    sig = RequestValidator(secret_store["twilio_auth_token"]).compute_signature(url, params)
    with TestClient(app) as client:
        r = client.post(
            f"/api/telephony/voice?{query}",
            data=params,
            headers={"X-Twilio-Signature": sig},
        )
    assert r.status_code == 200
    assert "<Connect>" in r.text
    assert 'name="direction" value="outbound"' in r.text
    assert "Servus" in r.text
    assert "wss://jarvis.example.com/api/telephony/media" in r.text
    # A per-call secret was minted and registered for the outbound call too.
    assert app.state.telephony_manager.peek_pending("CAOUT9") is not None
