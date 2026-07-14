"""Tests for config_writer: atomic TOML edits, comment preservation, roundtrip."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core import config_writer


@pytest.fixture(autouse=True)
def _isolate_provider_switch_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the config-soll.json + ENV side-effects of the provider switches.  # i18n-allow

    ``set_brain_primary`` / ``set_tts_provider`` / ``set_stt_provider`` are the
    authoritative writers of THREE persistence layers (jarvis.toml,
    scripts/config-soll.json, JARVIS__*  ENV). The tests in this module only  # i18n-allow
    exercise the TOML layer against a temp file, so we stub the best-effort syncs
    to no-ops — otherwise they would touch the LIVE scripts/config-soll.json and  # i18n-allow
    the LIVE Windows registry. The dedicated three-layer coverage lives in
    tests/unit/test_config_writer_{brain_primary,tts,stt}_sync.py.
    """
    monkeypatch.setattr(
        config_writer, "_sync_brain_primary_drift_soll", lambda name: None  # i18n-allow
    )
    monkeypatch.setattr(
        config_writer, "_sync_tts_provider_drift_soll", lambda applied: None  # i18n-allow
    )
    monkeypatch.setattr(
        config_writer, "_sync_stt_provider_drift_soll", lambda name: None  # i18n-allow
    )


@pytest.fixture
def sample_toml(tmp_path: Path) -> Path:
    """jarvis.toml skeleton with comments we don't want to lose."""
    p = tmp_path / "jarvis.toml"
    p.write_text(
        """\
# Personal Jarvis — main configuration
# Comments survive the write (tomlkit).

[brain]
# Active default provider
primary = "openrouter"
deep_brain = "claude-api"

[brain.providers.claude-api]
model = "claude-haiku-4-5"  # must NOT disappear

[tts]
provider = "gemini-flash-tts"
voice_de = "Charon"
""",
        encoding="utf-8",
    )
    return p


def test_set_brain_primary_changes_value(sample_toml: Path) -> None:
    config_writer.set_brain_primary("gemini", path=sample_toml)
    text = sample_toml.read_text(encoding="utf-8")
    assert 'primary = "gemini"' in text


def test_set_brain_primary_preserves_comments(sample_toml: Path) -> None:
    config_writer.set_brain_primary("gemini", path=sample_toml)
    text = sample_toml.read_text(encoding="utf-8")
    assert "# Personal Jarvis — main configuration" in text
    assert "# Active default provider" in text
    assert "# must NOT disappear" in text


def test_set_brain_primary_preserves_unrelated_keys(sample_toml: Path) -> None:
    config_writer.set_brain_primary("openai", path=sample_toml)
    text = sample_toml.read_text(encoding="utf-8")
    assert 'deep_brain = "claude-api"' in text
    assert 'voice_de = "Charon"' in text


def test_set_tts_provider_changes_value(sample_toml: Path) -> None:
    config_writer.set_tts_provider("openai-tts", path=sample_toml)
    text = sample_toml.read_text(encoding="utf-8")
    assert 'provider = "openai-tts"' in text


def test_set_reply_language_writes_brain_key(sample_toml: Path) -> None:
    config_writer.set_reply_language("es", path=sample_toml)
    text = sample_toml.read_text(encoding="utf-8")
    assert 'reply_language = "es"' in text


def test_set_reply_language_preserves_existing_brain_keys(sample_toml: Path) -> None:
    config_writer.set_reply_language("en", path=sample_toml)
    text = sample_toml.read_text(encoding="utf-8")
    # must not clobber the existing [brain] table
    assert 'primary = "openrouter"' in text
    assert 'deep_brain = "claude-api"' in text


def test_set_ui_language_writes_ui_key(sample_toml: Path) -> None:
    # The interface (display) language — backend home for the formerly
    # frontend-only localStorage setting, so voice/API can change it.
    config_writer.set_ui_language("de", path=sample_toml)
    text = sample_toml.read_text(encoding="utf-8")
    assert "[ui]" in text
    assert 'language = "de"' in text


