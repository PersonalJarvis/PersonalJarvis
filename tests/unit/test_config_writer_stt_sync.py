"""Tests for the three-layer ``[stt] provider`` persistence sync.

When the user switches the STT provider in the desktop app, the choice must
persist across a restart. ``config-soll.json`` pins ``stt.provider``, so a UI
switch that wrote only the TOML would be rolled back by the drift-guard within
5 minutes — exactly the BUG that hit ``brain.primary`` before it became
3-layer. The STT switch must therefore write ALL THREE layers:

  1. ``jarvis.toml`` ``[stt] provider``               (universal, always runs)
  2. ``scripts/config-soll.json`` ``stt.provider``     (drift-guard soll value)
  3. ``JARVIS__STT__PROVIDER`` User-scope ENV var      (boot override, winreg)

Layers 2 + 3 are best-effort (cloud-first): graceful no-op on a headless VPS,
never raise out of ``set_stt_provider``, never break the TOML write.

Uses TEMP files + monkeypatch only — never touches the live config.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis.core import config_writer


@pytest.fixture
def sample_toml(tmp_path: Path) -> Path:
    p = tmp_path / "jarvis.toml"
    p.write_text(
        """\
# Personal Jarvis config
[stt]
provider = "groq-api"
model = "large-v3-turbo"
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
                "_updated": "2026-05-29",
                "brain": {"primary": "gemini"},
                "stt": {"provider": "groq-api", "model": "large-v3-turbo"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


def test_set_stt_provider_writes_all_three_layers(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI switch must write TOML + config-soll.json + ENV (winreg)."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer,
        "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_stt_provider("faster-whisper", path=sample_toml)

    # Layer 1: TOML.
    assert 'provider = "faster-whisper"' in sample_toml.read_text(encoding="utf-8")

    # Layer 2: config-soll.json stt.provider.
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["stt"]["provider"] == "faster-whisper"

    # Layer 3: ENV setter called with the canonical var name + value.
    assert env_calls == [("JARVIS__STT__PROVIDER", "faster-whisper")]


def test_config_soll_sync_preserves_other_keys(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only stt.provider changes; the model + other tables stay."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_stt_provider("google-cloud-stt", path=sample_toml)

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["stt"]["provider"] == "google-cloud-stt"
    # The model pin inside stt is not touched by the provider switch.
    assert soll["stt"]["model"] == "large-v3-turbo"
    assert soll["_comment"] == "do not lose me"
    assert soll["brain"]["primary"] == "gemini"


def test_updates_live_os_environ(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", lambda name, value: None)
    monkeypatch.delenv("JARVIS__STT__PROVIDER", raising=False)

    config_writer.set_stt_provider("faster-whisper", path=sample_toml)

    import os

    assert os.environ.get("JARVIS__STT__PROVIDER") == "faster-whisper"


def test_missing_config_soll_does_not_break_toml(
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent = tmp_path / "no-such-config-soll.json"
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_stt_provider("faster-whisper", path=sample_toml)

    assert 'provider = "faster-whisper"' in sample_toml.read_text(encoding="utf-8")
    assert not nonexistent.exists()


def test_soll_sync_swallows_write_errors(
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "config-soll.json"
    broken.write_text("{ this is not valid json ", encoding="utf-8")
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: broken)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_stt_provider("faster-whisper", path=sample_toml)

    assert 'provider = "faster-whisper"' in sample_toml.read_text(encoding="utf-8")


def test_winreg_skipped_on_non_win32(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(sys, "platform", "linux")

    def _boom(name: str, value: str) -> None:  # pragma: no cover - guard
        raise AssertionError("registry write attempted on non-win32 platform")

    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", _boom)
    monkeypatch.delenv("JARVIS__STT__PROVIDER", raising=False)

    config_writer.set_stt_provider("faster-whisper", path=sample_toml)

    import os

    assert os.environ.get("JARVIS__STT__PROVIDER") == "faster-whisper"


def test_raises_on_missing_toml(
    sample_soll: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production now auto-creates a missing TOML instead of raising
    # FileNotFoundError (_ensure_writable_config_path, headless-VPS fix).
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    p = tmp_path / "nope.toml"
    assert not p.exists()
    config_writer.set_stt_provider("faster-whisper", path=p)
    assert p.exists(), "set_stt_provider must auto-create a missing config file"
    assert 'provider = "faster-whisper"' in p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# set_stt_language: TOML + config-soll sync (drift-guard pinned, no ENV var).
# ---------------------------------------------------------------------------


def test_set_stt_language_writes_all_three_layers(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recognition-language switch persists to TOML + config-soll + the
    JARVIS__STT__LANGUAGE ENV var, so neither the drift-guard nor a stale ENV
    override reverts it (same 3-layer trap as stt.model)."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "_set_user_env_var", lambda n, v: env_calls.append((n, v))
    )

    config_writer.set_stt_language("de", path=sample_toml)

    assert 'language = "de"' in sample_toml.read_text(encoding="utf-8")
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["stt"]["language"] == "de"
    # Sibling keys untouched.
    assert soll["stt"]["provider"] == "groq-api"
    assert soll["stt"]["model"] == "large-v3-turbo"
    # ENV layer written so a stale JARVIS__STT__LANGUAGE cannot mask the choice.
    assert env_calls == [("JARVIS__STT__LANGUAGE", "de")]


def test_set_stt_language_auto_roundtrip(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda n, v: None)

    config_writer.set_stt_language("auto", path=sample_toml)

    assert 'language = "auto"' in sample_toml.read_text(encoding="utf-8")
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["stt"]["language"] == "auto"


def test_set_stt_language_soll_failure_does_not_break_toml(
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "config-soll.json"
    broken.write_text("{ not valid json ", encoding="utf-8")
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: broken)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda n, v: None)

    config_writer.set_stt_language("es", path=sample_toml)

    assert 'language = "es"' in sample_toml.read_text(encoding="utf-8")
