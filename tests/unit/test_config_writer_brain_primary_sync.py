"""Tests for the three-layer ``brain.primary`` persistence sync.

When the user switches the Brain provider in the desktop app, the choice
must persist across a restart. There are THREE persistence layers and the
UI switch must be the authoritative writer of ALL three:

  1. ``jarvis.toml`` ``[brain] primary``           (universal, always runs)
  2. ``scripts/config-soll.json`` ``brain.primary`` (drift-guard soll value)
  3. ``JARVIS__BRAIN__PRIMARY`` User-scope ENV var  (boot override, winreg)

Layers 2 and 3 are best-effort enhancements gated for the cloud-first
doctrine: on a headless Linux VPS the config-soll.json may be absent and
there is no Windows registry. Neither must ever break the TOML write nor
raise out of ``set_brain_primary``.

These tests use TEMP files and monkeypatch only — they NEVER touch the live
``jarvis.toml`` or the live ``scripts/config-soll.json`` and NEVER mutate the
real Windows registry.
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
[brain]
primary = "openrouter"
deep_brain = "claude-api"
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_soll(tmp_path: Path) -> Path:
    """A config-soll.json skeleton with extra keys we must preserve."""
    p = tmp_path / "config-soll.json"
    p.write_text(
        json.dumps(
            {
                "_comment": "do not lose me",
                "_updated": "2026-05-28",
                "brain": {
                    "primary": "gemini",
                    "fallback": "gemini",
                    "deep_brain": "gemini",
                },
                "tts": {"provider": "gemini-flash-tts"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


# ----------------------------------------------------------------------
# Layer 1 + 2 + 3 all written by a single set_brain_primary call.
# ----------------------------------------------------------------------


def test_set_brain_primary_writes_all_three_layers(
    sample_toml: Path,
    sample_soll: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The UI switch must write TOML + config-soll.json + ENV (winreg)."""
    # Redirect the soll path to our temp copy.
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)

    # Capture the winreg setter call instead of touching the real registry.
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer,
        "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_brain_primary("openai", path=sample_toml)

    # Layer 1: TOML.
    assert 'primary = "openai"' in sample_toml.read_text(encoding="utf-8")

    # Layer 2: config-soll.json.
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["brain"]["primary"] == "openai"

    # Layer 3: ENV setter called with the canonical var name + value.
    assert env_calls == [("JARVIS__BRAIN__PRIMARY", "openai")]


