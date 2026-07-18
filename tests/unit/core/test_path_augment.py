"""Tests for jarvis.core.path_augment — the GUI-PATH augmentation.

The bug class under guard: a GUI-launched process (macOS launchd, Windows tray
relaunch) starts with a minimal PATH, so every ``shutil.which``-based CLI probe
reports an installed claude/codex/gemini as missing. ``ensure_cli_paths`` must
append existing well-known install dirs — and ONLY missing ones, keeping the
user's PATH order authoritative.
"""
from __future__ import annotations

import os
import sys

import pytest

from jarvis.core import path_augment


@pytest.fixture()
def fake_install_dir(tmp_path, monkeypatch):
    """A pretend CLI install dir + a candidate list pinned to it."""
    cli_dir = tmp_path / "cli-bin"
    cli_dir.mkdir()
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(
        path_augment, "candidate_dirs", lambda: [str(cli_dir), str(missing)]
    )
    return cli_dir


def test_appends_existing_dir_and_skips_missing(fake_install_dir, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    added = path_augment.ensure_cli_paths()
    assert added == [str(fake_install_dir)]
    parts = os.environ["PATH"].split(os.pathsep)
    # Existing entries keep priority — the new dir is appended, never prepended.
    assert parts[0] == "/usr/bin"
    assert str(fake_install_dir) in parts


def test_idempotent_second_call_adds_nothing(fake_install_dir, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    assert path_augment.ensure_cli_paths()
    before = os.environ["PATH"]
    assert path_augment.ensure_cli_paths() == []
    assert os.environ["PATH"] == before


def test_dir_already_on_path_is_not_duplicated(fake_install_dir, monkeypatch):
    monkeypatch.setenv("PATH", str(fake_install_dir))
    assert path_augment.ensure_cli_paths() == []
    assert os.environ["PATH"] == str(fake_install_dir)


def test_empty_path_still_works(fake_install_dir, monkeypatch):
    monkeypatch.setenv("PATH", "")
    added = path_augment.ensure_cli_paths()
    assert added == [str(fake_install_dir)]
    assert os.environ["PATH"] == str(fake_install_dir)


def test_candidates_are_platform_appropriate():
    dirs = path_augment.candidate_dirs()
    assert dirs, "candidate list must never be empty on a supported platform"
    if sys.platform == "win32":
        assert any("WinGet" in d for d in dirs)
        assert any(d.endswith("npm") for d in dirs)
        # Claude Code's native installer (install.ps1 -> ~/.local/bin) and the
        # `claude install` migration dir (~/.claude/local) — a working terminal
        # `claude` was reported "not installed" without them (2026-07-18
        # Windows test machine).
        assert any(d.endswith(os.path.join(".local", "bin")) for d in dirs)
        assert any(d.endswith(os.path.join(".claude", "local")) for d in dirs)
    else:
        assert "/usr/local/bin" in dirs
        assert "/opt/homebrew/bin" in dirs  # Apple-Silicon Homebrew
        assert any(d.endswith(os.path.join(".local", "bin")) for d in dirs)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX candidate shape")
def test_posix_candidates_cover_the_mac_report(monkeypatch):
    """The 2026-07-18 Mac symptom: claude installed via npm/native installer,
    app blind to it. The curated list must cover both install families."""
    dirs = path_augment.candidate_dirs()
    assert any(d.endswith(os.path.join(".claude", "local")) for d in dirs)
    assert any(d.endswith(os.path.join(".npm-global", "bin")) for d in dirs)
