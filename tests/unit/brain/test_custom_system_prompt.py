"""Custom (user-editable) system prompt override.

Feature 2026-06-14: the user can replace the packaged JARVIS persona with their
own Markdown in the Settings UI, and reset back to the default with one click.
The override lives in a sidecar file (``data/custom_system_prompt.md``) so the
shipped ``JARVIS_PERSONA.md`` is never mutated and "reset to default" is just a
file delete. These lock the persona-loader contract the routes + UI depend on.
"""
from __future__ import annotations

import pytest

import jarvis.core.config as core_config
from jarvis.brain import persona_loader


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the override file at a throwaway dir so tests never touch real data."""
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)
    return tmp_path


def test_effective_prompt_is_default_when_no_custom_file() -> None:
    assert not persona_loader.has_custom_prompt()
    assert persona_loader.load_effective_persona_prompt() == persona_loader.default_persona_prompt()


def test_default_persona_prompt_is_the_packaged_block() -> None:
    default = persona_loader.default_persona_prompt()
    assert default == persona_loader.load_persona_prompt()
    # Sanity: it is the real (name-neutral) voice persona, not an empty fallback.
    assert "voice companion" in default


def test_save_custom_then_effective_returns_custom() -> None:
    custom = "You are MAX, a terse pirate assistant."
    persona_loader.save_custom_prompt(custom)
    assert persona_loader.has_custom_prompt()
    assert persona_loader.load_effective_persona_prompt() == custom


def test_reset_deletes_custom_and_falls_back_to_default() -> None:
    persona_loader.save_custom_prompt("custom one")
    assert persona_loader.has_custom_prompt()

    existed = persona_loader.reset_custom_prompt()
    assert existed is True
    assert not persona_loader.has_custom_prompt()
    assert persona_loader.load_effective_persona_prompt() == persona_loader.default_persona_prompt()
    assert not persona_loader.custom_prompt_path().exists()


def test_reset_is_idempotent_when_no_custom_file() -> None:
    assert persona_loader.reset_custom_prompt() is False


def test_whitespace_only_custom_is_treated_as_no_custom() -> None:
    persona_loader.custom_prompt_path().parent.mkdir(parents=True, exist_ok=True)
    persona_loader.custom_prompt_path().write_text("   \n\t  \n", encoding="utf-8")
    assert not persona_loader.has_custom_prompt()
    assert persona_loader.load_effective_persona_prompt() == persona_loader.default_persona_prompt()


def test_save_roundtrips_unicode_exactly() -> None:
    text = "Du bist BÄRBEL — höflich, präzise, mit Glück 🍀 und Größe."  # i18n-allow
    persona_loader.save_custom_prompt(text)
    assert persona_loader.read_custom_prompt() == text
    # No BOM corruption (AP-7 lesson): the raw bytes start with the real text.
    raw = persona_loader.custom_prompt_path().read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_save_strips_trailing_whitespace_but_keeps_body() -> None:
    persona_loader.save_custom_prompt("  hello world  \n\n")
    assert persona_loader.read_custom_prompt() == "hello world"
