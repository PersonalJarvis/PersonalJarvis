"""A config write must never silently drop settings nobody asked to change.

Field evidence (2026-08-03): the maintainer's ``jarvis.toml`` went from 53 KB —
a 2026-07-17 backup still carries ``[brain.realtime] provider = "gemini-live"``
— down to 183 bytes holding only ``[trigger]``, ``[trigger.wake_word]`` and
``[dictation]``, i.e. exactly the keys boot migrations and the wake/dictation
writers happen to touch. The realtime provider pin was gone, which silently
turned the ChatGPT-subscription transport into a provider no wake word can even
select, and nothing anywhere had logged a word about it.

Every writer in ``config_writer`` is a read-modify-write over one key, so none
of them can produce that from a populated file. These tests pin the two
defences that make the class of loss impossible-to-miss instead of invisible:
a refused write when top-level entries would disappear, and a warning when an
empty configuration file is created.
"""
from __future__ import annotations

import logging

import pytest
import tomlkit

from jarvis.core import config_writer as cw

_POPULATED = """\
[trigger]
wake_word_enabled = true

[brain]
primary = "gemini"

[brain.realtime]
provider = "codex-subscription-realtime"

[tts]
provider = "grok-voice"
"""


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_ordinary_writers_preserve_every_other_section(tmp_path):
    """The realtime pin survives writes that have nothing to do with it."""
    config = tmp_path / "jarvis.toml"
    _write(config, _POPULATED)

    cw.set_wake_word_enabled(False, path=config)
    cw.set_dictation_setting("mode", "hold", path=config)
    cw.set_brain_primary("openrouter", path=config)

    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert doc["brain"]["realtime"]["provider"] == "codex-subscription-realtime"
    assert doc["tts"]["provider"] == "grok-voice"


def test_set_realtime_provider_preserves_unrelated_sections(tmp_path):
    config = tmp_path / "jarvis.toml"
    _write(config, _POPULATED)

    cw.set_realtime_provider("gemini-live", path=config)

    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert doc["brain"]["realtime"]["provider"] == "gemini-live"
    assert doc["brain"]["primary"] == "gemini"
    assert doc["tts"]["provider"] == "grok-voice"
    assert doc["trigger"]["wake_word_enabled"] is True


def test_atomic_write_refuses_to_drop_a_top_level_section(tmp_path):
    """The exact shape of the field loss: a populated file reduced to a stub."""
    config = tmp_path / "jarvis.toml"
    _write(config, _POPULATED)

    stub = '[trigger]\nwake_word_enabled = true\n'
    with pytest.raises(cw.ConfigSectionLossError) as excinfo:
        cw._atomic_write(config, stub)

    message = str(excinfo.value)
    assert "brain" in message
    assert "tts" in message
    # The file on disk is untouched — a refused write is not a partial write.
    assert config.read_text(encoding="utf-8") == _POPULATED


def test_atomic_write_refuses_to_empty_a_populated_config(tmp_path):
    config = tmp_path / "jarvis.toml"
    _write(config, _POPULATED)

    with pytest.raises(cw.ConfigSectionLossError):
        cw._atomic_write(config, "")

    assert config.read_text(encoding="utf-8") == _POPULATED


def test_atomic_write_allows_adding_and_changing_entries(tmp_path):
    config = tmp_path / "jarvis.toml"
    _write(config, _POPULATED)

    grown = _POPULATED + '\n[dictation]\nmode = "hold"\n'
    cw._atomic_write(config, grown)

    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert doc["dictation"]["mode"] == "hold"
    assert doc["brain"]["realtime"]["provider"] == "codex-subscription-realtime"


def test_nested_removals_stay_legal(tmp_path):
    """The worker-tier migration and the persona heal must keep working.

    Both delete something NESTED (``[brain.sub_jarvis]`` / ``[persona] name``),
    which is a legitimate edit; only a vanishing TOP-LEVEL entry is the bug.
    """
    config = tmp_path / "jarvis.toml"
    _write(
        config,
        '[persona]\nname = "Old"\n\n'
        '[brain]\nprimary = "gemini"\n\n'
        '[brain.sub_jarvis]\nprovider = "claude-api"\nfallback_provider = "openai"\n',
    )

    assert cw.migrate_worker_tier_table(path=config) is True
    cw._strip_persona_name(config)

    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert "sub_jarvis" not in doc["brain"]
    assert doc["brain"]["worker"]["fallback_provider"] == "openai"
    assert "name" not in doc["persona"]
    # The top-level tables themselves survived.
    assert set(doc.keys()) == {"persona", "brain"}


def test_unparsable_predecessor_does_not_veto_a_repair(tmp_path):
    """A broken file is exactly what an in-app repair must be able to replace."""
    config = tmp_path / "jarvis.toml"
    _write(config, "this is not = = valid toml [[[")

    cw._atomic_write(config, '[brain]\nprimary = "gemini"\n')

    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert doc["brain"]["primary"] == "gemini"


def test_creating_an_empty_config_is_reported(tmp_path, caplog):
    """Creating the file is where a configuration can vanish without a trace."""
    config = tmp_path / "nested" / "jarvis.toml"

    with caplog.at_level(logging.WARNING, logger="jarvis.core.config_writer"):
        cw._ensure_writable_config_path(config)

    assert config.exists()
    assert any(
        "Created a NEW empty configuration file" in record.message
        for record in caplog.records
    )


def test_existing_config_is_not_reported_as_created(tmp_path, caplog):
    config = tmp_path / "jarvis.toml"
    _write(config, _POPULATED)

    with caplog.at_level(logging.WARNING, logger="jarvis.core.config_writer"):
        cw._ensure_writable_config_path(config)

    assert not caplog.records
