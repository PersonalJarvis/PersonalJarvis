"""Validation contracts for curated Realtime model and voice options."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core import config as cfg_mod
from jarvis.core import config_writer
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.ui.web.provider_routes import router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.config = JarvisConfig()
    return app


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        ({"model": "unlisted-realtime-model"}, "unsupported_realtime_model"),
        ({"voice": "unlisted-realtime-voice"}, "unsupported_realtime_voice"),
    ],
)
def test_put_realtime_options_rejects_values_outside_curated_catalog(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, str],
    error_code: str,
) -> None:
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *args, **kwargs: "sk-test")
    writes: list[object] = []
    monkeypatch.setattr(
        config_writer,
        "set_brain_provider_model",
        lambda *args, **kwargs: writes.append((args, kwargs)),
    )

    response = TestClient(_app()).put(
        "/api/providers/openai-realtime/realtime-options",
        json=payload,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == error_code
    assert payload[next(iter(payload))] in detail["message"]
    assert detail["allowed_values"]
    assert writes == []


def test_put_realtime_options_allows_fully_omitted_partial_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *args, **kwargs: "sk-test")

    def unexpected_write(*args, **kwargs):
        raise AssertionError("An omitted update must not write configuration")

    monkeypatch.setattr(
        config_writer, "set_brain_provider_model", unexpected_write
    )
    app = _app()
    app.state.config.brain.providers["openai-realtime"] = BrainProviderConfig(
        model="gpt-realtime-2.1",
        voice="echo",
    )

    response = TestClient(app).put(
        "/api/providers/openai-realtime/realtime-options",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["model"] == "gpt-realtime-2.1"
    assert response.json()["voice"] == "echo"


def test_put_realtime_options_keeps_explicit_clear_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *args, **kwargs: "sk-test")
    writes: list[tuple[str | None, str | None]] = []
    monkeypatch.setattr(
        config_writer,
        "set_brain_provider_model",
        lambda _provider, *, model=None, voice=None, **_kwargs: writes.append(
            (model, voice)
        ),
    )

    response = TestClient(_app()).put(
        "/api/providers/openai-realtime/realtime-options",
        json={"model": "", "voice": ""},
    )

    assert response.status_code == 200
    assert writes == [("", "")]
