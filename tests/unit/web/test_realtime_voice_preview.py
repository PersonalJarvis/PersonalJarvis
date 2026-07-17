"""Contracts for POST /api/providers/{id}/realtime-voice-preview.

The route lets the user HEAR a realtime voice before pinning it. These tests
pin the validation surface (tier, catalog, credential) and the response
contract (playable WAV, clean 4xx/5xx on failure) with faked samplers — the
provider transports themselves are exercised live, not here.
"""

from __future__ import annotations

import io
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core import config as cfg_mod
from jarvis.core.config import JarvisConfig
from jarvis.ui.web import provider_routes
from jarvis.ui.web.provider_routes import router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.config = JarvisConfig()
    return app


def _preview(client: TestClient, provider: str, **body: str):
    return client.post(
        f"/api/providers/{provider}/realtime-voice-preview", json=body
    )


async def _silent_sampler(*_args, **_kwargs) -> tuple[bytes, int]:
    return b"\x01\x02" * 240, 24_000


def test_every_cataloged_realtime_provider_has_a_preview_sampler() -> None:
    """Adding a realtime provider means adding its sampler too (parity)."""
    from jarvis.brain.model_catalog import REALTIME_VOICES

    assert set(REALTIME_VOICES) == set(provider_routes._REALTIME_PREVIEW_SAMPLERS)


def test_unknown_provider_is_404() -> None:
    response = _preview(TestClient(_app()), "no-such-provider", voice="alloy")
    assert response.status_code == 404


def test_non_realtime_tier_is_400() -> None:
    response = _preview(TestClient(_app()), "openai", voice="alloy")
    assert response.status_code == 400


def test_missing_voice_is_400() -> None:
    response = _preview(TestClient(_app()), "openai-realtime", voice="")
    assert response.status_code == 400


def test_uncatalogued_voice_is_422() -> None:
    response = _preview(
        TestClient(_app()), "openai-realtime", voice="not-a-voice"
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_realtime_voice"


def test_uncatalogued_model_is_422() -> None:
    response = _preview(
        TestClient(_app()),
        "openai-realtime",
        voice="alloy",
        model="not-a-model",
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_realtime_model"


def test_missing_credential_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _pid: None)
    response = _preview(TestClient(_app()), "openai-realtime", voice="alloy")
    assert response.status_code == 409
    assert "credentials" in response.json()["detail"]


def test_happy_path_returns_playable_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _pid: "sk-test")
    calls: list[dict[str, str]] = []

    async def sampler(
        api_key: str, *, model: str, voice: str, text: str, language: str
    ) -> tuple[bytes, int]:
        calls.append(
            {
                "api_key": api_key,
                "model": model,
                "voice": voice,
                "text": text,
                "language": language,
            }
        )
        return b"\x01\x02" * 240, 24_000

    monkeypatch.setitem(
        provider_routes._REALTIME_PREVIEW_SAMPLERS, "gemini-live", sampler
    )

    response = _preview(
        TestClient(_app()), "gemini-live", voice="Puck", language="de"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    with wave.open(io.BytesIO(response.content), "rb") as wav:
        assert wav.getframerate() == 24_000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 240
    assert calls == [
        {
            "api_key": "sk-test",
            "model": "",
            "voice": "Puck",
            # The German sample sentence — the language pin must reach the
            # sampler as both the text AND the resolved language code.
            "text": provider_routes._TTS_PREVIEW_SAMPLES["de"],
            "language": "de",
        }
    ]


def test_unknown_sample_language_falls_back_to_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _pid: "sk-test")
    seen: list[str] = []

    async def sampler(
        api_key: str, *, model: str, voice: str, text: str, language: str
    ) -> tuple[bytes, int]:
        seen.append(text)
        return b"\x00\x01", 24_000

    monkeypatch.setitem(
        provider_routes._REALTIME_PREVIEW_SAMPLERS, "gemini-live", sampler
    )

    response = _preview(
        TestClient(_app()), "gemini-live", voice="Puck", language="fr"
    )

    assert response.status_code == 200
    assert seen == [provider_routes._TTS_PREVIEW_SAMPLES["en"]]


def test_sampler_failure_is_clean_502(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _pid: "sk-test")

    async def sampler(*_args, **_kwargs) -> tuple[bytes, int]:
        raise RuntimeError("quota exhausted")

    monkeypatch.setitem(
        provider_routes._REALTIME_PREVIEW_SAMPLERS, "openai-realtime", sampler
    )

    response = _preview(TestClient(_app()), "openai-realtime", voice="marin")
    assert response.status_code == 502
    assert "quota exhausted" in response.json()["detail"]


def test_empty_audio_is_clean_502(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _pid: "sk-test")

    async def sampler(*_args, **_kwargs) -> tuple[bytes, int]:
        return b"", 24_000

    monkeypatch.setitem(
        provider_routes._REALTIME_PREVIEW_SAMPLERS, "gemini-live", sampler
    )

    response = _preview(TestClient(_app()), "gemini-live", voice="Charon")
    assert response.status_code == 502
    assert "no audio" in response.json()["detail"]


def test_hung_sampler_times_out_as_502(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _pid: "sk-test")
    monkeypatch.setattr(provider_routes, "_REALTIME_PREVIEW_TIMEOUT_S", 0.05)

    async def sampler(*_args, **_kwargs) -> tuple[bytes, int]:
        await asyncio.sleep(5.0)
        return b"\x00\x01", 24_000

    monkeypatch.setitem(
        provider_routes._REALTIME_PREVIEW_SAMPLERS, "openai-realtime", sampler
    )

    response = _preview(TestClient(_app()), "openai-realtime", voice="cedar")
    assert response.status_code == 502
    assert "timed out" in response.json()["detail"]


def test_marin_and_cedar_are_in_the_openai_catalog() -> None:
    """The two Realtime-API-only voices must stay previewable (the whole
    reason the OpenAI sampler runs through a realtime session)."""
    from jarvis.brain.model_catalog import REALTIME_VOICES

    ids = {option.id for option in REALTIME_VOICES["openai-realtime"]}
    assert {"marin", "cedar"} <= ids
