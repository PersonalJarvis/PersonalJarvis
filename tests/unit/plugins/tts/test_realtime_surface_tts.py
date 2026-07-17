"""Mode separation for the realtime surface fallback voice (2026-07-17).

A realtime session's emergency re-render (scrub-gate cancel, text-only
completion) must speak with the SESSION's provider family, resolved through
the REALTIME credential slots — not with whatever the pipeline's separately
configured ``[tts]`` provider happens to be. Live incident 2026-07-17 10:04:
a gemini-live session (voice Fenrir) aborted a readback and the re-render
spoke as "Charon @ openrouter" because the pipeline primary was
openrouter-tts. The pipeline chain stays wired as the cross-family last
resort (AD-OE6 zero-silent-drops, AP-22).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.config import override_provider_secrets
from jarvis.plugins.tts import build_realtime_surface_tts
from jarvis.plugins.tts.fallback_tts import FallbackTTS
from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS


def _cfg(voice: str = "Fenrir") -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(
            providers={"gemini-live": SimpleNamespace(voice=voice)},
        ),
        tts=SimpleNamespace(
            language_code="de-DE",
            allow_sapi5_fallback=False,
            streaming=False,
        ),
    )


class _PipelineTTS:
    name = "pipeline-chain"

    async def synthesize(self, text, voice=None, language_code=None):
        if False:  # pragma: no cover — makes this an async iterator
            yield None


def test_gemini_live_builds_same_family_tts_with_session_voice() -> None:
    pipeline_tts = _PipelineTTS()
    with override_provider_secrets({"gemini-live": "rt-scoped-key"}):
        tts = build_realtime_surface_tts(_cfg(), "gemini-live", pipeline_tts)
    assert isinstance(tts, FallbackTTS)
    assert isinstance(tts.primary, GeminiFlashTTS)
    # The session voice carries over verbatim (shared prebuilt-voice catalog).
    assert tts.primary._default_voice == "Fenrir"
    # The realtime-resolved key is injected — never left to the generic
    # environment lookup, so realtime-scoped keys stay out of pipeline scope.
    assert tts.primary._resolve_api_key() == "rt-scoped-key"
    # The pipeline chain remains the cross-family last resort.
    assert tts.fallback is pipeline_tts


def test_unknown_session_voice_falls_back_to_family_default() -> None:
    pipeline_tts = _PipelineTTS()
    with override_provider_secrets({"gemini-live": "rt-scoped-key"}):
        tts = build_realtime_surface_tts(
            _cfg(voice="cedar"), "gemini-live", pipeline_tts
        )
    assert isinstance(tts, FallbackTTS)
    assert tts.primary._default_voice == "Charon"


def test_keyless_realtime_provider_keeps_pipeline_chain() -> None:
    pipeline_tts = _PipelineTTS()
    with override_provider_secrets({"gemini-live": None}):
        tts = build_realtime_surface_tts(_cfg(), "gemini-live", pipeline_tts)
    assert tts is pipeline_tts


def test_realtime_family_without_tts_sibling_keeps_pipeline_chain() -> None:
    pipeline_tts = _PipelineTTS()
    with override_provider_secrets({"openai-realtime": "some-key"}):
        tts = build_realtime_surface_tts(_cfg(), "openai-realtime", pipeline_tts)
    assert tts is pipeline_tts


def test_empty_provider_keeps_pipeline_chain() -> None:
    pipeline_tts = _PipelineTTS()
    assert build_realtime_surface_tts(_cfg(), "", pipeline_tts) is pipeline_tts


def test_resolution_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline_tts = _PipelineTTS()

    def _boom(_provider: str) -> str:
        raise RuntimeError("keyring exploded")

    monkeypatch.setattr("jarvis.core.config.get_provider_secret", _boom)
    tts = build_realtime_surface_tts(_cfg(), "gemini-live", pipeline_tts)
    assert tts is pipeline_tts


def test_injected_api_key_wins_over_environment_lookup() -> None:
    tts = GeminiFlashTTS(api_key="explicit-key")
    assert tts._resolve_api_key() == "explicit-key"
