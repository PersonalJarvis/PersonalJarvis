"""Range + default guards for the user-tunable voice silence window."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.core.config import SpeechConfig


def test_default_is_automatic() -> None:
    """0 = automatic: every voice engine keeps its own factory turn detection.

    A fixed window layered on a realtime provider that already endpoints
    natively made every finished sentence wait twice, and the extra wait was
    audible against the vendor's own client (maintainer 2026-08-23). Patience
    is now something a user asks for, not something the product assumes.
    """
    assert SpeechConfig().vad_silence_ms == 0


def test_automatic_resolves_to_the_pipelines_own_window() -> None:
    """A LOCAL VAD has no vendor default to inherit, so automatic = 1.5 s.

    Single source of truth for that resolution: a bare ``SileroEndpointer()``
    must fall back to the SAME window the pipeline resolves "automatic" to,
    never the pre-"1.5s rule" 1.0 s value. Every real caller passes the
    resolved value explicitly, but keeping the constructor default aligned
    stops a stale 1.0 s from creeping back via a bare construction.
    """
    from jarvis.audio.vad import SileroEndpointer
    from jarvis.speech.pipeline import _local_silence_window_ms

    resolved = _local_silence_window_ms(SpeechConfig().vad_silence_ms)
    assert resolved == 1500
    assert SileroEndpointer()._silence_frames == resolved // 32


def test_accepts_in_range_value() -> None:
    assert SpeechConfig(vad_silence_ms=2500).vad_silence_ms == 2500


def test_a_window_below_the_floor_is_raised_to_it() -> None:
    """400 ms is not a patience setting — it would cut a talker off between
    words — but a typo must never make the app unbootable, so it is raised to
    the floor rather than rejected. Only 0 (automatic) lives below it."""
    assert SpeechConfig(vad_silence_ms=400).vad_silence_ms == 500
    assert SpeechConfig(vad_silence_ms=0).vad_silence_ms == 0


def test_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        SpeechConfig(vad_silence_ms=-1)


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


def test_writer_keeps_automatic_as_zero(tmp_path) -> None:
    """0 is the automatic setting, not a 0 ms window — it must survive the clamp."""
    import tomllib

    from jarvis.core import config_writer

    p = tmp_path / "jarvis.toml"
    p.write_text("", encoding="utf-8")
    config_writer.set_silence_window_ms(0, path=p)
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["speech"]["vad_silence_ms"] == 0
