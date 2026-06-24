"""Writers for the TTS voice/model + STT model pickers.

The TTS config is a single ``[tts]`` block (voice_de / voice_en / model) and the
STT config a single ``[stt]`` block (model) — so these set the GLOBAL value for
the active provider. config-soll/ENV sync is best-effort and a no-op here (no
repo config-soll in the tmp path), which must not break the TOML write.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core import config_writer


def _toml(tmp_path: Path) -> Path:
    p = tmp_path / "jarvis.toml"
    p.write_text(
        "[tts]\nprovider = \"gemini-flash-tts\"\nvoice_de = \"Charon\"\nvoice_en = \"Charon\"\n\n"
        "[stt]\nprovider = \"groq-api\"\nmodel = \"distil-large-v3\"\n",
        encoding="utf-8",
    )
    return p


def test_set_tts_voice_writes_both_languages(tmp_path: Path) -> None:
    p = _toml(tmp_path)
    config_writer.set_tts_voice("Kore", path=p)
    body = p.read_text(encoding="utf-8")
    assert 'voice_de = "Kore"' in body
    assert 'voice_en = "Kore"' in body


def test_set_tts_model_writes_model(tmp_path: Path) -> None:
    p = _toml(tmp_path)
    config_writer.set_tts_model("sonic-2", path=p)
    assert 'model = "sonic-2"' in p.read_text(encoding="utf-8")


def test_set_stt_model_writes_model(tmp_path: Path) -> None:
    p = _toml(tmp_path)
    config_writer.set_stt_model("whisper-large-v3", path=p)
    assert 'model = "whisper-large-v3"' in p.read_text(encoding="utf-8")


def test_set_tts_cartesia_model_writes_subtable(tmp_path: Path) -> None:
    # Cartesia reads [tts.cartesia].model_id, NOT the global [tts].model.
    p = _toml(tmp_path)
    config_writer.set_tts_cartesia_model("sonic-2", path=p)
    body = p.read_text(encoding="utf-8")
    assert "[tts.cartesia]" in body
    assert 'model_id = "sonic-2"' in body
    # Must NOT touch the global model.
    import tomllib

    data = tomllib.loads(body)
    assert data["tts"]["cartesia"]["model_id"] == "sonic-2"


def test_writers_raise_when_config_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.toml"
    with pytest.raises(FileNotFoundError):
        config_writer.set_tts_voice("Kore", path=missing)
    with pytest.raises(FileNotFoundError):
        config_writer.set_stt_model("whisper-large-v3", path=missing)
