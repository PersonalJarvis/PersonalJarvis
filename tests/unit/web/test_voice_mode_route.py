"""Task 7 — the browser-voice connect gate, inverted to default OFF.

``_browser_voice_enabled`` now serves the /ws/audio socket only when the user
has explicitly opted into a voice surface: realtime mode ([voice].mode ==
"realtime") or the classic bridge ([browser_voice].enabled == True). A missing
[browser_voice] section is False, not True (the old default-ON contract).

NOTE: a later task (T8) appends settings-route handler tests to this same
file — keep additions here additive and self-contained.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.browser_voice.route import _browser_voice_enabled
from jarvis.ui.web.settings_routes import router


def test_gate_default_off_when_pipeline_and_no_browser_voice():
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="pipeline"))
    assert _browser_voice_enabled(cfg) is False


def test_gate_on_for_realtime_mode():
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="realtime"))
    assert _browser_voice_enabled(cfg) is True


def test_gate_on_for_explicit_classic_browser_voice():
    cfg = SimpleNamespace(
        voice=SimpleNamespace(mode="pipeline"), browser_voice=SimpleNamespace(enabled=True)
    )
    assert _browser_voice_enabled(cfg) is True


# ---------------------------------------------------------------------------
# Task 8 — GET/PUT /api/settings/voice-mode route handlers.
# ---------------------------------------------------------------------------


def _app(mode="pipeline", key="sk-x", monkeypatch=None):
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(voice=SimpleNamespace(mode=mode))
    return app


def test_get_voice_mode(monkeypatch):
    import jarvis.realtime.factory as rf

    def openai_only(candidates):
        keys = {c[0] for c in candidates}
        return "sk-x" if "openai_api_key" in keys else None

    monkeypatch.setattr(rf, "get_secret_any", openai_only)
    client = TestClient(_app(mode="realtime"))
    r = client.get("/api/settings/voice-mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "realtime"
    assert body["realtime_available"] is True
    assert body["active_provider"] == "openai-realtime"


def test_get_voice_mode_cross_family_gemini_only(monkeypatch):
    """Feature A2: realtime_available must NOT be OpenAI-only — a user with
    only a Gemini key gets realtime_available=true, active_provider=gemini-live."""
    import jarvis.realtime.factory as rf

    def only_gemini(candidates: tuple[tuple[str, str | None], ...]) -> str | None:
        keys = {c[0] for c in candidates}
        return "sk-x" if "gemini_api_key" in keys else None

    monkeypatch.setattr(rf, "get_secret_any", only_gemini)
    client = TestClient(_app(mode="pipeline"))
    r = client.get("/api/settings/voice-mode")
    assert r.status_code == 200
    body = r.json()
    assert body["realtime_available"] is True
    assert body["active_provider"] == "gemini-live"


def test_get_voice_mode_no_realtime_key_anywhere(monkeypatch):
    import jarvis.realtime.factory as rf
    monkeypatch.setattr(rf, "get_secret_any", lambda _candidates: None)
    client = TestClient(_app(mode="pipeline"))
    r = client.get("/api/settings/voice-mode")
    body = r.json()
    assert body["realtime_available"] is False
    assert body["active_provider"] is None


def test_put_voice_mode_invalid_is_400():
    client = TestClient(_app())
    r = client.put("/api/settings/voice-mode", json={"mode": "bogus", "persist": False})
    assert r.status_code == 400


def test_put_voice_mode_realtime_without_key_is_400(monkeypatch):
    """A3: PUT-guard rejects selecting realtime when no family has a key —
    prevents pinning the boot default to an unreachable engine."""
    import jarvis.realtime.factory as rf
    monkeypatch.setattr(rf, "get_secret_any", lambda _candidates: None)
    client = TestClient(_app())
    r = client.put("/api/settings/voice-mode", json={"mode": "realtime", "persist": False})
    assert r.status_code == 400


def test_put_voice_mode_updates_live_and_persists(monkeypatch):
    import jarvis.realtime.factory as rf
    persisted = {"called": False}
    monkeypatch.setattr(rf, "get_secret_any", lambda _candidates: "sk-x")

    def fake_set(mode, **kw):
        persisted["called"] = True

    import jarvis.core.config_writer as cw
    monkeypatch.setattr(cw, "set_voice_mode", fake_set)
    app = _app()
    client = TestClient(app)
    r = client.put("/api/settings/voice-mode", json={"mode": "realtime", "persist": True})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "mode": "realtime", "persisted": True}
    assert app.state.config.voice.mode == "realtime"
    assert persisted["called"] is True
