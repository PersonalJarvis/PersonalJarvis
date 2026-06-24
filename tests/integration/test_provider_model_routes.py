"""Integration tests for the per-provider model picker endpoints:

    GET /api/providers/{id}/models   — the searchable model list (live catalog)
    PUT /api/providers/{id}/model    — pin a model, live-apply + honest probe

Hermetic: the catalog is an injected fake (no network), the live probe is
monkeypatched, and the TOML writer is patched so no disk write happens.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.brain.model_catalog import CatalogResult, ModelInfo
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


class _FakeCatalog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def list_models(self, provider: str, *, force_refresh: bool = False) -> CatalogResult:
        self.calls.append((provider, force_refresh))
        return CatalogResult(
            provider=provider,
            models=(
                ModelInfo(id="gemini-3.1-pro-preview", label="Gemini 3.1 Pro"),
                ModelInfo(id="gemini-3-flash-preview", label="Gemini 3 Flash"),
            ),
            source="live",
            fetched_at=123.0,
        )


class _FakeBrain:
    def __init__(self, active: str = "gemini") -> None:
        self.active_provider = active
        self.applied: list[tuple[str, str]] = []

    def apply_provider_model(self, provider: str, model: str) -> bool:
        self.applied.append((provider, model))
        return provider == self.active_provider


@pytest.fixture
def server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    srv.app.state.model_catalog = _FakeCatalog()
    srv.app.state.brain = _FakeBrain(active="gemini")
    yield srv


# ── GET /models ──────────────────────────────────────────────────────────────

def test_get_models_returns_catalog(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.get("/api/providers/gemini/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "gemini"
        assert body["source"] == "live"
        ids = [m["id"] for m in body["models"]]
        assert "gemini-3.1-pro-preview" in ids
        # current_model falls back to the frontier default when unset.
        assert body["current_model"]


def test_get_models_passes_refresh_flag(server: WebServer) -> None:
    with TestClient(server.app) as client:
        client.get("/api/providers/gemini/models?refresh=true")
    catalog: _FakeCatalog = server.app.state.model_catalog
    assert catalog.calls[-1] == ("gemini", True)


def test_get_models_unknown_provider_404(server: WebServer) -> None:
    with TestClient(server.app) as client:
        assert client.get("/api/providers/nope/models").status_code == 404


def test_get_models_stt_provider_now_has_catalog(server: WebServer) -> None:
    with TestClient(server.app) as client:
        # deepgram is an STT provider — it now exposes a model catalog.
        resp = client.get("/api/providers/deepgram/models")
        assert resp.status_code == 200


def test_get_models_codex_now_has_catalog(server: WebServer) -> None:
    with TestClient(server.app) as client:
        # codex (subscription brain) now exposes a curated model catalog.
        assert client.get("/api/providers/codex/models").status_code == 200


# ── PUT /model ───────────────────────────────────────────────────────────────

def test_put_model_persists_applies_and_probes(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[tuple[str, str | None]] = []

    def _fake_writer(provider: str, *, model: str | None = None, **_k: Any) -> None:
        writes.append((provider, model))

    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_provider_model", _fake_writer
    )

    from jarvis.brain import provider_test as pt

    async def _fake_probe(provider: str, model: str, *, timeout_s: float = 20.0):
        return pt.ProviderTestResult(provider, pt.OK, "", 42.0)

    monkeypatch.setattr("jarvis.ui.web.provider_routes._probe_brain_model", _fake_probe)

    with TestClient(server.app) as client:
        resp = client.put(
            "/api/providers/gemini/model",
            json={"model": "gemini-3.1-pro-preview", "persist": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["model"] == "gemini-3.1-pro-preview"
        assert body["persisted"] is True
        assert body["applied_live"] is True  # gemini is the active brain
        assert body["restart_required"] is False
        assert body["probe"]["status"] == "ok"
        assert body["probe"]["integration_ok"] is True

    # Persisted to TOML and live-applied to the running brain.
    assert ("gemini", "gemini-3.1-pro-preview") in writes
    brain: _FakeBrain = server.app.state.brain
    assert ("gemini", "gemini-3.1-pro-preview") in brain.applied


def test_put_model_inactive_provider_not_live(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_provider_model",
        lambda *a, **k: None,
    )
    from jarvis.brain import provider_test as pt

    async def _fake_probe(provider: str, model: str, *, timeout_s: float = 20.0):
        return pt.ProviderTestResult(provider, pt.OK, "", 10.0)

    monkeypatch.setattr("jarvis.ui.web.provider_routes._probe_brain_model", _fake_probe)

    with TestClient(server.app) as client:
        resp = client.put(
            "/api/providers/openai/model",  # active brain is gemini
            json={"model": "gpt-5.5", "persist": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied_live"] is False
        assert body["restart_required"] is False  # applies on switch


def test_put_model_headless_requires_restart(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.app.state.brain = None  # headless: no running brain
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_provider_model",
        lambda *a, **k: None,
    )
    from jarvis.brain import provider_test as pt

    async def _fake_probe(provider: str, model: str, *, timeout_s: float = 20.0):
        return pt.ProviderTestResult(provider, pt.MODEL_UNAVAILABLE, "404 model", 5.0)

    monkeypatch.setattr("jarvis.ui.web.provider_routes._probe_brain_model", _fake_probe)

    with TestClient(server.app) as client:
        resp = client.put(
            "/api/providers/gemini/model",
            json={"model": "gemini-bogus", "persist": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied_live"] is False
        assert body["restart_required"] is True
        # The probe is honest about a non-existent model — saved anyway.
        assert body["probe"]["status"] == "model_unavailable"


def test_put_tts_voice_persists_no_probe(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    voices: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_tts_voice", lambda v, **k: voices.append(v)
    )
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/providers/grok-voice/model",  # a TTS voice provider
            json={"model": "rex", "persist": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["persisted"] is True
        assert body["restart_required"] is True  # no live pipeline in the test
        assert body["probe"] is None  # TTS does not run a brain probe
    assert voices == ["rex"]


def test_put_stt_model_requires_restart(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    models: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_stt_model", lambda m, **k: models.append(m)
    )
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/providers/deepgram/model",
            json={"model": "nova-3", "persist": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["persisted"] is True
        assert body["restart_required"] is True
        assert body["probe"] is None
    assert models == ["nova-3"]


# ── GET/PUT /cu-model (Phase 3: selectable Computer-Use model) ────────────────


def test_get_cu_model_defaults_to_main(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.get("/api/providers/gemini/cu-model")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "gemini"
        assert body["cu_model"] == ""           # nothing pinned
        assert body["uses_main"] is True
        assert body["effective_model"]          # the model CU would actually use


def test_put_cu_model_persists_and_updates_live(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[tuple[str, str | None]] = []

    def _fake_writer(provider: str, *, cu_model: str | None = None, **_k: Any) -> None:
        writes.append((provider, cu_model))

    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_provider_model", _fake_writer
    )

    with TestClient(server.app) as client:
        resp = client.put(
            "/api/providers/gemini/cu-model",
            json={"cu_model": "gemini-3.1-pro-preview", "persist": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["cu_model"] == "gemini-3.1-pro-preview"
        assert body["uses_main"] is False
        assert body["persisted"] is True
        assert body["restart_required"] is False

    assert ("gemini", "gemini-3.1-pro-preview") in writes
    # In-memory cfg updated so the next CU mission uses it without a restart.
    pc = server.app.state.config.brain.providers["gemini"]
    assert pc.cu_model == "gemini-3.1-pro-preview"


def test_put_cu_model_empty_clears_to_main(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core.config import BrainProviderConfig

    server.app.state.config.brain.providers["gemini"] = BrainProviderConfig(
        model="gemini-3.5-flash", cu_model="gemini-3.1-pro-preview"
    )
    writes: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_provider_model",
        lambda provider, *, cu_model=None, **_k: writes.append((provider, cu_model)),
    )

    with TestClient(server.app) as client:
        resp = client.put(
            "/api/providers/gemini/cu-model", json={"cu_model": "", "persist": True}
        )
        assert resp.status_code == 200
        assert resp.json()["uses_main"] is True

    assert ("gemini", "") in writes


def test_cu_model_rejected_for_non_brain_provider(server: WebServer) -> None:
    with TestClient(server.app) as client:
        # grok-voice is a TTS provider — Computer-Use model does not apply.
        assert client.get("/api/providers/grok-voice/cu-model").status_code == 400
        assert (
            client.put(
                "/api/providers/grok-voice/cu-model", json={"cu_model": "x"}
            ).status_code
            == 400
        )
