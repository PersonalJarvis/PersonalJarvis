"""Regression guards for restart-free STT provider switching."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig, STTConfig
from jarvis.speech.pipeline import SpeechPipeline
from jarvis.ui.web import provider_routes
from jarvis.ui.web.server import WebServer


class _Provider:
    def __init__(self, name: str) -> None:
        self.name = name


def _bare_pipeline() -> tuple[SpeechPipeline, Any, Any]:
    pipeline = SpeechPipeline.__new__(SpeechPipeline)
    old_provider = _Provider("groq-api")
    old_dictation = _Provider("faster-whisper")
    pipeline._config = SimpleNamespace(stt=STTConfig(provider="groq-api", fallback=""))
    pipeline._dictation_cfg = SimpleNamespace(bias_prompt="")
    pipeline._stt = None
    pipeline._utterance_stt = old_provider
    pipeline._probe_stt = old_provider
    pipeline._dictation_stt_instance = old_dictation
    pipeline._voice_stt_fallback_chain = ("openai-api",)
    pipeline._voice_stt_fallback_instances = {"openai-api": object()}
    return pipeline, old_provider, old_dictation


def test_pipeline_switch_replaces_voice_and_next_dictation_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, old_provider, _old_dictation = _bare_pipeline()
    built: list[str] = []

    def _build(cfg: Any) -> _Provider:
        built.append(str(cfg.provider))
        return _Provider(str(cfg.provider))

    monkeypatch.setattr("jarvis.plugins.stt.build_stt_from_config", _build)
    monkeypatch.setattr("jarvis.plugins.stt.provider_runs_on_device", lambda _provider: False)
    monkeypatch.setattr(
        "jarvis.speech.stt_fallback.wrap_stt_with_fallback",
        lambda provider, _cfg: provider,
    )
    monkeypatch.setattr(
        "jarvis.speech.stt_dictionary.wrap_stt_with_dictionary",
        lambda provider: provider,
    )

    assert pipeline.set_stt_provider("openrouter-stt") is True

    assert pipeline._utterance_stt.name == "openrouter-stt"
    assert pipeline._utterance_stt is not old_provider
    assert pipeline._probe_stt is pipeline._utterance_stt
    assert pipeline._config.stt.provider == "openrouter-stt"
    assert pipeline._dictation_stt_instance is None
    assert pipeline._voice_stt_fallback_chain is None
    assert pipeline._voice_stt_fallback_instances == {}

    # The dictation lane builds its own prompt-free instance lazily. Its first
    # use after the cut-over must read the new provider, not the stale cache.
    assert pipeline._dictation_stt().name == "openrouter-stt"
    assert built == ["openrouter-stt", "openrouter-stt"]


def test_pipeline_switch_failure_preserves_every_live_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, old_provider, old_dictation = _bare_pipeline()

    def _fail(_cfg: Any) -> Any:
        raise RuntimeError("constructor failed")

    monkeypatch.setattr("jarvis.plugins.stt.build_stt_from_config", _fail)

    assert pipeline.set_stt_provider("openrouter-stt") is False
    assert pipeline._utterance_stt is old_provider
    assert pipeline._probe_stt is old_provider
    assert pipeline._dictation_stt_instance is old_dictation
    assert pipeline._config.stt.provider == "groq-api"


@pytest.fixture
def web_server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.stt.provider = "groq-api"
    server = WebServer(cfg, bus=EventBus())
    yield server


class _LivePipeline:
    def __init__(self, cfg: JarvisConfig) -> None:
        self.cfg = cfg
        self.calls: list[tuple[str, str | None]] = []
        self.mode_calls: list[str] = []

    def set_stt_provider(self, provider: str, *, model: str | None = None) -> bool:
        self.calls.append((provider, model))
        self.cfg.stt.provider = provider
        return True

    def apply_voice_mode(self, mode: str) -> bool:
        self.mode_calls.append(mode)
        self.cfg.voice.mode = mode
        return True


def test_switch_route_applies_to_running_pipeline_without_restart(
    web_server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _LivePipeline(web_server.cfg)
    web_server.app.state.speech_pipeline = pipeline
    writes: list[str] = []
    mode_writes: list[str] = []
    web_server.cfg.voice.mode = "realtime"
    monkeypatch.setattr(provider_routes, "_is_credential_present", lambda _spec: True)
    monkeypatch.setattr("jarvis.brain.app_control.local_readiness_error", lambda _spec: None)
    monkeypatch.setattr("jarvis.core.config_writer.set_stt_provider", writes.append)
    monkeypatch.setattr("jarvis.core.config_writer.set_voice_mode", mode_writes.append)

    with TestClient(web_server.app) as client:
        response = client.post(
            "/api/stt/switch",
            json={"provider": "openrouter-stt", "persist": True},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "active": "openrouter-stt",
        "persisted": True,
        "live_switched": True,
        "restart_required": False,
        "session_restarted": False,
    }
    assert pipeline.calls == [("openrouter-stt", None)]
    assert pipeline.mode_calls == []
    assert web_server.cfg.stt.provider == "openrouter-stt"
    assert web_server.cfg.voice.mode == "realtime"
    assert writes == ["openrouter-stt"]
    assert mode_writes == []


def test_switch_route_never_requests_restart_when_voice_is_not_running(
    web_server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial_mode = web_server.cfg.voice.mode
    monkeypatch.setattr(provider_routes, "_is_credential_present", lambda _spec: True)
    monkeypatch.setattr("jarvis.brain.app_control.local_readiness_error", lambda _spec: None)

    with TestClient(web_server.app) as client:
        response = client.post(
            "/api/stt/switch",
            json={"provider": "openrouter-stt", "persist": False},
        )

    assert response.status_code == 200, response.text
    assert response.json()["live_switched"] is False
    assert response.json()["restart_required"] is False
    assert response.json()["session_restarted"] is False
    assert web_server.cfg.stt.provider == "openrouter-stt"
    assert web_server.cfg.voice.mode == initial_mode
