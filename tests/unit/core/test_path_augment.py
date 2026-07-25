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
        assert any(d.endswith("nodejs") for d in dirs)
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


def test_windows_candidates_cover_official_antigravity_installer(
    tmp_path, monkeypatch
):
    local = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    dirs = path_augment._windows_candidates()

    assert str(local / "agy" / "bin") in dirs


def test_windows_candidates_refresh_node_installer_paths(tmp_path, monkeypatch):
    """A GUI already running during Node installation must find the runtime.

    npm puts the Codex shim in APPDATA, but that shim still invokes node.exe.
    The official machine-wide and user-scoped Node install locations therefore
    both belong in the refreshed GUI PATH.
    """
    program_files = tmp_path / "Program Files"
    local = tmp_path / "LocalAppData"
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    dirs = path_augment._windows_candidates()

    assert str(program_files / "nodejs") in dirs
    assert str(local / "Programs" / "nodejs") in dirs


def test_windows_candidates_keep_64_bit_node_visible_to_32_bit_process(
    tmp_path, monkeypatch
):
    program_files_x86 = tmp_path / "Program Files (x86)"
    program_files_64 = tmp_path / "Program Files"
    monkeypatch.setenv("ProgramFiles", str(program_files_x86))
    monkeypatch.setenv("ProgramFiles(x86)", str(program_files_x86))
    monkeypatch.setenv("ProgramW6432", str(program_files_64))

    dirs = path_augment._windows_candidates()

    assert str(program_files_64 / "nodejs") in dirs
    assert dirs.count(str(program_files_x86 / "nodejs")) == 1


def test_resolve_node_executable_finds_well_known_windows_install(
    tmp_path, monkeypatch
):
    program_files = tmp_path / "Program Files"
    node_exe = program_files / "nodejs" / "node.exe"
    node_exe.parent.mkdir(parents=True)
    node_exe.write_bytes(b"MZ")

    monkeypatch.setattr(path_augment.sys, "platform", "win32")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)

    assert os.path.normcase(path_augment.resolve_node_executable() or "") == (
        os.path.normcase(str(node_exe))
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX candidate shape")
def test_posix_candidates_cover_the_mac_report(monkeypatch):
    """The 2026-07-18 Mac symptom: claude installed via npm/native installer,
    app blind to it. The curated list must cover both install families."""
    dirs = path_augment.candidate_dirs()
    assert any(d.endswith(os.path.join(".claude", "local")) for d in dirs)
    assert any(d.endswith(os.path.join(".npm-global", "bin")) for d in dirs)
