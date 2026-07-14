"""Gemini Live credential aliases stay aligned across factory and UI paths."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.brain.app_control import AUTH_PROVIDER_ALIASES, is_credential_present
from jarvis.core import config as cfg_mod
from jarvis.core.config import PROVIDER_SECRET_CANDIDATES, JarvisConfig
from jarvis.plugins.realtime.gemini_live import GeminiLiveProvider
from jarvis.ui.web.provider_routes import router
from jarvis.ui.web.provider_spec import get_spec


def test_gemini_live_adapter_credentials_match_canonical_family() -> None:
    assert GeminiLiveProvider.credential_candidates == PROVIDER_SECRET_CANDIDATES[
        "gemini"
    ]
    assert AUTH_PROVIDER_ALIASES["gemini-live"] == "gemini"


@pytest.mark.parametrize(
    "available_slot",
    ["gemini_api_key", "google_aistudio_api_key", "google_api_key"],
)
def test_gemini_live_provider_spec_and_switch_accept_every_alias(
    monkeypatch: pytest.MonkeyPatch,
    available_slot: str,
) -> None:
    def fake_get_secret(
        key: str, env_fallback: str | None = None
    ) -> str | None:
        return "AIza-test" if key == available_slot else None

    monkeypatch.setattr(cfg_mod, "get_secret", fake_get_secret)
    spec = get_spec("gemini-live")
    assert spec is not None
    assert is_credential_present(spec) is True

    app = FastAPI()
    app.include_router(router)
    app.state.config = JarvisConfig()
    response = TestClient(app).post(
        "/api/realtime/switch",
        json={"provider": "gemini-live", "persist": False},
    )

    assert response.status_code == 200
    assert response.json()["active"] == "gemini-live"
