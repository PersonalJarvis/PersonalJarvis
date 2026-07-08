"""Tests for the three-layer ``[brain.computer_use].provider`` persistence sync.

The dedicated GLOBAL Computer-Use planner provider is decoupled from
``[brain] primary`` (see the "dedicated Computer-Use provider" plan) and is
pinned in config-soll.json (``"brain.computer_use": {"provider": ...}``), so  # i18n-allow
a UI switch that writes only the TOML would be rolled back by the drift-guard
within 5 minutes — exactly the bug class that hit ``brain.primary`` before it
became 3-layer. The switch must therefore write ALL THREE layers:

  1. ``jarvis.toml`` ``[brain.computer_use] provider``               (TOML)
  2. ``scripts/config-soll.json`` ``brain.computer_use.provider``    (drift-soll)  # i18n-allow
  3. ``JARVIS__BRAIN__COMPUTER_USE__PROVIDER`` User-scope ENV var    (boot override)

Layers 2 + 3 are best-effort (cloud-first): graceful no-op on a headless VPS,
never raise out of ``set_computer_use_provider``, never break the TOML write.

Uses TEMP files + monkeypatch only — never touches the live config. Mirrors
``tests/unit/test_config_writer_worker_sync.py`` (the ``[brain.worker]``
sibling this feature copies the shape from).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.core import config_writer


@pytest.fixture
def sample_toml(tmp_path: Path) -> Path:
    p = tmp_path / "jarvis.toml"
    p.write_text(
        """\
# Personal Jarvis config
[brain]
primary = "gemini"

[brain.computer_use]
provider = "claude-api"
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_soll(tmp_path: Path) -> Path:  # i18n-allow
    p = tmp_path / "config-soll.json"  # i18n-allow
    p.write_text(
        json.dumps(
            {
                "_comment": "do not lose me",
                "_updated": "2026-07-08",
                "brain": {"primary": "gemini"},
                "brain.computer_use": {"provider": "claude-api"},
                "tts": {"provider": "gemini-flash-tts"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


def test_writes_all_three_layers(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_computer_use_provider("openai", path=sample_toml)

    # Layer 1: TOML — the [brain.computer_use] block, NOT a top-level key.
    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert "[brain.computer_use]" in toml_raw
    assert 'provider = "openai"' in toml_raw
    # brain.primary must be untouched (CU is decoupled from the main Brain).
    assert 'primary = "gemini"' in toml_raw

    # Layer 2: config-soll.json flat "brain.computer_use" key.  # i18n-allow
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.computer_use"]["provider"] == "openai"  # i18n-allow

    # Layer 3: ENV var.
    assert env_calls == [("JARVIS__BRAIN__COMPUTER_USE__PROVIDER", "openai")]


def test_toml_preserves_sibling_brain_primary(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_computer_use_provider("openrouter", path=sample_toml)

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'provider = "openrouter"' in toml_raw
    assert 'primary = "gemini"' in toml_raw


def test_config_soll_preserves_other_keys(  # i18n-allow
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_computer_use_provider("gemini", path=sample_toml)

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.computer_use"]["provider"] == "gemini"  # i18n-allow
    # Other keys in the block + other tables untouched.
    assert soll["_comment"] == "do not lose me"  # i18n-allow
    assert soll["brain"]["primary"] == "gemini"  # i18n-allow
    assert soll["tts"]["provider"] == "gemini-flash-tts"  # i18n-allow


def test_creates_computer_use_block_if_missing(
    tmp_path: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """A TOML with [brain] but no [brain.computer_use] gets the block created."""
    toml = tmp_path / "jarvis.toml"
    toml.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_computer_use_provider("claude-api", path=toml)

    raw = toml.read_text(encoding="utf-8")
    assert "[brain.computer_use]" in raw
    assert 'provider = "claude-api"' in raw


def test_missing_config_soll_does_not_break_toml(  # i18n-allow
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent = tmp_path / "no-such-config-soll.json"  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_computer_use_provider("gemini", path=sample_toml)

    assert 'provider = "gemini"' in sample_toml.read_text(encoding="utf-8")
    assert not nonexistent.exists()


def test_missing_toml_is_created(
    sample_soll: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """A missing TOML file is auto-created (headless VPS / first-run path)."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    new_toml = tmp_path / "nope.toml"
    assert not new_toml.exists()

    config_writer.set_computer_use_provider("gemini", path=new_toml)

    assert new_toml.exists()
    assert 'provider = "gemini"' in new_toml.read_text(encoding="utf-8")


def test_updates_live_os_environ(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", lambda name, value: None)
    monkeypatch.delenv("JARVIS__BRAIN__COMPUTER_USE__PROVIDER", raising=False)

    config_writer.set_computer_use_provider("openai", path=sample_toml)

    import os

    assert os.environ.get("JARVIS__BRAIN__COMPUTER_USE__PROVIDER") == "openai"
