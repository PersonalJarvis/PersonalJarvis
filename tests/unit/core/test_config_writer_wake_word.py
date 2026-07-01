"""config_writer.set_wake_word persists the custom wake word to jarvis.toml.

Toml-only by design (see the set_wake_word docstring): wake_word is NOT tracked
by the drift-guard, so a plain atomic toml write survives — and a stale soll/ENV  # i18n-allow: "soll" is the real config_writer identifier, not prose
layer would fight the documented hand-edit path. The write must preserve
comments, sibling keys, and a BOM.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from jarvis.core import config_writer

_SAMPLE = """\
[trigger]
# keep this comment
single_turn_mode = false

[trigger.wake_word]
phrase = "Hey Jarvis"
engine = "auto"
custom_model_path = ""
sensitivity = 0.5
fuzzy_match_ratio = 0.8
"""


def _write(tmp_path: Path, *, bom: bool = False) -> Path:
    f = tmp_path / "jarvis.toml"
    data = _SAMPLE
    if bom:
        f.write_bytes(b"\xef\xbb\xbf" + data.encode("utf-8"))
    else:
        f.write_text(data, encoding="utf-8")
    return f


def _load(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("﻿"):
        raw = raw[1:]
    return tomllib.loads(raw)


def test_set_wake_word_updates_phrase_and_engine(tmp_path: Path) -> None:
    f = _write(tmp_path)
    config_writer.set_wake_word("Computer", engine="stt_match", path=f)
    data = _load(f)
    assert data["trigger"]["wake_word"]["phrase"] == "Computer"
    assert data["trigger"]["wake_word"]["engine"] == "stt_match"


def test_set_wake_word_preserves_sibling_keys_and_comments(tmp_path: Path) -> None:
    f = _write(tmp_path)
    config_writer.set_wake_word("Athena", path=f)
    data = _load(f)
    # Sibling [trigger] key untouched.
    assert data["trigger"]["single_turn_mode"] is False
    # Comment preserved (tomlkit round-trip).
    assert "keep this comment" in f.read_text(encoding="utf-8")


def test_set_wake_word_updates_all_provided_fields(tmp_path: Path) -> None:
    f = _write(tmp_path)
    config_writer.set_wake_word(
        "Friday",
        engine="custom_onnx",
        custom_model_path="/models/friday.onnx",
        sensitivity=0.7,
        fuzzy_match_ratio=0.85,
        path=f,
    )
    ww = _load(f)["trigger"]["wake_word"]
    assert ww["phrase"] == "Friday"
    assert ww["engine"] == "custom_onnx"
    assert ww["custom_model_path"] == "/models/friday.onnx"
    assert ww["sensitivity"] == 0.7
    assert ww["fuzzy_match_ratio"] == 0.85


def test_set_wake_word_is_bom_safe(tmp_path: Path) -> None:
    f = _write(tmp_path, bom=True)
    config_writer.set_wake_word("Computer", path=f)
    raw = f.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "BOM must be preserved"
    assert _load(f)["trigger"]["wake_word"]["phrase"] == "Computer"


def test_set_wake_word_creates_section_if_missing(tmp_path: Path) -> None:
    f = tmp_path / "jarvis.toml"
    f.write_text("[trigger]\nsingle_turn_mode = false\n", encoding="utf-8")
    config_writer.set_wake_word("Computer", engine="stt_match", path=f)
    ww = _load(f)["trigger"]["wake_word"]
    assert ww["phrase"] == "Computer"
    assert ww["engine"] == "stt_match"
