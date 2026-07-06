"""resolve_wake_plan: the any-word vosk_kws engine slots into the chain.

Chain contract (design spec 2026-07-05): custom_onnx (matching file) →
pretrained OWW (known phrase) → **vosk_kws (any phrase, per-language model)**
→ stt_match (fallback) → none/hotkey. vosk_kws must never require local
Whisper, and a missing vosk package or model falls through gracefully.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.speech.wake_constants import resolve_vosk_model_path
from jarvis.speech.wake_phrase import resolve_wake_plan


def _cfg(phrase: str = "Hey Nova", engine: str = "auto", custom: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        phrase=phrase,
        engine=engine,
        custom_model_path=custom,
        sensitivity=0.5,
        fuzzy_match_ratio=0.8,
    )


@pytest.fixture()
def vosk_model_dir(tmp_path, monkeypatch) -> Path:
    """A minimal on-disk layout resolve_vosk_model_path accepts (am/ subdir)."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    model = tmp_path / "wake_models" / "vosk" / "de" / "vosk-model-small-de-0.15"
    (model / "am").mkdir(parents=True)
    return model


def test_resolve_vosk_model_path_finds_language_and_auto(vosk_model_dir) -> None:
    assert resolve_vosk_model_path("de") == str(vosk_model_dir)
    assert resolve_vosk_model_path("de-DE") == str(vosk_model_dir)
    # auto/None fall back to the first installed language folder
    assert resolve_vosk_model_path("auto") == str(vosk_model_dir)
    assert resolve_vosk_model_path(None) == str(vosk_model_dir)
    # a language without a model resolves to nothing
    assert resolve_vosk_model_path("es") is None


def test_auto_picks_vosk_for_arbitrary_phrase(vosk_model_dir) -> None:
    plan = resolve_wake_plan(
        _cfg(), local_whisper_available=True, language="de", vosk_available=True
    )
    assert plan.engine == "vosk_kws"
    assert plan.vosk_model_path == str(vosk_model_dir)
    assert plan.needs_local_whisper is False
    assert plan.wake_available is True
    assert plan.verify_prefix is False  # the provider's own confirm handles it


def test_known_pretrained_phrase_keeps_openwakeword(vosk_model_dir) -> None:
    # "Hey Jarvis" has a bundled instant OWW model — vosk must not hijack it.
    plan = resolve_wake_plan(
        _cfg(phrase="Hey Jarvis"),
        local_whisper_available=True,
        language="de",
        vosk_available=True,
    )
    assert plan.engine == "openwakeword"


def test_explicit_vosk_engine_forces_vosk(vosk_model_dir) -> None:
    plan = resolve_wake_plan(
        _cfg(phrase="Hey Jarvis", engine="vosk_kws"),
        local_whisper_available=True,
        language="de",
        vosk_available=True,
    )
    assert plan.engine == "vosk_kws"


def test_no_vosk_package_falls_back_to_stt_match(vosk_model_dir) -> None:
    plan = resolve_wake_plan(
        _cfg(), local_whisper_available=True, language="de", vosk_available=False
    )
    assert plan.engine == "stt_match"


def test_no_model_for_language_falls_back_to_stt_match(vosk_model_dir) -> None:
    plan = resolve_wake_plan(
        _cfg(), local_whisper_available=True, language="es", vosk_available=True
    )
    assert plan.engine == "stt_match"


def test_stale_custom_model_resolves_to_vosk(vosk_model_dir, tmp_path) -> None:
    # A leftover hey_nico.onnx must not keep the NEW phrase on the weak path.
    stale = tmp_path / "hey_nico.onnx"
    stale.write_bytes(b"onnx")
    plan = resolve_wake_plan(
        _cfg(custom=str(stale)),
        local_whisper_available=True,
        language="de",
        vosk_available=True,
    )
    assert plan.engine == "vosk_kws"


def test_matching_custom_model_still_wins(vosk_model_dir, tmp_path) -> None:
    own = tmp_path / "hey_nova.onnx"
    own.write_bytes(b"onnx")
    plan = resolve_wake_plan(
        _cfg(custom=str(own)),
        local_whisper_available=True,
        language="de",
        vosk_available=True,
    )
    assert plan.engine == "custom_onnx"


def test_no_vosk_no_whisper_is_honest_wake_off(vosk_model_dir) -> None:
    plan = resolve_wake_plan(
        _cfg(),
        local_whisper_available=False,
        language="es",  # no model for es in the fixture
        vosk_available=True,
    )
    assert plan.engine == "none"
    assert plan.wake_available is False
