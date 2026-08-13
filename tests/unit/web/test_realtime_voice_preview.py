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


def test_every_preview_sampler_has_a_cataloged_realtime_provider() -> None:
    """A sampler may not exist without a catalog; offer-only providers may omit one."""
    from jarvis.brain.model_catalog import REALTIME_VOICES

    assert set(provider_routes._REALTIME_PREVIEW_SAMPLERS) <= set(REALTIME_VOICES)


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


def test_keyless_preview_does_not_require_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``requires_api_key = False`` is a generic sampler capability: a keyless
    provider's preview must not be blocked on a missing credential."""
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _pid: None)

    async def sampler(*_args, **_kwargs) -> tuple[bytes, int]:
        return b"\x01\x02" * 240, 24_000

    sampler.requires_api_key = False  # type: ignore[attr-defined]
    monkeypatch.setitem(
        provider_routes._REALTIME_PREVIEW_SAMPLERS,
        "local-realtime",
        sampler,
    )

    response = _preview(
        TestClient(_app()),
        "local-realtime",
        voice="auto",
    )

    assert response.status_code == 200


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


def test_every_api_key_realtime_provider_can_be_previewed() -> None:
    """Parity guard: a cataloged realtime provider the user pays per minute for
    must let them hear the voice BEFORE pinning it. ``grok-realtime`` shipped
    without a sampler on 2026-08-13, so its voice picker was the only one whose
    play button stayed dark. Providers without a hosted voice roster of their
    own (a self-hosted local server) are exempt.
    """
    from jarvis.brain.model_catalog import REALTIME_VOICES

    exempt = {"local-realtime"}
    missing = set(REALTIME_VOICES) - set(provider_routes._REALTIME_PREVIEW_SAMPLERS)
    assert not (missing - exempt), (
        f"cataloged realtime providers without a voice preview: {sorted(missing)}"
    )


def test_grok_preview_renders_through_the_xai_voice_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sampler must speak with xAI's OWN voice, never cross to another
    family on a quota error — a preview that plays a different provider's
    voice is worse than an error."""
    from jarvis.plugins.tts import grok_voice_tts

    built: dict[str, object] = {}

    class _FakeGrokTTS:
        def __init__(self, **kwargs: object) -> None:
            built.update(kwargs)

        async def synthesize(self, _text: str, **_kw: object):
            from jarvis.core.protocols import AudioChunk

            yield AudioChunk(pcm=b"\x03\x04" * 240, sample_rate=24_000, timestamp_ns=0)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(grok_voice_tts, "GrokVoiceTTS", _FakeGrokTTS)
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: "xai-key")
    client = TestClient(_app())
    response = _preview(client, "grok-realtime", voice="eve", model="grok-voice-latest")

    assert response.status_code == 200
    assert built["api_key"] == "xai-key"
    assert built["allow_cross_family_fallback"] is False
    assert built["allow_sapi5_fallback"] is False
