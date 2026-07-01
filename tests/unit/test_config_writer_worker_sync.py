"""Tests for the three-layer ``[brain.worker].provider`` persistence sync.

The Heavy-Task worker provider is pinned in config-soll.json  # i18n-allow
(``"brain.worker": {"provider": ...}``), so a UI switch that writes only
the TOML would be rolled back by the drift-guard within 5 minutes — exactly
the BUG that hit ``brain.primary`` before it became 3-layer. The worker
switch must therefore write ALL THREE layers:

  1. ``jarvis.toml`` ``[brain.worker] provider``                  (TOML)
  2. ``scripts/config-soll.json`` ``brain.worker.provider``       (drift-soll)  # i18n-allow
  3. ``JARVIS__BRAIN__WORKER__PROVIDER`` User-scope ENV var       (boot override)

Layers 2 + 3 are best-effort (cloud-first): graceful no-op on a headless VPS,
never raise out of ``set_worker_provider``, never break the TOML write.

Uses TEMP files + monkeypatch only — never touches the live config.

Renamed from [brain.sub_jarvis] to [brain.worker] in the 2026-06-29
Jarvis-Agents rename.  The old function names (set_sub_jarvis_provider,
set_sub_jarvis_model) are back-compat aliases and still work; tests now use
the canonical new names.
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

[brain.worker]
provider = "claude-api"
model = ""
fallback_provider = "gemini"
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
                "_updated": "2026-05-28",
                "brain": {"primary": "gemini"},
                "brain.worker": {
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
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_worker_provider("gemini", path=sample_toml)

    # Layer 1: TOML — the [brain.worker] block, NOT a top-level key.
    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert "[brain.worker]" in toml_raw
    assert 'provider = "gemini"' in toml_raw
    # brain.primary must be untouched (the router stays separate).
    assert 'primary = "gemini"' in toml_raw

    # Layer 2: config-soll.json flat "brain.worker" key.  # i18n-allow
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.worker"]["provider"] == "gemini"  # i18n-allow

    # Layer 3: ENV var (new name post-rename).
    assert env_calls == [("JARVIS__BRAIN__WORKER__PROVIDER", "gemini")]


def test_toml_preserves_sibling_keys(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_worker_provider("openai", path=sample_toml)

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'provider = "openai"' in toml_raw
    # Sibling keys in the same block survive.
    assert 'fallback_provider = "gemini"' in toml_raw
    assert 'model = ""' in toml_raw


def test_config_soll_preserves_other_keys(  # i18n-allow
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_worker_provider("openrouter", path=sample_toml)

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.worker"]["provider"] == "openrouter"  # i18n-allow
    # Other keys in the block + other tables untouched.
    assert soll["brain.worker"]["fallback_provider"] == "gemini"  # i18n-allow
    assert soll["_comment"] == "do not lose me"  # i18n-allow
    assert soll["brain"]["primary"] == "gemini"  # i18n-allow
    assert soll["tts"]["provider"] == "gemini-flash-tts"  # i18n-allow


def test_creates_worker_block_if_missing(
    tmp_path: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """A TOML with [brain] but no [brain.worker] gets the block created."""
    toml = tmp_path / "jarvis.toml"
    toml.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_worker_provider("grok", path=toml)

    raw = toml.read_text(encoding="utf-8")
    assert "[brain.worker]" in raw
    assert 'provider = "grok"' in raw


def test_missing_config_soll_does_not_break_toml(  # i18n-allow
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent = tmp_path / "no-such-config-soll.json"  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_worker_provider("gemini", path=sample_toml)

    assert 'provider = "gemini"' in sample_toml.read_text(encoding="utf-8")
    assert not nonexistent.exists()


def test_missing_toml_is_created(
    sample_soll: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """A missing TOML file is auto-created (headless VPS / first-run path).

    _ensure_writable_config_path creates an empty file rather than raising
    FileNotFoundError, so the writer works on a fresh VPS install.
    """
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    new_toml = tmp_path / "nope.toml"
    assert not new_toml.exists()

    config_writer.set_worker_provider("gemini", path=new_toml)

    # File must now exist and contain the written key.
    assert new_toml.exists()
    assert 'provider = "gemini"' in new_toml.read_text(encoding="utf-8")


def test_updates_live_os_environ(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", lambda name, value: None)
    monkeypatch.delenv("JARVIS__BRAIN__WORKER__PROVIDER", raising=False)

    config_writer.set_worker_provider("openai", path=sample_toml)

    import os

    assert os.environ.get("JARVIS__BRAIN__WORKER__PROVIDER") == "openai"


# ---------------------------------------------------------------------------
# [brain.worker].model — the dedicated Jarvis-Agent LLM override (C2).
# Also pinned in config-soll.json, so the same 3-layer rule applies: a  # i18n-allow
# TOML-only write would be reverted by the drift-guard within minutes.
# ---------------------------------------------------------------------------


def test_set_model_writes_all_three_layers(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_worker_model("claude-sonnet-4-6", path=sample_toml)

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert "[brain.worker]" in toml_raw
    assert 'model = "claude-sonnet-4-6"' in toml_raw
    # provider untouched.
    assert 'provider = "claude-api"' in toml_raw

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.worker"]["model"] == "claude-sonnet-4-6"  # i18n-allow
    assert soll["brain.worker"]["provider"] == "claude-api"  # i18n-allow

    assert env_calls == [("JARVIS__BRAIN__WORKER__MODEL", "claude-sonnet-4-6")]


def test_set_model_empty_string_resets_to_provider_default(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """Empty model is the documented sentinel: provider's deep model wins."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_worker_model("claude-sonnet-4-6", path=sample_toml)
    config_writer.set_worker_model("", path=sample_toml)

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'model = ""' in toml_raw
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.worker"]["model"] == ""  # i18n-allow
    assert env_calls[-1] == ("JARVIS__BRAIN__WORKER__MODEL", "")


def test_set_model_missing_soll_does_not_break_toml(  # i18n-allow
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent = tmp_path / "no-such-config-soll.json"  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_sub_jarvis_model("gemini-3.1-pro-preview", path=sample_toml)

    assert 'model = "gemini-3.1-pro-preview"' in sample_toml.read_text(encoding="utf-8")
    assert not nonexistent.exists()
