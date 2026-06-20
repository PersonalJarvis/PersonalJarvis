"""set_wake_word() removes a stale [persona] name so a legacy override can't linger."""
from __future__ import annotations

from pathlib import Path

from jarvis.core import config_writer


def test_set_wake_word_strips_legacy_persona_name(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        "[persona]\nname = \"Josef\"\n\n[trigger.wake_word]\nphrase = \"Hey Jarvis\"\n",
        encoding="utf-8",
    )

    config_writer.set_wake_word("Hey Alex", path=toml)

    text = toml.read_text(encoding="utf-8")
    assert "Hey Alex" in text
    # The stale identity override is gone; the wake word is now the single source.
    assert "Josef" not in text


def test_set_wake_word_without_persona_table_is_a_noop_strip(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger.wake_word]\nphrase = \"Hey Jarvis\"\n", encoding="utf-8")

    config_writer.set_wake_word("Hey Nova", path=toml)  # must not raise

    assert "Hey Nova" in toml.read_text(encoding="utf-8")


def test_set_wake_word_strips_legacy_persona_name_bom_file(tmp_path: Path) -> None:
    # A UTF-8 BOM (Notepad / VS Code utf8bom) must survive the strip+save.
    toml = tmp_path / "jarvis.toml"
    toml.write_bytes(
        "﻿[persona]\nname = \"Josef\"\n\n[trigger.wake_word]\nphrase = \"Hey Jarvis\"\n".encode()
    )

    config_writer.set_wake_word("Hey Nova", path=toml)

    raw = toml.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "BOM must be preserved"
    assert b"Josef" not in raw
    assert b"Hey Nova" in raw
