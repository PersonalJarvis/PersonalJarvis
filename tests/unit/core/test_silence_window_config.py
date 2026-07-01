"""Range + default guards for the user-tunable voice silence window."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.core.config import SpeechConfig


def test_default_is_1500_ms() -> None:
    assert SpeechConfig().vad_silence_ms == 1500


def test_vad_constructor_default_matches_product_default() -> None:
    """Single source of truth: a bare ``SileroEndpointer()`` must fall back to the
    SAME silence window as the product default (1.5 s), never the pre-"1.5s rule"
    1.0 s value. Every real caller passes the configured value explicitly, but
    keeping the constructor default aligned stops a stale 1.0 s from creeping back
    via any future bare construction (the maintainer's mandate: the default is
    1.5 s everywhere; a single 1.0 s user setting stays a *user* choice)."""
    from jarvis.audio.vad import SileroEndpointer

    default_ms = SpeechConfig().vad_silence_ms
    assert SileroEndpointer()._silence_frames == default_ms // 32


def test_accepts_in_range_value() -> None:
    assert SpeechConfig(vad_silence_ms=2500).vad_silence_ms == 2500


def test_rejects_below_minimum() -> None:
    with pytest.raises(ValidationError):
        SpeechConfig(vad_silence_ms=400)


def test_rejects_above_maximum() -> None:
    with pytest.raises(ValidationError):
        SpeechConfig(vad_silence_ms=6000)


def test_writer_roundtrips_to_speech_table(tmp_path) -> None:
    import tomllib

    from jarvis.core import config_writer

    p = tmp_path / "jarvis.toml"
    p.write_text("", encoding="utf-8")
    config_writer.set_silence_window_ms(2500, path=p)
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["speech"]["vad_silence_ms"] == 2500


def test_writer_clamps_out_of_range(tmp_path) -> None:
    import tomllib

    from jarvis.core import config_writer

    p = tmp_path / "jarvis.toml"
    p.write_text("", encoding="utf-8")
    config_writer.set_silence_window_ms(99999, path=p)
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["speech"]["vad_silence_ms"] == 5000
