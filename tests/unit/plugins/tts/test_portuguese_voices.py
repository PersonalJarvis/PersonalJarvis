"""A European Portuguese turn ("pt" / "pt-PT") speaks with a Portuguese voice.

Every TTS family that picks a voice or a language code per reply language must
map ``pt`` to a Portuguese voice instead of falling through to the German or
English default. No network: voice resolution and payload normalisation are
pure, and the factory test only needs a fake credential lookup.
"""
from __future__ import annotations

import pytest

import jarvis.core.config as cfg
import jarvis.plugins.tts as tts_pkg
from jarvis.core.config import TTSConfig
from jarvis.plugins.tts import curated_catalog as cc
from jarvis.plugins.tts.cartesia_tts import DEFAULT_VOICE_ID_PT, CartesiaTTS
from jarvis.plugins.tts.cartesia_tts import _normalize_language as cartesia_lang
from jarvis.plugins.tts.gemini_flash_tts import _sapi5_voice_preferences
from jarvis.plugins.tts.grok_voice_tts import _normalize_language as grok_lang
from jarvis.plugins.tts.inworld_tts import DEFAULT_VOICE_PT, InworldTTS
from jarvis.plugins.tts.inworld_tts import _normalize_language as inworld_lang


def _keys(*env_names: str):
    have = set(env_names)

    def fake(candidates: tuple[tuple[str, str], ...]) -> str | None:
        for _keyring, env in candidates:
            if env in have:
                return "KEY"
        return None

    return fake


# ---- Inworld ---------------------------------------------------------------


def test_inworld_pt_turn_picks_the_portuguese_default_voice() -> None:
    tts = InworldTTS()
    assert tts._resolve_voice("Olá.", None, "pt-PT") == DEFAULT_VOICE_PT
    assert DEFAULT_VOICE_PT == "Leonor"
    assert inworld_lang("pt") == "pt-PT"


def test_inworld_pt_voice_is_configurable() -> None:
    tts = InworldTTS(default_voice_pt="Heitor")
    assert tts._resolve_voice("Olá.", None, "pt") == "Heitor"
    assert tts.list_voices("pt-PT") == ["Heitor"]
    assert "Heitor" in tts.list_voices()


def test_inworld_factory_reads_voice_pt_from_subtable(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_secret_any", _keys("INWORLD_API_KEY"))
    built = tts_pkg.build_tts_from_config(
        TTSConfig(provider="inworld", inworld={"voice_pt": "Madalena"})
    )
    assert isinstance(built, InworldTTS)
    assert built._resolve_voice("Olá.", None, "pt-PT") == "Madalena"


# ---- Cartesia --------------------------------------------------------------


def test_cartesia_pt_turn_picks_the_portuguese_voice_and_language() -> None:
    tts = CartesiaTTS()
    assert tts._resolve_voice("Olá.", None, "pt-PT") == DEFAULT_VOICE_ID_PT
    assert cartesia_lang("pt-PT") == "pt"


def test_cartesia_factory_reads_voice_id_pt_from_subtable(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_secret_any", _keys("CARTESIA_API_KEY"))
    built = tts_pkg.build_tts_from_config(
        TTSConfig(provider="cartesia", cartesia={"voice_id_pt": "PT-PT-UUID"})
    )
    assert isinstance(built, CartesiaTTS)
    assert built._resolve_voice("Olá.", None, "pt-PT") == "PT-PT-UUID"
    assert built.list_voices("pt") == ["PT-PT-UUID"]


# ---- Grok + SAPI5 ----------------------------------------------------------


def test_grok_keeps_the_european_subtag() -> None:
    assert grok_lang("pt-PT") == "pt-pt"


@pytest.mark.parametrize(
    ("code", "first"),
    [("pt-PT", "Portugal"), ("pt", "Portugal"), ("es-ES", "Spain"), ("de-DE", "German")],
)
def test_sapi5_prefers_the_regional_voice(code: str, first: str) -> None:
    prefs = _sapi5_voice_preferences(code)
    assert prefs[0] == first
    assert prefs[-1] == "English"


def test_sapi5_unknown_language_uses_english() -> None:
    assert _sapi5_voice_preferences(None) == ("English",)


# ---- Curated catalog -------------------------------------------------------


@pytest.mark.parametrize(
    ("family", "model_id"),
    [
        ("inworld", "inworld-tts-2"),
        ("cartesia", "sonic-3.5"),
        ("gemini-flash-tts", "gemini-3.1-flash-tts-preview"),
        ("elevenlabs", "eleven_flash_v2_5"),
        ("grok-voice", "grok-voice-tts-1.0"),
    ],
)
def test_documented_portuguese_models_list_pt(family: str, model_id: str) -> None:
    models = cc.allowed_models(family=family, language="pt-PT")
    assert any(m.model_id == model_id for m in models)


def test_inworld_curates_a_european_portuguese_voice() -> None:
    pt = cc.allowed_voices("inworld", "inworld-tts-2", language="pt")
    assert any(v.id == "Leonor" and v.language == "pt" for v in pt)
    assert cc.voice_gender("Leonor") == cc.FEMININE
    assert cc.voice_gender("Heitor") == cc.MASCULINE


# ---- Piper (local) ---------------------------------------------------------


def test_piper_ships_a_european_portuguese_default_voice() -> None:
    from jarvis.speech.local_models import PIPER_DEFAULT_VOICES, SHERPA_BUNDLES

    bundle = SHERPA_BUNDLES["vits-piper-pt_PT-tugao-medium"]
    assert bundle.language == "pt"
    assert bundle.required_files[0] == "pt_PT-tugao-medium.onnx"
    assert bundle.archive_url is not None
    assert bundle.archive_url.endswith("/vits-piper-pt_PT-tugao-medium.tar.bz2")
    assert "vits-piper-pt_PT-tugao-medium" in PIPER_DEFAULT_VOICES


def test_piper_download_size_covers_every_default_voice() -> None:
    """Four ~67 MB voices (de/en/es/pt): the card and the docs say 270 MB."""
    from pathlib import Path

    from jarvis.speech.local_models import PIPER_DEFAULT_VOICES, get_local_provider

    entry = get_local_provider("piper-local")
    assert entry is not None
    assert entry.bundles == PIPER_DEFAULT_VOICES
    assert len(PIPER_DEFAULT_VOICES) == 4
    assert entry.download_size == "about 270 MB"
    doc = (
        Path(__file__).resolve().parents[4]
        / "docs/product/personalize-and-connect/languages-and-voices.md"
    )
    assert "downloads about 270 MB" in doc.read_text(encoding="utf-8")


def test_piper_voice_is_on_the_writer_allowlist_and_picker() -> None:
    from jarvis.brain.model_catalog import TTS_CATALOG
    from jarvis.core.config_writer import _VOICES_FOR_PROVIDER

    assert "vits-piper-pt_PT-tugao-medium" in _VOICES_FOR_PROVIDER["piper-local"]
    _field, models = TTS_CATALOG["piper-local"]
    assert any(m.id == "vits-piper-pt_PT-tugao-medium" for m in models)
