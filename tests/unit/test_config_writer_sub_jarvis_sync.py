"""Tests for the three-layer ``[brain.sub_jarvis].provider`` persistence sync.

The Heavy-Task subagent provider is pinned in config-soll.json
(``"brain.sub_jarvis": {"provider": ...}``), so a UI switch that writes only
the TOML would be rolled back by the drift-guard within 5 minutes — exactly
the BUG that hit ``brain.primary`` before it became 3-layer. The subagent
switch must therefore write ALL THREE layers:

  1. ``jarvis.toml`` ``[brain.sub_jarvis] provider``                  (TOML)
  2. ``scripts/config-soll.json`` ``brain.sub_jarvis.provider``       (drift-soll)
  3. ``JARVIS__BRAIN__SUB_JARVIS__PROVIDER`` User-scope ENV var       (boot override)

Layers 2 + 3 are best-effort (cloud-first): graceful no-op on a headless VPS,
never raise out of ``set_sub_jarvis_provider``, never break the TOML write.

Uses TEMP files + monkeypatch only — never touches the live config.
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

[brain.sub_jarvis]
provider = "claude-api"
model = ""
fallback_provider = "gemini"
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_soll(tmp_path: Path) -> Path:
    p = tmp_path / "config-soll.json"
    p.write_text(
        json.dumps(
            {
                "_comment": "do not lose me",
                "_updated": "2026-05-28",
                "brain": {"primary": "gemini"},
                "brain.sub_jarvis": {
                    "provider": "claude-api",
                    "model": "",
                    "fallback_provider": "gemini",
                },
                "tts": {"provider": "gemini-flash-tts"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


def test_writes_all_three_layers(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_sub_jarvis_provider("gemini", path=sample_toml)

    # Layer 1: TOML — the [brain.sub_jarvis] block, NOT a top-level key.
    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert "[brain.sub_jarvis]" in toml_raw
    assert 'provider = "gemini"' in toml_raw
    # brain.primary must be untouched (the router stays separate).
    assert 'primary = "gemini"' in toml_raw

    # Layer 2: config-soll.json flat "brain.sub_jarvis" key.
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["brain.sub_jarvis"]["provider"] == "gemini"

    # Layer 3: ENV var.
    assert env_calls == [("JARVIS__BRAIN__SUB_JARVIS__PROVIDER", "gemini")]


def test_toml_preserves_sibling_keys(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_sub_jarvis_provider("openai", path=sample_toml)

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'provider = "openai"' in toml_raw
    # Sibling keys in the same block survive.
    assert 'fallback_provider = "gemini"' in toml_raw
    assert 'model = ""' in toml_raw


def test_config_soll_preserves_other_keys(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_sub_jarvis_provider("openrouter", path=sample_toml)

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["brain.sub_jarvis"]["provider"] == "openrouter"
    # Other keys in the block + other tables untouched.
    assert soll["brain.sub_jarvis"]["fallback_provider"] == "gemini"
    assert soll["_comment"] == "do not lose me"
    assert soll["brain"]["primary"] == "gemini"
    assert soll["tts"]["provider"] == "gemini-flash-tts"


def test_creates_sub_jarvis_block_if_missing(
    tmp_path: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TOML with [brain] but no [brain.sub_jarvis] gets the block created."""
    toml = tmp_path / "jarvis.toml"
    toml.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_sub_jarvis_provider("grok", path=toml)

    raw = toml.read_text(encoding="utf-8")
    assert "[brain.sub_jarvis]" in raw
    assert 'provider = "grok"' in raw


def test_missing_config_soll_does_not_break_toml(
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent = tmp_path / "no-such-config-soll.json"
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_sub_jarvis_provider("gemini", path=sample_toml)

    assert 'provider = "gemini"' in sample_toml.read_text(encoding="utf-8")
    assert not nonexistent.exists()


def test_raises_on_missing_toml(
    sample_soll: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    with pytest.raises(FileNotFoundError):
        config_writer.set_sub_jarvis_provider("gemini", path=tmp_path / "nope.toml")


def test_updates_live_os_environ(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", lambda name, value: None)
    monkeypatch.delenv("JARVIS__BRAIN__SUB_JARVIS__PROVIDER", raising=False)

    config_writer.set_sub_jarvis_provider("openai", path=sample_toml)

    import os

    assert os.environ.get("JARVIS__BRAIN__SUB_JARVIS__PROVIDER") == "openai"
