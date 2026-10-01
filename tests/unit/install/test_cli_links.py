"""Linux installer CLI discovery, repeat installs, and collision protection."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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
    for command in ("jarvis", "jarvisctl"):
        target = root / ".venv" / "bin" / command
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer, "repo_root", lambda: root)
    monkeypatch.setattr(installer.Path, "home", lambda: home)
    monkeypatch.setenv("PATH", "/usr/bin")
    return root, home / ".local" / "bin"


def test_linux_links_both_commands_and_survives_reinstall(
    linux_install, capsys, symlinks_available
):
    root, bin_dir = linux_install
    installer.step_cli_links(dry_run=False)
    installer.step_cli_links(dry_run=False)
    for command in ("jarvis", "jarvisctl"):
        assert (bin_dir / command).is_symlink()
        assert (bin_dir / command).resolve() == root / ".venv" / "bin" / command
    assert "export PATH=" in capsys.readouterr().out


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
    assert "export PATH=" not in capsys.readouterr().out


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_other_platforms_do_not_create_linux_links(linux_install, monkeypatch, platform):
    _, bin_dir = linux_install
    monkeypatch.setattr(installer.sys, "platform", platform)
    installer.step_cli_links(dry_run=False)
    assert not bin_dir.exists()
