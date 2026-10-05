"""The browser-voice connect gate, then the voice-mode settings routes.

``browser_voice_enabled`` decides whether /ws/audio serves a browser-held
call. Realtime mode always serves it (the socket carries the realtime call and
its classic fallback). Pipeline mode serves the classic STT -> brain -> TTS
bridge while ``[browser_voice].enabled`` is on — the default. Issue #399: the
section used to be missing from ``JarvisConfig``, so ``load_config`` dropped
it and pipeline mode could never serve the bridge.

NOTE: the settings-route handler tests further down share this file — keep
additions here additive and self-contained.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.browser_voice.route import browser_voice_enabled
from jarvis.core import config as cfg_mod
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.settings_routes import router


def test_gate_on_by_default_in_pipeline_mode():
    cfg = JarvisConfig.model_validate({"voice": {"mode": "pipeline"}})
    assert cfg.browser_voice.enabled is True
    assert browser_voice_enabled(cfg) is True


def test_gate_off_when_the_classic_bridge_is_switched_off():
    cfg = JarvisConfig.model_validate(
        {"voice": {"mode": "pipeline"}, "browser_voice": {"enabled": False}}
    )
    assert browser_voice_enabled(cfg) is False


def test_gate_on_for_realtime_mode_even_with_the_classic_bridge_off():
    # The desktop hands realtime media to the WebView over the same socket;
    # the classic switch must never take that away.
    cfg = JarvisConfig.model_validate(
        {"voice": {"mode": "realtime"}, "browser_voice": {"enabled": False}}
    )
    assert browser_voice_enabled(cfg) is True


def test_gate_treats_a_config_without_the_section_like_the_default():
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="pipeline"))
    assert browser_voice_enabled(cfg) is True


def test_load_config_keeps_the_browser_voice_table(tmp_path: Path):
    # The exact reproduction from issue #399: the table used to be dropped
    # during validation, so ``load_config().browser_voice`` raised.
    path = tmp_path / "jarvis.toml"
    path.write_text(
        '[voice]\nmode = "pipeline"\n\n[browser_voice]\nenabled = false\n',
        encoding="utf-8",
    )
    loaded = cfg_mod.load_config(path)
    assert loaded.browser_voice.enabled is False


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
    assert body["requires_webrtc_offer"] is False
    assert body["transport_offer_ready"] is None
    assert body["transport_offer_detail"] is None
    assert body["active_provider"] == "openai-live"
    # Sidebar display fields: registry label + the catalog-default model (no
    # pin configured in this app fixture).
    assert body["active_provider_label"] == "OpenAI GPT-Live"
    assert body["active_model"] == "gpt-live-1"
    assert body["active_model_label"] == "GPT-Live 1"
    assert body["session_active"] is False
    assert body["active_session_mode"] is None


def test_get_voice_mode_reports_browser_offer_capability(monkeypatch):
    import jarvis.realtime.factory as rf
    from jarvis.ui.web import settings_routes

    # An offer-requiring transport without browser audio. Fixed here, because
    # without a pin the answer follows the host's real credentials.
    monkeypatch.setattr(rf, "realtime_browser_audio", lambda _cfg: False)
    monkeypatch.setattr(
        settings_routes,
        "_realtime_available_provider",
        lambda _cfg: "openai-realtime",
    )
    monkeypatch.setattr(
        settings_routes,
        "_realtime_requires_webrtc_offer",
        lambda _cfg: True,
    )
    async def _offer_ready(_required: bool) -> bool:
        return True

    monkeypatch.setattr(
        settings_routes,
        "_realtime_transport_offer_ready",
        _offer_ready,
    )


    body = TestClient(_app(mode="realtime")).get("/api/settings/voice-mode").json()

    assert body["active_provider"] == "openai-realtime"
    assert body["active_model"] == "gpt-realtime"
    assert body["active_model_label"] == "GPT Realtime (default)"
    assert body["requires_webrtc_offer"] is True
    assert body["transport_offer_ready"] is True
    assert body["transport_offer_detail"] == "Embedded desktop WebRTC offer is ready."


def test_get_voice_mode_reports_stable_subscription_profile(monkeypatch):
    from jarvis.platform import capabilities
    from jarvis.ui.web import provider_routes

    monkeypatch.setattr(
        provider_routes,
        "_codex_subscription_status_payload",
        lambda _binary: {
            "connected": True,
            "reason_code": "ready",
        },
    )
    monkeypatch.setattr(
        capabilities,
        "detect_capabilities",
        lambda: SimpleNamespace(display_present=True),
    )
    app = _app(mode="realtime")
    app.state.config.voice.profile = "codex-subscription-voice"
    app.state.config.brain = SimpleNamespace(realtime=None, providers={})
    app.state.config.stt = SimpleNamespace(provider="nemotron-local")
    app.state.config.tts = SimpleNamespace(provider="piper-local")
    app.state.speech_pipeline = object()

    body = TestClient(app).get("/api/settings/voice-mode").json()

    assert body["mode"] == "pipeline"
    assert body["profile"] == "codex-subscription-voice"
    assert body["active_provider"] == "codex-subscription-realtime"
    assert body["active_model"] == "subscription-text"
    assert body["active_model_label"] == "Codex App Server (subscription text)"
    assert body["requires_webrtc_offer"] is False
    assert body["subscription_voice_capability"]["available"] is True


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
    assert body["active_provider_label"] == "Gemini Live"
    assert body["active_model"] == "gemini-3.1-flash-live-preview"


def test_get_voice_mode_reports_browser_audio_of_the_automatic_provider(monkeypatch):
    """Issue #399: a Gemini-only install with no pinned realtime provider
    reported ``active_provider: gemini-live`` next to ``browser_audio: false``
    although Gemini Live declares browser audio."""
    import jarvis.realtime.factory as rf

    def only_gemini(candidates):
        keys = {c[0] for c in candidates}
        return "sk-x" if "gemini_api_key" in keys else None

    monkeypatch.setattr(rf, "get_secret_any", only_gemini)
    body = TestClient(_app(mode="realtime")).get("/api/settings/voice-mode").json()
    assert body["active_provider"] == "gemini-live"
    assert body["browser_audio"] is True


def test_get_voice_mode_reports_pinned_realtime_model(monkeypatch):
    """A model pinned in [brain.providers.<id>].model outranks the catalog
    default in the sidebar display fields."""
    import jarvis.realtime.factory as rf

    def only_gemini(candidates):
        keys = {c[0] for c in candidates}
        return "sk-x" if "gemini_api_key" in keys else None

    monkeypatch.setattr(rf, "get_secret_any", only_gemini)
    app = _app(mode="realtime")
    app.state.config.brain = SimpleNamespace(
        providers={
            "gemini-live": SimpleNamespace(
                model="gemini-2.5-flash-native-audio-latest", voice=""
            )
        }
    )
    body = TestClient(app).get("/api/settings/voice-mode").json()
    assert body["active_provider"] == "gemini-live"
    assert body["active_model"] == "gemini-2.5-flash-native-audio-latest"


def test_get_voice_mode_no_realtime_key_anywhere(monkeypatch):
    import jarvis.realtime.factory as rf
    monkeypatch.setattr(rf, "get_secret_any", lambda _candidates: None)
    client = TestClient(_app(mode="pipeline"))
    r = client.get("/api/settings/voice-mode")
    body = r.json()
    assert body["realtime_available"] is False
    assert body["active_provider"] is None
    assert body["active_provider_label"] is None
    assert body["active_model"] is None


# The codex-subscription-realtime adapter (and its busy/logged-out PUT gates)
# was removed 2026-08-10; the generic "no ready provider" 400 below is the
# remaining refusal path for an unavailable realtime engine.


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
    assert r.json() == {
        "ok": True,
        "mode": "realtime",
        "persisted": True,
        "session_restarted": False,
    }
    assert app.state.config.voice.mode == "realtime"
    assert persisted["called"] is True


def test_put_voice_mode_restarts_an_incompatible_active_session(monkeypatch):
    import jarvis.realtime.factory as rf

    monkeypatch.setattr(rf, "get_secret_any", lambda _candidates: "sk-x")
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(cw, "set_voice_mode", lambda _mode, **_kw: None)

    applied: list[str] = []

    class LivePipeline:
        def apply_voice_mode(self, mode: str) -> bool:
            applied.append(mode)
            return True

    app = _app(mode="pipeline")
    app.state.speech_pipeline = LivePipeline()
    client = TestClient(app)

    response = client.put(
        "/api/settings/voice-mode",
        json={"mode": "realtime", "persist": True},
    )

    assert response.status_code == 200
    assert response.json()["session_restarted"] is True
    assert applied == ["realtime"]


def test_get_voice_mode_reports_effective_active_engine(monkeypatch):
    import jarvis.realtime.factory as rf

    monkeypatch.setattr(rf, "get_secret_any", lambda _candidates: "sk-x")
    app = _app(mode="realtime")
    app.state.speech_pipeline = SimpleNamespace(
        voice_engine_status=lambda: {
            "session_active": True,
            "active_session_mode": "realtime",
            "active_session_provider": "openai-realtime",
            "active_session_model": "gpt-realtime-2.1",
            "transitioning": False,
        }
    )
    client = TestClient(app)

    body = client.get("/api/settings/voice-mode").json()

    assert body["mode"] == "realtime"
    assert body["session_active"] is True
    assert body["active_session_mode"] == "realtime"
    assert body["active_session_provider"] == "openai-realtime"
    assert body["active_session_model"] == "gpt-realtime-2.1"
    assert body["active_session_model_label"] == "GPT Realtime 2.1"
    assert body["transitioning"] is False
