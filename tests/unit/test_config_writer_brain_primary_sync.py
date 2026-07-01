"""Tests for the three-layer ``brain.primary`` persistence sync.

When the user switches the Brain provider in the desktop app, the choice
must persist across a restart. There are THREE persistence layers and the
UI switch must be the authoritative writer of ALL three:

  1. ``jarvis.toml`` ``[brain] primary``           (universal, always runs)
  2. ``scripts/config-soll.json`` ``brain.primary`` (drift-guard soll value)  # i18n-allow
  3. ``JARVIS__BRAIN__PRIMARY`` User-scope ENV var  (boot override, winreg)

Layers 2 and 3 are best-effort enhancements gated for the cloud-first
doctrine: on a headless Linux VPS the config-soll.json may be absent and  # i18n-allow
there is no Windows registry. Neither must ever break the TOML write nor
raise out of ``set_brain_primary``.

These tests use TEMP files and monkeypatch only — they NEVER touch the live
``jarvis.toml`` or the live ``scripts/config-soll.json`` and NEVER mutate the  # i18n-allow
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
def sample_soll(tmp_path: Path) -> Path:  # i18n-allow
    """A config-soll.json skeleton with extra keys we must preserve."""  # i18n-allow
    p = tmp_path / "config-soll.json"  # i18n-allow
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
    sample_soll: Path,  # i18n-allow
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The UI switch must write TOML + config-soll.json + ENV (winreg)."""  # i18n-allow
    # Redirect the soll path to our temp copy.  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow

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

    # Layer 2: config-soll.json.  # i18n-allow
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain"]["primary"] == "openai"  # i18n-allow

    # Layer 3: ENV setter called with the canonical var name + value.
    assert env_calls == [("JARVIS__BRAIN__PRIMARY", "openai")]


def test_set_brain_primary_updates_live_os_environ(
    sample_toml: Path,
    sample_soll: Path,  # i18n-allow
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live process os.environ must be updated so children inherit it.

    We mock the registry-write layer (``_set_user_env_var_winreg``) so the real
    ``_set_user_env_var`` runs its os.environ update without touching the real
    registry.
    """
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", lambda name, value: None)
    monkeypatch.delenv("JARVIS__BRAIN__PRIMARY", raising=False)

    config_writer.set_brain_primary("grok", path=sample_toml)

    import os

    assert os.environ.get("JARVIS__BRAIN__PRIMARY") == "grok"


# ----------------------------------------------------------------------
# Layer 2: config-soll.json key preservation.  # i18n-allow
# ----------------------------------------------------------------------


def test_config_soll_sync_preserves_other_keys(  # i18n-allow
    sample_toml: Path,
    sample_soll: Path,  # i18n-allow
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only brain.primary changes; _comment and other brain.* keys stay."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_primary("openai", path=sample_toml)

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["_comment"] == "do not lose me"  # i18n-allow
    assert soll["_updated"] == "2026-05-28"  # i18n-allow
    assert soll["brain"]["primary"] == "openai"  # i18n-allow
    # Other brain.* keys untouched.
    assert soll["brain"]["fallback"] == "gemini"  # i18n-allow
    assert soll["brain"]["deep_brain"] == "gemini"  # i18n-allow
    # Other top-level tables untouched.
    assert soll["tts"]["provider"] == "gemini-flash-tts"  # i18n-allow


def test_config_soll_sync_indent_two(  # i18n-allow
    sample_toml: Path,
    sample_soll: Path,  # i18n-allow
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config-soll.json is written with indent=2 (drift-guard readability)."""  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_primary("openai", path=sample_toml)

    raw = sample_soll.read_text(encoding="utf-8")  # i18n-allow
    assert '\n  "brain"' in raw  # two-space indent for top-level keys


def test_config_soll_sync_creates_brain_block_if_missing(  # i18n-allow
    sample_toml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config-soll.json without a brain block gets one created."""  # i18n-allow
    soll = tmp_path / "config-soll.json"  # i18n-allow
    soll.write_text(json.dumps({"_comment": "x", "tts": {}}, indent=2), encoding="utf-8")  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_primary("openai", path=sample_toml)

    data = json.loads(soll.read_text(encoding="utf-8"))  # i18n-allow
    assert data["brain"]["primary"] == "openai"
    assert data["_comment"] == "x"


def test_config_soll_sync_leaves_no_tmp_file(  # i18n-allow
    sample_toml: Path,
    sample_soll: Path,  # i18n-allow
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomic tempfile+replace cleans up after itself."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_primary("openai", path=sample_toml)

    leftovers = list(sample_soll.parent.glob("*.tmp"))  # i18n-allow
    assert leftovers == [], f"Tempfile not cleaned up: {leftovers}"


# ----------------------------------------------------------------------
# Cloud-first gating: graceful no-op behavior.
# ----------------------------------------------------------------------


def test_missing_config_soll_does_not_break_toml_write(  # i18n-allow
    sample_toml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When config-soll.json is absent (headless VPS), TOML still gets written  # i18n-allow
    and set_brain_primary does NOT raise."""
    nonexistent = tmp_path / "no-such-config-soll.json"  # i18n-allow
    assert not nonexistent.exists()
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    # Must not raise.
    config_writer.set_brain_primary("openai", path=sample_toml)

    # Layer 1 still applied.
    assert 'primary = "openai"' in sample_toml.read_text(encoding="utf-8")
    # No file was created at the soll path.  # i18n-allow
    assert not nonexistent.exists()


def test_winreg_path_skipped_on_non_win32(
    sample_toml: Path,
    sample_soll: Path,  # i18n-allow
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a Linux VPS (sys.platform != 'win32') the registry setter is skipped,
    but os.environ is still updated for the live process."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
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
    sample_soll: Path,  # i18n-allow
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry/import error in the ENV sync is logged and swallowed —
    never raised out of set_brain_primary, and the TOML write still wins."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow

    def _raise(name: str, value: str) -> None:
        raise OSError("simulated registry failure")

    monkeypatch.setattr(config_writer, "_set_user_env_var", _raise)

    # Must not raise.
    config_writer.set_brain_primary("openai", path=sample_toml)

    assert 'primary = "openai"' in sample_toml.read_text(encoding="utf-8")


def test_soll_sync_swallows_write_errors(  # i18n-allow
    sample_toml: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A broken config-soll.json (invalid JSON) must not break the TOML write."""  # i18n-allow
    broken = tmp_path / "config-soll.json"  # i18n-allow
    broken.write_text("{ this is not valid json ", encoding="utf-8")
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: broken)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    # Must not raise.
    config_writer.set_brain_primary("openai", path=sample_toml)

    assert 'primary = "openai"' in sample_toml.read_text(encoding="utf-8")


def test_set_brain_primary_still_raises_on_missing_toml(
    sample_soll: Path,  # i18n-allow
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production now auto-creates a missing TOML (_ensure_writable_config_path).
    Verify the file is created and the key is written even when the file was absent."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    p = tmp_path / "does-not-exist.toml"
    assert not p.exists()
    config_writer.set_brain_primary("openai", path=p)
    assert p.exists(), "set_brain_primary must auto-create a missing config file"
    assert 'primary = "openai"' in p.read_text(encoding="utf-8")