def test_set_brain_primary_updates_live_os_environ(
    sample_toml: Path,
    sample_soll: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live process os.environ must be updated so children inherit it.

    We mock the registry-write layer (``_set_user_env_var_winreg``) so the real
    ``_set_user_env_var`` runs its os.environ update without touching the real
    registry.
    """
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", lambda name, value: None)
    monkeypatch.delenv("JARVIS__BRAIN__PRIMARY", raising=False)

    config_writer.set_brain_primary("grok", path=sample_toml)

    import os

    assert os.environ.get("JARVIS__BRAIN__PRIMARY") == "grok"


# ----------------------------------------------------------------------
# Layer 2: config-soll.json key preservation.
# ----------------------------------------------------------------------


def test_config_soll_sync_preserves_other_keys(
    sample_toml: Path,
    sample_soll: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only brain.primary changes; _comment and other brain.* keys stay."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_primary("openai", path=sample_toml)

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))
    assert soll["_comment"] == "do not lose me"
    assert soll["_updated"] == "2026-05-28"
    assert soll["brain"]["primary"] == "openai"
    # Other brain.* keys untouched.
    assert soll["brain"]["fallback"] == "gemini"
    assert soll["brain"]["deep_brain"] == "gemini"
    # Other top-level tables untouched.
    assert soll["tts"]["provider"] == "gemini-flash-tts"


def test_config_soll_sync_indent_two(
    sample_toml: Path,
    sample_soll: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config-soll.json is written with indent=2 (drift-guard readability)."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_primary("openai", path=sample_toml)

    raw = sample_soll.read_text(encoding="utf-8")
    assert '\n  "brain"' in raw  # two-space indent for top-level keys


def test_config_soll_sync_creates_brain_block_if_missing(
    sample_toml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config-soll.json without a brain block gets one created."""
    soll = tmp_path / "config-soll.json"
    soll.write_text(json.dumps({"_comment": "x", "tts": {}}, indent=2), encoding="utf-8")
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_primary("openai", path=sample_toml)

    data = json.loads(soll.read_text(encoding="utf-8"))
    assert data["brain"]["primary"] == "openai"
    assert data["_comment"] == "x"


def test_config_soll_sync_leaves_no_tmp_file(
    sample_toml: Path,
    sample_soll: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomic tempfile+replace cleans up after itself."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_primary("openai", path=sample_toml)

    leftovers = list(sample_soll.parent.glob("*.tmp"))
    assert leftovers == [], f"Tempfile not cleaned up: {leftovers}"


# ----------------------------------------------------------------------
# Cloud-first gating: graceful no-op behavior.
# ----------------------------------------------------------------------


def test_missing_config_soll_does_not_break_toml_write(
    sample_toml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When config-soll.json is absent (headless VPS), TOML still gets written
    and set_brain_primary does NOT raise."""
    nonexistent = tmp_path / "no-such-config-soll.json"
    assert not nonexistent.exists()
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    # Must not raise.
    config_writer.set_brain_primary("openai", path=sample_toml)

    # Layer 1 still applied.
    assert 'primary = "openai"' in sample_toml.read_text(encoding="utf-8")
    # No file was created at the soll path.
    assert not nonexistent.exists()


def test_winreg_path_skipped_on_non_win32(
    sample_toml: Path,
    sample_soll: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a Linux VPS (sys.platform != 'win32') the registry setter is skipped,
    but os.environ is still updated for the live process."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(sys, "platform", "linux")

    reg_calls: list[str] = []
    # The real winreg setter must never run on Linux. We assert it is NOT
    # invoked by making it explode if it ever is.
    def _boom(name: str, value: str) -> None:  # pragma: no cover - guard
        reg_calls.append(name)
        raise AssertionError("registry write attempted on non-win32 platform")

    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", _boom)
    monkeypatch.delenv("JARVIS__BRAIN__PRIMARY", raising=False)

    # Must not raise.
    config_writer.set_brain_primary("openai", path=sample_toml)

    import os

    # ENV still updated in-process (cross-platform).
    assert os.environ.get("JARVIS__BRAIN__PRIMARY") == "openai"
    # winreg path never touched.
    assert reg_calls == []


def test_env_sync_swallows_winreg_errors(
    sample_toml: Path,
    sample_soll: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry/import error in the ENV sync is logged and swallowed —
    never raised out of set_brain_primary, and the TOML write still wins."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)

    def _raise(name: str, value: str) -> None:
        raise OSError("simulated registry failure")

    monkeypatch.setattr(config_writer, "_set_user_env_var", _raise)

    # Must not raise.
    config_writer.set_brain_primary("openai", path=sample_toml)

    assert 'primary = "openai"' in sample_toml.read_text(encoding="utf-8")


def test_soll_sync_swallows_write_errors(
    sample_toml: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A broken config-soll.json (invalid JSON) must not break the TOML write."""
    broken = tmp_path / "config-soll.json"
    broken.write_text("{ this is not valid json ", encoding="utf-8")
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: broken)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    # Must not raise.
    config_writer.set_brain_primary("openai", path=sample_toml)

    assert 'primary = "openai"' in sample_toml.read_text(encoding="utf-8")


def test_set_brain_primary_still_raises_on_missing_toml(
    sample_soll: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The TOML write is the universal path — a missing TOML still raises
    FileNotFoundError (the syncs are not a way to mask a broken setup)."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    with pytest.raises(FileNotFoundError):
        config_writer.set_brain_primary("openai", path=tmp_path / "does-not-exist.toml")
