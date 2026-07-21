"""STRICT mode separation for the realtime surface fallback voice (2026-07-17).

Realtime and Pipeline are independent modes (maintainer mandate 2026-07-17):
each must work with only its own API keys, and neither may fall back onto the
other's providers or credentials — not even as a last resort. Live incident
2026-07-17 10:04: a gemini-live session (voice Fenrir) aborted a readback and
the re-render spoke as "Charon @ openrouter" because the pipeline `[tts]`
primary was openrouter-tts.

Forward guard: the emergency re-render resolves ONLY a same-family TTS keyed
through the realtime credential slots; no candidate → ``None`` (text-only).
Reverse guard: pipeline TTS credential resolution must never see a
realtime-scoped key slot.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.config import (
    PROVIDER_SECRET_CANDIDATES,
    override_provider_secrets,
)
from jarvis.plugins.tts import (
    _TTS_SECRET_CANDIDATES,
    _tts_has_credential,
    build_realtime_surface_tts,
)
from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

_REALTIME_PROVIDER_IDS = ("gemini-live", "openai-realtime")


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


# ---------------------------------------------------------------------------
# Forward direction: realtime emergency voice stays realtime-scoped.
# ---------------------------------------------------------------------------


def test_gemini_live_builds_same_family_tts_with_session_voice() -> None:
    with override_provider_secrets({"gemini-live": "rt-scoped-key"}):
        tts = build_realtime_surface_tts(_cfg(), "gemini-live")
    assert isinstance(tts, GeminiFlashTTS)
    # The session voice carries over verbatim (shared prebuilt-voice catalog).
    assert tts._default_voice == "Fenrir"
    # The realtime-resolved key is injected — never left to the generic
    # environment lookup, so realtime-scoped keys stay out of pipeline scope.
    assert tts._resolve_api_key() == "rt-scoped-key"


def test_unknown_session_voice_falls_back_to_family_default() -> None:
    # "cedar" is not a Gemini voice but carries a curated MASCULINE profile,
    # and Charon is the family's first curated masculine voice — the
    # continuity pick and the historical default coincide here.
    with override_provider_secrets({"gemini-live": "rt-scoped-key"}):
        tts = build_realtime_surface_tts(_cfg(voice="cedar"), "gemini-live")
    assert isinstance(tts, GeminiFlashTTS)
    assert tts._default_voice == "Charon"


def test_feminine_session_voice_keeps_a_feminine_fallback() -> None:
    """BUG-089: the surface fallback keeps the session's voice PROFILE.

    A feminine live voice hard-flipping to masculine Charon reads as a
    second assistant joining the call (Mac live test 2026-07-18). Pinned via
    the curated gender register, never a hardcoded voice id.
    """
    from jarvis.plugins.tts import _GEMINI_VOICES
    from jarvis.plugins.tts.curated_catalog import FEMININE, voice_gender

    with override_provider_secrets({"gemini-live": "rt-scoped-key"}):
        tts = build_realtime_surface_tts(_cfg(voice="marin"), "gemini-live")
    assert isinstance(tts, GeminiFlashTTS)
    assert tts._default_voice in _GEMINI_VOICES
    assert voice_gender(tts._default_voice) == FEMININE


def test_untagged_unknown_voice_still_falls_back_to_charon() -> None:
    with override_provider_secrets({"gemini-live": "rt-scoped-key"}):
        tts = build_realtime_surface_tts(
            _cfg(voice="definitely-not-a-voice"), "gemini-live"
        )
    assert isinstance(tts, GeminiFlashTTS)
    assert tts._default_voice == "Charon"


def test_surface_fallback_renders_one_take_with_the_config_drift_knobs() -> None:
    """BUG-090: Gemini TTS is generative — one generation per SENTENCE let the
    2026-07-19 fallback re-roll the delivery mid-answer and flip the voice's
    gender past the Fenrir pin. The emergency re-render is always ONE take and
    inherits the [tts] drift knobs the pipeline instance already honors."""
    cfg = _cfg()
    cfg.tts.seed = 7
    cfg.tts.temperature = 0.7
    with override_provider_secrets({"gemini-live": "rt-scoped-key"}):
        tts = build_realtime_surface_tts(cfg, "gemini-live")
    assert isinstance(tts, GeminiFlashTTS)
    assert tts._chunk_by_sentence is False
    assert tts._seed == 7
    assert tts._temperature == 0.7


def test_surface_fallback_is_one_take_even_without_configured_knobs() -> None:
    """A fresh install without the [tts] drift knobs still gets the one-take
    profile — voice identity is the fallback's entire purpose (§3: never
    assume the maintainer's config)."""
    with override_provider_secrets({"gemini-live": "rt-scoped-key"}):
        tts = build_realtime_surface_tts(_cfg(), "gemini-live")
    assert isinstance(tts, GeminiFlashTTS)
    assert tts._chunk_by_sentence is False
    assert tts._seed is None
    assert tts._temperature is None


