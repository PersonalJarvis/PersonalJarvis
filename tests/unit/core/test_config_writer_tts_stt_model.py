"""Writers for the TTS voice/model + STT model pickers.

The TTS config is a single ``[tts]`` block (voice_de / voice_en / model) and the
STT config a single ``[stt]`` block (model) — so these set the GLOBAL value for
the active provider. config-soll/ENV sync is best-effort and a no-op here (no  # i18n-allow: "config-soll" is the real config_writer identifier/filename, not prose
repo config-soll in the tmp path), which must not break the TOML write.  # i18n-allow: "config-soll" is the real config_writer identifier/filename, not prose
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


def test_set_stt_model_writes_all_three_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The STT model picker writes TOML + config-soll + the JARVIS__STT__MODEL ENV  # i18n-allow: "config-soll" is the real config_writer identifier/filename, not prose
    var — a stale single-word ENV override otherwise masks the TOML at boot (the
    "model is hardcoded, I can't change it" trap, forensic 2026-06-28)."""
    p = _toml(tmp_path)
    soll = tmp_path / "config-soll.json"  # i18n-allow: real config_writer filename
    soll.write_text('{"stt": {"model": "distil-large-v3"}}', encoding="utf-8")  # i18n-allow: "soll" is the real config_writer variable name
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: soll)  # i18n-allow: real config_writer identifier
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "_set_user_env_var", lambda n, v: env_calls.append((n, v))
    )

    config_writer.set_stt_model("large-v3-turbo", path=p)

    # Layer 1: TOML.
    assert 'model = "large-v3-turbo"' in p.read_text(encoding="utf-8")
    # Layer 2: config-soll.json.  # i18n-allow: real config_writer filename
    import json

    assert json.loads(soll.read_text(encoding="utf-8"))["stt"]["model"] == "large-v3-turbo"  # i18n-allow: "soll" is the real config_writer variable name
    # Layer 3: the User-scope ENV var that overrides TOML at boot.
    assert env_calls == [("JARVIS__STT__MODEL", "large-v3-turbo")]


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


def test_writers_create_config_when_missing(tmp_path: Path) -> None:
    # M1 (headless VPS): provider-switch writers create the config if absent.
    missing = tmp_path / "nope.toml"
    config_writer.set_tts_voice("Kore", path=missing)
    assert missing.exists()
    config_writer.set_stt_model("whisper-large-v3", path=missing)
