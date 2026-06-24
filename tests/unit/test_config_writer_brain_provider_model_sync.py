"""Tests for the two-layer ``[brain.providers.<p>]`` model persistence sync.

Per-provider model pins (``model`` / ``deep_model``) are pinned in
config-soll.json under the flat dotted key ``"brain.providers.<p>"``, so a
model picked in the per-provider model selector that wrote only the TOML would
be rolled back by the drift-guard within 5 minutes — the same BUG-010 class
that hit ``brain.primary``. ``set_brain_provider_model`` must therefore write:

  1. ``jarvis.toml`` ``[brain.providers.<p>] model/deep_model``      (TOML)
  2. ``scripts/config-soll.json`` ``brain.providers.<p>``            (drift-soll)

There is intentionally NO ENV layer: per-provider model keys have no effective
``JARVIS__*`` boot override (the override parser only nests on ``__``, and the
drift-guard's dotted ``JARVIS__BRAIN.PROVIDERS.*`` vars are inert), so adding
one would only create a new stale-override trap.

Layer 2 is best-effort (cloud-first): graceful no-op on a headless VPS, never
raises out of ``set_brain_provider_model``, never breaks the TOML write.

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

[brain.providers.gemini]
model = "gemini-2.5-flash"
deep_model = "gemini-2.5-pro"
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
                "_updated": "2026-06-20",
                "brain": {"primary": "gemini"},
                "brain.providers.gemini": {
                    "model": "gemini-2.5-flash",
                    "deep_model": "gemini-2.5-pro",
                },
                "tts": {"provider": "cartesia"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


def test_writes_toml_and_soll_no_env(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", deep_model="gemini-3.1-pro-preview",
        path=sample_toml,
    )

    # Layer 1: TOML.
    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'model = "gemini-3.5-flash"' in toml_raw
    assert 'deep_model = "gemini-3.1-pro-preview"' in toml_raw

    # Layer 2: config-soll.json flat "brain.providers.gemini" key.
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["brain.providers.gemini"]["model"] == "gemini-3.5-flash"
    assert soll["brain.providers.gemini"]["deep_model"] == "gemini-3.1-pro-preview"

    # NO ENV layer for per-provider model keys.
    assert env_calls == []


def test_only_written_keys_are_synced(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing only ``model`` leaves the soll ``deep_model`` untouched."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", path=sample_toml,
    )

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["brain.providers.gemini"]["model"] == "gemini-3.5-flash"
    # deep_model in the soll block is NOT changed (only model was written).
    assert soll["brain.providers.gemini"]["deep_model"] == "gemini-2.5-pro"


def test_config_soll_preserves_other_keys(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", path=sample_toml,
    )

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["_comment"] == "do not lose me"
    assert soll["brain"]["primary"] == "gemini"
    assert soll["tts"]["provider"] == "cartesia"


def test_creates_soll_block_if_missing(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider with no soll block yet gets one created (not an error)."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "claude-api", deep_model="claude-opus-4-8", path=sample_toml,
    )

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["brain.providers.claude-api"]["deep_model"] == "claude-opus-4-8"


def test_missing_config_soll_does_not_break_toml(
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent = tmp_path / "no-such-config-soll.json"
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", path=sample_toml,
    )

    assert 'model = "gemini-3.5-flash"' in sample_toml.read_text(encoding="utf-8")
    assert not nonexistent.exists()


def test_noop_when_both_none(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both ``None`` → nothing written anywhere (idempotent early return)."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)
    before_toml = sample_toml.read_text(encoding="utf-8")
    before_soll = sample_soll.read_text(encoding="utf-8")

    config_writer.set_brain_provider_model("gemini", path=sample_toml)

    assert sample_toml.read_text(encoding="utf-8") == before_toml
    assert sample_soll.read_text(encoding="utf-8") == before_soll


def test_writes_cu_model_to_toml_and_soll(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 3: the per-provider Computer-Use model persists like model/deep_model."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "gemini", cu_model="gemini-3.1-pro-preview", path=sample_toml,
    )

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'cu_model = "gemini-3.1-pro-preview"' in toml_raw
    # Untouched sibling keys remain.
    assert 'model = "gemini-2.5-flash"' in toml_raw

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["brain.providers.gemini"]["cu_model"] == "gemini-3.1-pro-preview"
    # Only cu_model synced — model/deep_model in the soll block stay as they were.
    assert soll["brain.providers.gemini"]["model"] == "gemini-2.5-flash"
    assert soll["brain.providers.gemini"]["deep_model"] == "gemini-2.5-pro"


def test_cu_model_cleared_with_empty_string(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty cu_model writes "" (UI 'use my main model') — distinct from None
    which means 'leave unchanged'."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model("gemini", cu_model="", path=sample_toml)

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'cu_model = ""' in toml_raw
