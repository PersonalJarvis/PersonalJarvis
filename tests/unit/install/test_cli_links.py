"""Installer CLI discovery on every OS, repeat installs, and collision protection."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.setup import cli_path
from jarvis.setup.cli_path import COMMAND_NAMES, PROFILE_MARKER

_spec = importlib.util.spec_from_file_location(
    "cli_links_installer", Path(__file__).resolve().parents[3] / "install" / "installer.py"
)
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)


@pytest.fixture
def symlinks_available(tmp_path):
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(tmp_path / "probe-target")
    except OSError as exc:
        pytest.skip(f"Host does not permit symlinks: {exc}")
    probe.unlink()


@pytest.fixture
def linux_install(monkeypatch, tmp_path):
    root = tmp_path / "installed app"
    home = tmp_path / "user home"
    for command in COMMAND_NAMES:
        target = root / ".venv" / "bin" / command
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"#!/bin/sh\necho 'fixture {command}'\n", encoding="utf-8")
        target.chmod(0o755)
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer, "repo_root", lambda: root)
    monkeypatch.setattr(installer.Path, "home", lambda: home)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("SHELL", "/bin/bash")
    return root, home / ".local" / "bin"


def test_linux_links_both_commands_and_survives_reinstall(
    linux_install, capsys, symlinks_available
):
    root, bin_dir = linux_install
    installer.step_cli_links(dry_run=False)
    installer.step_cli_links(dry_run=False)
    for command in COMMAND_NAMES:
        assert (bin_dir / command).is_symlink()
        assert (bin_dir / command).resolve() == root / ".venv" / "bin" / command
    # ~/.local/bin was not on PATH: one guarded block, not one per install run.
    profile = bin_dir.parent.parent / ".bashrc"
    assert profile.read_text(encoding="utf-8").count(PROFILE_MARKER) == 1
    assert "Open a new terminal" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="Executes POSIX entry points")
def test_linux_commands_run_on_clean_path(linux_install, symlinks_available):
    _, bin_dir = linux_install
    installer.step_cli_links(dry_run=False)
    for command in COMMAND_NAMES:
        result = subprocess.run(
            [command, "--version"],
            env={"HOME": str(bin_dir.parent.parent), "PATH": f"/usr/bin:/bin:{bin_dir}"},
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"fixture {command}"


@pytest.mark.parametrize("symlink", [False, True])
def test_linux_preserves_unrelated_command(
    linux_install, tmp_path, capsys, symlink, symlinks_available
):
    _, bin_dir = linux_install
    bin_dir.mkdir(parents=True)
    link = bin_dir / "jarvis"
    if symlink:
        link.symlink_to(tmp_path / "missing-other-install")
        original_target = link.readlink()
    else:
        link.write_text("unrelated command", encoding="utf-8")
    installer.step_cli_links(dry_run=False)
    assert "Keeping existing" in capsys.readouterr().out
    if symlink:
        assert link.readlink() == original_target
    else:
        assert link.read_text(encoding="utf-8") == "unrelated command"
    assert (bin_dir / "jarvisctl").is_symlink()


def test_linux_missing_entry_point_leaves_no_broken_link(linux_install, capsys):
    root, bin_dir = linux_install
    (root / ".venv" / "bin" / "jarvis").unlink()
    installer.step_cli_links(dry_run=False)
    assert not (bin_dir / "jarvis").is_symlink()
    assert "CLI entry point missing" in capsys.readouterr().out


def test_dry_run_never_creates_links(linux_install):
    _, bin_dir = linux_install
    installer.step_cli_links(dry_run=True)
    assert not bin_dir.exists()


def test_no_path_hint_when_bin_directory_is_already_present(linux_install, monkeypatch, capsys):
    _, bin_dir = linux_install
    monkeypatch.setenv("PATH", str(bin_dir))
    installer.step_cli_links(dry_run=False)
    assert "Open a new terminal" not in capsys.readouterr().out
    assert not (bin_dir.parent.parent / ".bashrc").exists()


def test_macos_links_into_local_bin_and_zsh_profile(
    linux_install, monkeypatch, symlinks_available
):
    root, bin_dir = linux_install
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    installer.step_cli_links(dry_run=False)
    assert (bin_dir / "personal-jarvis").resolve() == root / ".venv" / "bin" / "personal-jarvis"
    assert PROFILE_MARKER in (bin_dir.parent.parent / ".zshrc").read_text(encoding="utf-8")


class _FakeUserPath:
    def __init__(self) -> None:
        self.value = r"C:\Windows\system32"
        self.kind: int | None = 2
        self.writes = 0

    def read(self) -> tuple[str, int | None]:
        return self.value, self.kind

    def write(self, value: str, kind: int | None) -> None:
        self.value, self.kind = value, kind
        self.writes += 1


def test_windows_copies_launchers_and_adds_bin_to_user_path(
    linux_install, monkeypatch, capsys
):
    root, home_bin = linux_install
    for command in COMMAND_NAMES:
        exe = root / ".venv" / "Scripts" / f"{command}.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ launcher " + command.encode())
    store = _FakeUserPath()
    monkeypatch.setattr(cli_path, "UserPathStore", lambda: store)
    monkeypatch.setattr(installer.sys, "platform", "win32")
    installer.step_cli_links(dry_run=False)
    installer.step_cli_links(dry_run=False)
    for command in COMMAND_NAMES:
        copied = root / "bin" / f"{command}.exe"
        assert copied.read_bytes() == b"MZ launcher " + command.encode()
    assert store.value.endswith(str(root / "bin"))
    assert store.writes == 1
    assert store.kind == 2  # REG_EXPAND_SZ kept, %VARS% in Path keep working
    assert not home_bin.exists()  # nothing in ~/.local/bin on Windows
    assert "Open a new terminal" in capsys.readouterr().out