def test_atomic_write_does_not_leave_tmp(sample_toml: Path) -> None:
    config_writer.set_brain_primary("openai", path=sample_toml)
    leftovers = list(sample_toml.parent.glob("*.tmp"))
    assert leftovers == [], f"Tempfile not cleaned up: {leftovers}"


def test_missing_config_raises(tmp_path: Path) -> None:
    # Production now auto-creates a missing TOML instead of raising
    # FileNotFoundError (open-source / headless-VPS fix, _ensure_writable_config_path).
    p = tmp_path / "does-not-exist.toml"
    assert not p.exists()
    config_writer.set_brain_primary("openai", path=p)
    assert p.exists(), "set_brain_primary must auto-create a missing config file"
    assert 'primary = "openai"' in p.read_text(encoding="utf-8")


def test_creates_brain_section_if_missing(tmp_path: Path) -> None:
    p = tmp_path / "jarvis.toml"
    p.write_text("[tts]\nprovider = \"gemini-flash-tts\"\n", encoding="utf-8")
    config_writer.set_brain_primary("openai", path=p)
    text = p.read_text(encoding="utf-8")
    assert "[brain]" in text
    assert 'primary = "openai"' in text


def test_roundtrip_keeps_value_stable(sample_toml: Path) -> None:
    config_writer.set_brain_primary("openai", path=sample_toml)
    config_writer.set_brain_primary("openai", path=sample_toml)  # idempotent
    text = sample_toml.read_text(encoding="utf-8")
    assert text.count('primary = "openai"') == 1


def test_atomic_write_succeeds_on_read_only_target(sample_toml: Path) -> None:
    """Read-only flag on jarvis.toml is the BUG-010 second defense layer.

    A provider-switch must lift it for the duration of the write and restore
    it afterwards. Without this the User sees WinError 5 in the UI toast
    and the provider switch is silently lost.
    """
    import stat

    # Mark the file read-only (Windows: clear S_IWRITE) before the patch.
    sample_toml.chmod(sample_toml.stat().st_mode & ~stat.S_IWRITE)
    assert not (sample_toml.stat().st_mode & stat.S_IWRITE), (
        "fixture is not read-only"
    )

    # The patch must succeed and persist the new provider.
    config_writer.set_tts_provider("grok-voice", path=sample_toml)
    text = sample_toml.read_text(encoding="utf-8")
    assert 'provider = "grok-voice"' in text

    # And the read-only flag must still be set afterwards (defense holds).
    assert not (sample_toml.stat().st_mode & stat.S_IWRITE), (
        "read-only flag was not restored after write"
    )

    # Restore writability so tmp_path cleanup can delete the file.
    sample_toml.chmod(sample_toml.stat().st_mode | stat.S_IWRITE)


def test_atomic_write_retries_transient_replace_denial(
    sample_toml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A short Windows sharing lock must not lose an in-app provider switch."""
    import os

    real_replace = os.replace
    attempted_sources: list[Path] = []

    def flaky_replace(
        source: str | bytes | Path, destination: str | bytes | Path
    ) -> None:
        attempted_sources.append(Path(source))
        if len(attempted_sources) < 3:
            raise PermissionError(5, "simulated transient sharing lock")
        real_replace(source, destination)

    monkeypatch.setattr(config_writer.os, "replace", flaky_replace)
    monkeypatch.setattr(config_writer.time, "sleep", lambda _seconds: None)

    config_writer.set_voice_mode("realtime", path=sample_toml)

    text = sample_toml.read_text(encoding="utf-8")
    assert "[voice]" in text
    assert 'mode = "realtime"' in text
    assert len(attempted_sources) == 3
    assert len(set(attempted_sources)) == 1
    assert attempted_sources[0].name.startswith(f".{sample_toml.name}.")
    assert list(sample_toml.parent.glob("*.tmp")) == []
