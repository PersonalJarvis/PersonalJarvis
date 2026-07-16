"""Realtime provider tier for the API-Keys & Providers Realtime tab.

Covers both provider families, registry-based active-provider resolution,
credential health, switching, and per-provider model and voice selection.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core import config as cfg_mod
from jarvis.core import config_writer
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.provider_routes import router
from jarvis.ui.web.provider_spec import get_spec


def _only_openai_key(key: str, *_a, **_kw) -> str | None:
    """Fake ``cfg_mod.get_secret``: only ``openai_api_key`` looks configured."""
    return "sk-test" if key == "openai_api_key" else None


def _only_gemini_key(key: str, *_a, **_kw) -> str | None:
    """Fake ``cfg_mod.get_secret`` for a Gemini-only fresh install."""
    return "AIza-test" if key == "gemini_api_key" else None

# ---------------------------------------------------------------------------
# ProviderSpec
# ---------------------------------------------------------------------------


def test_openai_realtime_spec_owns_a_dedicated_key():
    spec = get_spec("openai-realtime")
    assert spec is not None
    assert spec.tier == "realtime"
    assert spec.secret_keys == ("realtime_openai_api_key",)


def test_gemini_live_spec_present():
    spec = get_spec("gemini-live")
    assert spec is not None
    assert spec.tier == "realtime"
    assert spec.secret_keys == ("realtime_gemini_api_key",)
    assert spec.alt_credential is None


def test_grok_realtime_spec_owns_a_dedicated_key() -> None:
    spec = get_spec("grok-realtime")
    assert spec is not None
    assert spec.label == "xAI Grok Realtime"
    assert spec.tier == "realtime"
    assert spec.secret_keys == ("realtime_grok_api_key",)
    assert spec.dashboard_url == "https://console.x.ai/"


# ---------------------------------------------------------------------------
# GET /api/providers
# ---------------------------------------------------------------------------


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.config = JarvisConfig()
    return app


def test_list_providers_includes_active_realtime_provider(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    client = TestClient(_app())
    resp = client.get("/api/providers")
    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.json()["providers"]}
    assert "openai-realtime" in by_id
    realtime = by_id["openai-realtime"]
    assert realtime["tier"] == "realtime"
    # No explicit brain.realtime.provider set -> defaults to the sole spec,
    # so the only realtime card shows as active rather than "nothing selected".
    assert realtime["active"] is True
    assert realtime["configured"] is True


def test_list_providers_resolves_gemini_only_fresh_install(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", _only_gemini_key)
    client = TestClient(_app())

    resp = client.get("/api/providers")

    assert resp.status_code == 200
    by_id = {provider["id"]: provider for provider in resp.json()["providers"]}
    assert by_id["gemini-live"]["active"] is True
    assert by_id["openai-realtime"]["active"] is False


# ---------------------------------------------------------------------------
# POST /api/realtime/switch
# ---------------------------------------------------------------------------


def test_realtime_switch_persists_with_key(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    writes: list[str] = []
    monkeypatch.setattr(
        config_writer, "set_realtime_provider", lambda name, **kw: writes.append(name)
    )
    # Feature A4 also flips [voice].mode inside this route; mock it too so the
    # test never touches the real jarvis.toml (isolation defect otherwise).
    monkeypatch.setattr(config_writer, "set_voice_mode", lambda mode, **kw: None)

    app = _app()
    client = TestClient(app)
    resp = client.post(
        "/api/realtime/switch", json={"provider": "openai-realtime", "persist": True}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["active"] == "openai-realtime"
    assert body["persisted"] is True
    assert body["restart_required"] is False
    assert writes == ["openai-realtime"]
    assert app.state.config.brain.realtime is not None
    assert app.state.config.brain.realtime.provider == "openai-realtime"


def test_realtime_switch_sets_voice_mode(monkeypatch):
    """Feature A4: activating a realtime provider must flip [voice].mode to
    "realtime" too — the "Active" badge reads [voice].mode, not
    [brain.realtime].provider, so persisting only the provider can never move
    it (the original bug this feature fixes)."""
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    monkeypatch.setattr(config_writer, "set_realtime_provider", lambda name, **kw: None)
    voice_mode_writes: list[str] = []
    monkeypatch.setattr(
        config_writer, "set_voice_mode", lambda mode, **kw: voice_mode_writes.append(mode)
    )

    app = _app()
    client = TestClient(app)
    resp = client.post(
        "/api/realtime/switch", json={"provider": "openai-realtime", "persist": True}
    )
    assert resp.status_code == 200
    assert voice_mode_writes == ["realtime"]
    assert app.state.config.voice.mode == "realtime"


def test_realtime_switch_reconnects_the_active_voice_session(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    monkeypatch.setattr(config_writer, "set_realtime_provider", lambda _name: None)
    monkeypatch.setattr(config_writer, "set_voice_mode", lambda _mode: None)
    reasons: list[str] = []

    class LivePipeline:
        def reconnect_realtime_session(self, *, reason: str) -> bool:
            reasons.append(reason)
            return True

    app = _app()
    app.state.speech_pipeline = LivePipeline()
    client = TestClient(app)

    response = client.post(
        "/api/realtime/switch",
        json={"provider": "openai-realtime", "persist": True},
    )

    assert response.status_code == 200
    assert response.json()["session_restarted"] is True
    assert reasons == ["realtime_provider:openai-realtime"]


def test_realtime_switch_reports_voice_mode_not_persisted_on_write_failure(monkeypatch):
    """Bug: the [voice].mode write failure was only logged, but the response
    still reported persisted=True unconditionally — the UI showed "saved"
    for a switch that silently left [voice].mode stale on disk."""
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    monkeypatch.setattr(config_writer, "set_realtime_provider", lambda name, **kw: None)

    def _boom(mode, **kw):
        raise RuntimeError("disk full (simulated)")

    monkeypatch.setattr(config_writer, "set_voice_mode", _boom)

    app = _app()
    client = TestClient(app)
    resp = client.post(
        "/api/realtime/switch", json={"provider": "openai-realtime", "persist": True}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["persisted"] is False
    assert body["voice_mode_persisted"] is False


def test_realtime_switch_without_key_is_409(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: None)
    client = TestClient(_app())
    resp = client.post(
        "/api/realtime/switch", json={"provider": "openai-realtime", "persist": True}
    )
    assert resp.status_code == 409


def test_realtime_switch_rejects_non_realtime_provider(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda key, *a, **kw: "sk-test")
    client = TestClient(_app())
    resp = client.post("/api/realtime/switch", json={"provider": "openai", "persist": True})
    assert resp.status_code == 400


def test_realtime_switch_unknown_provider_is_404(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: None)
    client = TestClient(_app())
    resp = client.post("/api/realtime/switch", json={"provider": "does-not-exist", "persist": True})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/providers/section-health
# ---------------------------------------------------------------------------


def test_section_health_includes_realtime_key(monkeypatch):
    from jarvis.brain import provider_test as _pt

    async def _fake_run(spec, cfg):
        from types import SimpleNamespace

        return SimpleNamespace(status="ok", detail="")

    monkeypatch.setattr(_pt, "run_provider_test", _fake_run)
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    client = TestClient(_app())
    resp = client.get("/api/providers/section-health")
    assert resp.status_code == 200
    sections = resp.json()["sections"]
    assert "realtime" in sections
    assert sections["realtime"]["status"] == "ok"


def test_section_health_realtime_needs_setup_without_key(monkeypatch):
    from jarvis.brain import provider_test as _pt

    async def _fake_run(spec, cfg):
        from types import SimpleNamespace

        return SimpleNamespace(status="ok", detail="")

    monkeypatch.setattr(_pt, "run_provider_test", _fake_run)
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: None)
    client = TestClient(_app())
    resp = client.get("/api/providers/section-health")
    sections = resp.json()["sections"]
    assert sections["realtime"]["status"] == "needs_setup"


# ---------------------------------------------------------------------------
# config_writer.set_realtime_provider
# ---------------------------------------------------------------------------


def test_set_realtime_provider_writes_nested_table(tmp_path: Path):
    toml = tmp_path / "jarvis.toml"
    toml.write_text("", encoding="utf-8")
    config_writer.set_realtime_provider("openai-realtime", path=toml)
    content = toml.read_text(encoding="utf-8")
    assert "[brain.realtime]" in content
    assert 'provider = "openai-realtime"' in content


def test_set_realtime_provider_preserves_sibling_worker_table(tmp_path: Path):
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        '[brain.worker]\nprovider = "claude-api"\n',
        encoding="utf-8",
    )
    config_writer.set_realtime_provider("openai-realtime", path=toml)
    content = toml.read_text(encoding="utf-8")
    assert '[brain.worker]' in content
    assert 'provider = "claude-api"' in content
    assert '[brain.realtime]' in content
    assert 'provider = "openai-realtime"' in content


# ---------------------------------------------------------------------------
# GET/PUT /api/providers/{id}/realtime-options
# (selectable Realtime model + voice, per realtime provider)
# ---------------------------------------------------------------------------


def test_get_realtime_options_returns_curated_models_and_voices(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    client = TestClient(_app())
    resp = client.get("/api/providers/openai-realtime/realtime-options")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "openai-realtime"
    voice_ids = {v["id"] for v in body["voices"]}
    assert voice_ids == {
        "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse",
        "marin", "cedar",
    }
    model_ids = [m["id"] for m in body["models"]]
    assert model_ids[0] == "gpt-realtime"  # the hardcoded default leads
    assert body["current_model"] == ""
    assert body["current_voice"] == ""


def test_get_realtime_options_gemini_live_voices(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "AIza-test")
    client = TestClient(_app())
    resp = client.get("/api/providers/gemini-live/realtime-options")
    assert resp.status_code == 200
    body = resp.json()
    voice_ids = {v["id"] for v in body["voices"]}
    assert voice_ids == {
        "Puck", "Charon", "Kore", "Fenrir", "Aoede", "Orus", "Leda", "Zephyr",
        "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
        "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
        "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
        "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
    }
    assert body["models"][0]["id"] == "gemini-3.1-flash-live-preview"


def test_get_realtime_options_grok_models_and_voices(monkeypatch) -> None:
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "xai-test")
    client = TestClient(_app())

    resp = client.get("/api/providers/grok-realtime/realtime-options")

    assert resp.status_code == 200
    body = resp.json()
    assert body["models"][0]["id"] == "grok-voice-latest"
    assert body["models"][1]["id"] == "grok-voice-think-fast-1.0"
    assert body["voices"][0]["id"] == "eve"
    assert {"ara", "leo", "rex", "sal"}.issubset(
        {voice["id"] for voice in body["voices"]}
    )


def test_get_realtime_options_reflects_pinned_selection(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    app = _app()
    from jarvis.core.config import BrainProviderConfig

    app.state.config.brain.providers["openai-realtime"] = BrainProviderConfig(
        model="gpt-realtime-2.1", voice="echo"
    )
    client = TestClient(app)
    resp = client.get("/api/providers/openai-realtime/realtime-options")
    body = resp.json()
    assert body["current_model"] == "gpt-realtime-2.1"
    assert body["current_voice"] == "echo"


def test_get_realtime_options_unknown_provider_404(monkeypatch):
    client = TestClient(_app())
    resp = client.get("/api/providers/does-not-exist/realtime-options")
    assert resp.status_code == 404


def test_get_realtime_options_rejects_non_realtime_provider(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    client = TestClient(_app())
    resp = client.get("/api/providers/gemini/realtime-options")
    assert resp.status_code == 400


def test_put_realtime_options_persists_voice(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    writes: list[tuple[str, str | None, str | None]] = []
    monkeypatch.setattr(
        config_writer,
        "set_brain_provider_model",
        lambda provider, *, model=None, voice=None, **_k: writes.append(
            (provider, model, voice)
        ),
    )
    app = _app()
    client = TestClient(app)
    resp = client.put(
        "/api/providers/openai-realtime/realtime-options", json={"voice": "echo"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["voice"] == "echo"
    assert body["restart_required"] is False
    assert ("openai-realtime", None, "echo") in writes
    assert app.state.config.brain.providers["openai-realtime"].voice == "echo"


def test_put_realtime_options_persists_model_and_voice(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    writes: list[tuple[str, str | None, str | None]] = []
    monkeypatch.setattr(
        config_writer,
        "set_brain_provider_model",
        lambda provider, *, model=None, voice=None, **_k: writes.append(
            (provider, model, voice)
        ),
    )
    app = _app()
    client = TestClient(app)
    resp = client.put(
        "/api/providers/openai-realtime/realtime-options",
        json={"model": "gpt-realtime-2.1", "voice": "echo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "gpt-realtime-2.1"
    assert body["voice"] == "echo"
    assert ("openai-realtime", "gpt-realtime-2.1", "echo") in writes
    pc = app.state.config.brain.providers["openai-realtime"]
    assert pc.model == "gpt-realtime-2.1"
    assert pc.voice == "echo"


def test_put_realtime_options_omitted_field_leaves_it_unwritten(monkeypatch):
    """Only the field actually present in the body is persisted — mirrors the
    model/cu_model endpoints' partial-update contract."""
    monkeypatch.setattr(cfg_mod, "get_secret", _only_openai_key)
    writes: list[tuple[str, str | None, str | None]] = []
    monkeypatch.setattr(
        config_writer,
        "set_brain_provider_model",
        lambda provider, *, model=None, voice=None, **_k: writes.append(
            (provider, model, voice)
        ),
    )
    client = TestClient(_app())
    resp = client.put(
        "/api/providers/openai-realtime/realtime-options",
        json={"model": "gpt-realtime-2.1"},
    )
    assert resp.status_code == 200
    assert ("openai-realtime", "gpt-realtime-2.1", None) in writes


def test_put_realtime_options_without_key_is_409(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: None)
    client = TestClient(_app())
    resp = client.put(
        "/api/providers/openai-realtime/realtime-options", json={"voice": "echo"}
    )
    assert resp.status_code == 409


def test_put_realtime_options_rejects_non_realtime_provider(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    client = TestClient(_app())
    resp = client.put("/api/providers/gemini/realtime-options", json={"voice": "x"})
    assert resp.status_code == 400


def test_put_realtime_options_unknown_provider_404(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    client = TestClient(_app())
    resp = client.put(
        "/api/providers/does-not-exist/realtime-options", json={"voice": "x"}
    )
    assert resp.status_code == 404