def test_keyless_realtime_provider_yields_no_surface_tts() -> None:
    with override_provider_secrets({"gemini-live": None}):
        assert build_realtime_surface_tts(_cfg(), "gemini-live") is None


def test_realtime_family_without_tts_sibling_yields_no_surface_tts() -> None:
    # A key alone is not enough: without a same-family TTS sibling the
    # emergency re-render stays text-only — never the pipeline voice.
    with override_provider_secrets({"openai-realtime": "some-key"}):
        assert build_realtime_surface_tts(_cfg(), "openai-realtime") is None


def test_empty_provider_yields_no_surface_tts() -> None:
    assert build_realtime_surface_tts(_cfg(), "") is None


def test_resolution_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_provider: str) -> str:
        raise RuntimeError("keyring exploded")

    monkeypatch.setattr("jarvis.core.config.get_provider_secret", _boom)
    assert build_realtime_surface_tts(_cfg(), "gemini-live") is None


def test_injected_api_key_wins_over_environment_lookup() -> None:
    tts = GeminiFlashTTS(api_key="explicit-key")
    assert tts._resolve_api_key() == "explicit-key"


# ---------------------------------------------------------------------------
# Reverse direction: pipeline resolution never sees realtime key slots.
# ---------------------------------------------------------------------------


def test_pipeline_tts_credential_map_lists_no_realtime_slots() -> None:
    """Static guard: the pipeline TTS key-aware factory consults
    ``_TTS_SECRET_CANDIDATES`` — a realtime-scoped slot appearing there would
    silently let pipeline mode spend realtime credentials."""
    for family, candidates in _TTS_SECRET_CANDIDATES.items():
        for keyring_key, env_var in candidates:
            assert not keyring_key.startswith("realtime_"), (
                f"pipeline TTS family {family!r} lists realtime slot "
                f"{keyring_key!r}"
            )
            assert "REALTIME" not in (env_var or ""), (
                f"pipeline TTS family {family!r} lists realtime env var "
                f"{env_var!r}"
            )


def test_realtime_slots_are_only_trailing_fallbacks_outside_realtime_ids() -> None:
    """Static guard: outside the realtime provider ids, a dedicated realtime
    slot may appear ONLY as the last (trailing) candidate of its family.

    The trailing cross-read is deliberate (2026-07-21 Mac forensic: an install
    whose only key came from the Realtime card had every delegated Brain turn
    brick on "Kein Brain-Key gefunden"),  # i18n-allow: quoted log diagnostic
    but it must never outrank a generic
    family key — and pipeline TTS keeps its own realtime-free candidate table
    (guarded above)."""
    for provider, candidates in PROVIDER_SECRET_CANDIDATES.items():
        if provider in _REALTIME_PROVIDER_IDS:
            continue
        for position, (keyring_key, _env) in enumerate(candidates):
            if keyring_key.startswith("realtime_"):
                assert position == len(candidates) - 1, (
                    f"realtime slot {keyring_key!r} must be the LAST candidate "
                    f"of provider {provider!r}, found at index {position}"
                )


def test_pipeline_tts_cannot_see_a_realtime_only_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioral guard: with ONLY the dedicated realtime Gemini key present,
    the pipeline gemini-flash-tts family reads as keyless — pipeline mode
    must not light up on a realtime-only install."""

    def _secret(name: str, env_fallback: str | None = None, **_kw: object):
        return "rt-only-key" if name == "realtime_gemini_api_key" else None

    monkeypatch.setattr("jarvis.core.config.get_secret", _secret)
    tts_cfg = SimpleNamespace(use_vertex=False)
    assert _tts_has_credential("gemini-flash-tts", tts_cfg) is False
