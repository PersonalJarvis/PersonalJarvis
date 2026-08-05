"""Pipeline provider cards never change the user's voice-engine selection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web import provider_routes
from jarvis.ui.web.server import WebServer


class _LivePipeline:
    def __init__(self, cfg: JarvisConfig) -> None:
        self.cfg = cfg
        self.tts: object | None = None
        self.mode_calls: list[str] = []

    def set_tts(self, provider: object) -> None:
        self.tts = provider

    def apply_voice_mode(self, mode: str) -> bool:
        self.mode_calls.append(mode)
        self.cfg.voice.mode = mode
        return True


def test_local_tts_selection_preserves_realtime_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = JarvisConfig()
    cfg.voice.mode = "realtime"
    server = WebServer(cfg, bus=EventBus())
    pipeline = _LivePipeline(cfg)
    server.app.state.speech_pipeline = pipeline
    provider_writes: list[str] = []
    mode_writes: list[str] = []
    built = SimpleNamespace(name="piper-local")

    monkeypatch.setattr(provider_routes, "_is_credential_present", lambda _spec: True)
    monkeypatch.setattr("jarvis.brain.app_control.local_readiness_error", lambda _spec: None)
    monkeypatch.setattr("jarvis.core.config_writer.set_tts_provider", provider_writes.append)
    monkeypatch.setattr("jarvis.core.config_writer.set_voice_mode", mode_writes.append)
    monkeypatch.setattr("jarvis.plugins.tts.build_tts_from_config", lambda _cfg: built)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/tts/switch",
            json={"provider": "piper-local", "persist": True},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "active": "piper-local",
        "persisted": True,
        "live_switched": True,
        "restart_required": False,
        "session_restarted": False,
    }
    assert provider_writes == ["piper-local"]
    assert mode_writes == []
    assert pipeline.tts is built
    assert pipeline.mode_calls == []
    assert cfg.voice.mode == "realtime"
