"""REST contract for capability-aware Tool Model selection."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.brain.model_catalog import ModelInfo
from jarvis.core import config_writer
from jarvis.core.config import JarvisConfig
from jarvis.ui.web import tool_model_routes
from jarvis.ui.web.provider_routes import _brain_model_info


class _Brain:
    def __init__(self, cfg: JarvisConfig) -> None:
        self._config = cfg
        self.ready = True
        self.reactivated: list[str] = []

    def tool_model_candidate_status(self, provider: str, model: str | None = None):
        return {
            "provider": provider,
            "model": model or "main-model",
            "ready": self.ready,
            "reason": "ready" if self.ready else "tools_unsupported",
            "tools": self.ready,
            "vision": True,
        }

    def resolve_tool_model(self):
        tier = self._config.brain.tool_model
        provider = tier.provider if tier is not None else "auto"
        if provider == "auto":
            provider = "gemini"
        pc = self._config.brain.providers.get(provider)
        model = getattr(pc, "tool_model", None) if pc is not None else None
        return {
            "configured_provider": tier.provider if tier is not None else "auto",
            "configured_model": model,
            "effective_provider": provider,
            "effective_model": model or "main-model",
            "state": "ready",
            "reason": "configured_selection" if tier else "automatic_selection",
            "source": "tool_model" if tier else "auto",
            "tools": True,
            "vision": True,
        }

    def reactivate_provider(self, provider: str) -> None:
        self.reactivated.append(provider)


def _app() -> tuple[FastAPI, _Brain]:
    app = FastAPI()
    app.include_router(tool_model_routes.router)
    app.state.config = JarvisConfig()
    brain = _Brain(app.state.config)
    app.state.brain = brain
    return app, brain


def test_get_status_reports_effective_auto_selection() -> None:
    app, _brain = _app()

    response = TestClient(app).get("/api/tool-model/status")

    assert response.status_code == 200
    assert response.json()["configured_provider"] == "auto"
    assert response.json()["effective_provider"] == "gemini"
    assert response.json()["tools"] is True


def test_put_persists_canonical_selection_and_updates_live(
    monkeypatch,
) -> None:
    app, brain = _app()
    writes: list[tuple[str, str | None]] = []
    monkeypatch.setattr(tool_model_routes, "is_credential_present", lambda _s: True)
    monkeypatch.setattr(
        config_writer,
        "set_tool_model_selection",
        lambda provider, *, model=None, **_kw: writes.append((provider, model)),
    )

    response = TestClient(app).put(
        "/api/tool-model",
        json={"provider": "gemini", "model": "gemini-tool", "persist": True},
    )

    assert response.status_code == 200
    assert writes == [("gemini", "gemini-tool")]
    assert app.state.config.brain.tool_model.provider == "gemini"
    assert app.state.config.brain.providers["gemini"].tool_model == "gemini-tool"
    assert brain.reactivated == ["gemini"]
    assert response.json()["persisted"] is True


def test_put_reactivates_a_provider_before_its_runtime_probe(monkeypatch) -> None:
    app, brain = _app()
    monkeypatch.setattr(tool_model_routes, "is_credential_present", lambda _s: True)

    def _probe(provider: str, model: str | None = None):
        ready = provider in brain.reactivated
        return {
            "provider": provider,
            "model": model,
            "ready": ready,
            "reason": "ready" if ready else "provider_dead",
            "tools": ready,
            "vision": None,
        }

    brain.tool_model_candidate_status = _probe  # type: ignore[method-assign]

    response = TestClient(app).put(
        "/api/tool-model",
        json={"provider": "gemini", "persist": False},
    )

    assert response.status_code == 200
    assert brain.reactivated == ["gemini"]


def test_put_rejects_runtime_toolless_provider(monkeypatch) -> None:
    app, brain = _app()
    brain.ready = False
    monkeypatch.setattr(tool_model_routes, "is_credential_present", lambda _s: True)

    response = TestClient(app).put(
        "/api/tool-model",
        json={"provider": "gemini", "model": "text-only", "persist": False},
    )

    assert response.status_code == 409
    assert "tools_unsupported" in response.json()["detail"]


def test_put_auto_needs_no_provider_credential() -> None:
    app, _brain = _app()

    response = TestClient(app).put(
        "/api/tool-model", json={"provider": "auto", "persist": False}
    )

    assert response.status_code == 200
    assert app.state.config.brain.tool_model.provider == "auto"


def test_provider_model_metadata_exposes_tool_capability() -> None:
    capable = _brain_model_info(
        ModelInfo(
            id="vendor/tool-model",
            label="Tool Model",
            supported_parameters=("tools", "temperature"),
        )
    )
    incapable = _brain_model_info(
        ModelInfo(
            id="vendor/text-model",
            label="Text Model",
            supported_parameters=("temperature",),
        )
    )

    assert capable.tools is True
    assert incapable.tools is False
