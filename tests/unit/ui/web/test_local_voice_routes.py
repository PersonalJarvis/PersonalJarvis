"""Routes behind the Local voice card (``jarvis/ui/web/local_voice_routes.py``).

Pins: the status read never starts anything, the self-test refuses honestly
before setup, settings land in ``[voice_engine]`` through the config writer and
in memory, and the provider list carries the card's ``voice_engine`` probe.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.brain import ollama_pull
from jarvis.core import config_writer
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.plugins.realtime.local_voice import LocalVoiceProvider
from jarvis.realtime import local_voice_setup as setup
from jarvis.ui.web.local_voice_routes import router


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    engine_home = tmp_path / "engine"
    monkeypatch.setenv("JARVIS_VOICE_ENGINE_HOME", str(engine_home))
    monkeypatch.setattr(setup, "machine_class", lambda probe=None: "cpu")

    async def installed() -> tuple[set[str], str | None]:
        return {"qwen3.5:2b"}, None

    async def every_model_calls_tools(names: set[str]) -> set[str]:
        return names

    monkeypatch.setattr(ollama_pull, "installed_models", installed)
    # Never read the machine's real Ollama inventory from a test.
    monkeypatch.setattr(setup, "_without_toolless", every_model_calls_tools)
    setup._reset_for_tests()
    LocalVoiceProvider._engine = None
    yield engine_home
    setup._reset_for_tests()
    LocalVoiceProvider._engine = None


@pytest.fixture
def client(home: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.config = JarvisConfig()
    return TestClient(app)


def test_status_before_setup_starts_nothing(client: TestClient, home: Path) -> None:
    resp = client.get("/api/providers/local-voice/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "not_installed"
    assert body["machine_class"] == "cpu"
    assert (body["llm_model"], body["llm_installed"]) == ("qwen3.5:2b", True)
    assert body["expected_latency"]["basis"] == "estimate"
    assert LocalVoiceProvider._engine is None
    assert not home.exists()


def test_selftest_refuses_before_setup(client: TestClient) -> None:
    resp = client.post("/api/providers/local-voice/selftest")
    assert resp.status_code == 409
    assert "not set up" in resp.json()["detail"]


def test_settings_are_written_and_applied(client: TestClient, tmp_path: Path) -> None:
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text("[brain]\nprimary = \"ollama\"\n", encoding="utf-8")
    original = config_writer.set_voice_engine_settings

    def write_here(**kwargs):
        return original(path=config_file, **kwargs)

    pytest.MonkeyPatch().setattr(config_writer, "set_voice_engine_settings", write_here)
    try:
        resp = client.put("/api/providers/local-voice/settings",
                          json={"voice": "piper", "llm_model": "granite4.2:8b"})
    finally:
        config_writer.set_voice_engine_settings = original
    assert resp.status_code == 200
    body = resp.json()
    assert body["voice"] == "piper"
    assert (body["llm_model"], body["llm_source"]) == ("granite4.2:8b", "config")
    text = config_file.read_text(encoding="utf-8")
    assert "[voice_engine]" in text and 'tts = "piper"' in text
    assert 'llm_model = "granite4.2:8b"' in text


def test_an_unknown_voice_is_rejected(client: TestClient) -> None:
    resp = client.put("/api/providers/local-voice/settings", json={"voice": "kokoro"})
    assert resp.status_code == 400


def test_the_provider_list_carries_the_voice_engine_probe(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.ui.web.server import WebServer

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    with TestClient(srv.app) as web:
        resp = web.get("/api/providers")
    assert resp.status_code == 200
    payload = resp.json()
    rows = payload["providers"] if isinstance(payload, dict) else payload
    providers = {row["id"]: row for row in rows}
    assert providers["local-voice"]["voice_engine"] == {"installed": False, "ready": False}
    assert providers["local-voice"]["billing"] == "local"
    assert providers["gemini-live"]["voice_engine"] is None
