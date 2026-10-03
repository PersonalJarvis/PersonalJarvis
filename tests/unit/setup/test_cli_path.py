"""PATH setup for the terminal commands (jarvis/setup/cli_path.py), every OS."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.setup import cli_path
from jarvis.setup.cli_path import (
    COMMAND_NAMES,
    PROFILE_MARKER,
    add_profile_block,
    add_to_user_path,
    install_commands,
    profile_file,
    remove_commands,
    remove_from_user_path,
    remove_profile_block,
)


class FakeUserPath:
    """Stands in for HKCU\\Environment so no test touches the real registry."""

    def __init__(self, value: str = "", kind: int | None = None) -> None:
        self.value = value
        self.kind = kind
        self.writes = 0

    def read(self) -> tuple[str, int | None]:
        return self.value, self.kind

    def write(self, value: str, kind: int | None) -> None:
        self.value, self.kind = value, kind
        self.writes += 1


def _never_found(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture
def symlinks_available(tmp_path):
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(tmp_path / "probe-target")
    except OSError as exc:
        pytest.skip(f"Host does not permit symlinks: {exc}")
    probe.unlink()


def _windows_root(tmp_path: Path) -> Path:
    root = tmp_path / "install"
    scripts = root / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    for command in COMMAND_NAMES:
        (scripts / f"{command}.exe").write_bytes(b"launcher:" + command.encode())
    return root


def _posix_root(tmp_path: Path) -> Path:
    root = tmp_path / "install"
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    for command in COMMAND_NAMES:
        (venv_bin / command).write_text("#!/bin/sh\n", encoding="utf-8")
    return root


# ------------------------------------------------------------------ profiles
@pytest.mark.parametrize(
    ("shell", "platform_name", "expected"),
    [
        ("/bin/zsh", "darwin", ".zshrc"),
        ("/usr/bin/zsh", "linux", ".zshrc"),
        ("/bin/bash", "darwin", ".bash_profile"),
        ("/bin/bash", "linux", ".bashrc"),
        ("/usr/bin/fish", "linux", ".config/fish/conf.d/personal-jarvis.fish"),
        ("/bin/dash", "linux", ".profile"),
        (None, "linux", ".profile"),
    ],
)
def test_profile_matches_the_login_shell(tmp_path, shell, platform_name, expected):
    found = profile_file(tmp_path, login_shell=shell, platform_name=platform_name)
    assert found == tmp_path / expected


def test_profile_block_is_added_once_and_removed_cleanly(tmp_path):
    profile = tmp_path / ".zshrc"
    profile.write_text("export EDITOR=vim", encoding="utf-8")  # no trailing newline
    assert add_profile_block(profile) is True
    assert add_profile_block(profile) is False
    text = profile.read_text(encoding="utf-8")
    assert text.count(PROFILE_MARKER) == 1
    assert text.startswith("export EDITOR=vim\n")
    assert '$HOME/.local/bin' in text

    assert remove_profile_block(profile) is True
    assert profile.read_text(encoding="utf-8") == "export EDITOR=vim\n"
    assert remove_profile_block(profile) is False


def test_fish_profile_uses_fish_syntax(tmp_path):
    profile = profile_file(tmp_path, login_shell="fish", platform_name="linux")
    add_profile_block(profile)
    text = profile.read_text(encoding="utf-8")
    assert "set -gx PATH" in text
    assert "export" not in text


# ---------------------------------------------------------- Windows user Path
def test_user_path_append_is_idempotent_and_case_insensitive(tmp_path):
    store = FakeUserPath(r"%USERPROFILE%\bin;C:\Tools", kind=2)
    target = Path(r"C:\Users\Someone\.personal-jarvis\bin")
    assert add_to_user_path(target, store) is True
    assert store.value == r"%USERPROFILE%\bin;C:\Tools;" + str(target)
    store.value = store.value.upper() + "\\"
    assert add_to_user_path(target, store) is False
    assert store.writes == 1
    assert store.kind == 2


def test_user_path_removal_keeps_other_entries():
    target = Path(r"C:\j\bin")
    store = FakeUserPath(r"C:\Tools;C:\j\bin;D:\other", kind=1)
    assert remove_from_user_path(target, store) is True
    assert store.value == r"C:\Tools;D:\other"
    assert remove_from_user_path(target, store) is False


def test_windows_install_copies_launchers_and_sets_path(tmp_path):
    root = _windows_root(tmp_path)
    store = FakeUserPath()
    report = install_commands(
        root, platform_name="win32", environ={"PATH": r"C:\Windows"}, store=store,
        which=_never_found,
    )
    assert report.linked == list(COMMAND_NAMES)
    for command in COMMAND_NAMES:
        assert (root / "bin" / f"{command}.exe").read_bytes() == b"launcher:" + command.encode()
    assert store.value == str(root / "bin")
    assert report.path_updated is not None
    assert report.needs_new_terminal is True


def test_windows_repeat_install_leaves_identical_launchers_untouched(tmp_path):
    root = _windows_root(tmp_path)
    store = FakeUserPath()
    install_commands(root, platform_name="win32", environ={}, store=store, which=_never_found)
    copied = root / "bin" / "jarvis.exe"
    before = copied.stat().st_mtime_ns
    (root / ".venv" / "Scripts" / "jarvisctl.exe").write_bytes(b"launcher:new")

    report = install_commands(
        root, platform_name="win32", environ={"PATH": str(root / "bin")}, store=store,
        which=_never_found,
    )
    assert copied.stat().st_mtime_ns == before
    assert (root / "bin" / "jarvisctl.exe").read_bytes() == b"launcher:new"
    assert store.writes == 1
    assert report.path_updated is None
    assert report.needs_new_terminal is False


def test_windows_missing_entry_point_is_reported(tmp_path):
    root = _windows_root(tmp_path)
    (root / ".venv" / "Scripts" / "personal-jarvis.exe").unlink()
    report = install_commands(
        root, platform_name="win32", environ={}, store=FakeUserPath(), which=_never_found
    )
    assert report.missing == ["personal-jarvis"]
    assert not (root / "bin" / "personal-jarvis.exe").exists()


def test_windows_remove_only_touches_the_path(tmp_path):
    root = _windows_root(tmp_path)
    store = FakeUserPath(r"C:\Tools")
    install_commands(root, platform_name="win32", environ={}, store=store, which=_never_found)
    removed = remove_commands(root, platform_name="win32", home=tmp_path / "home", store=store)
    assert store.value == r"C:\Tools"
    assert len(removed) == 1


def test_shadowing_command_is_reported(tmp_path):
    root = _windows_root(tmp_path)
    bin_dir = root / "bin"

    def which(command: str, path: str | None = None) -> str:
        return str(tmp_path / "elsewhere" / f"{command}.exe")

    report = install_commands(
        root, platform_name="win32", environ={"PATH": str(bin_dir)}, store=FakeUserPath(),
        which=which,
    )
    assert set(report.shadowed_by) == set(COMMAND_NAMES)


def test_dry_run_changes_nothing(tmp_path):
    root = _windows_root(tmp_path)
    store = FakeUserPath()
    report = install_commands(
        root, platform_name="win32", environ={}, store=store, which=_never_found, dry_run=True
    )
    assert report.linked == list(COMMAND_NAMES)
    assert not (root / "bin").exists()
    assert store.writes == 0


# ------------------------------------------------------------- macOS / Linux
@pytest.mark.parametrize("platform_name", ["darwin", "linux"])
def test_posix_install_and_remove_round_trip(tmp_path, platform_name, symlinks_available):
    root = _posix_root(tmp_path)
    home = tmp_path / "home"
    foreign = home / ".local" / "bin" / "jarvisctl"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("someone else's tool", encoding="utf-8")

    report = install_commands(
        root, platform_name=platform_name, home=home, environ={"PATH": "/usr/bin"},
        login_shell="/bin/zsh", which=_never_found,
    )
    assert report.kept == ["jarvisctl"]
    assert set(report.linked) == set(COMMAND_NAMES) - {"jarvisctl"}
    assert report.path_updated == str(home / ".zshrc")

    removed = remove_commands(root, platform_name=platform_name, home=home)
    assert not (home / ".local" / "bin" / "jarvis").exists()
    assert foreign.read_text(encoding="utf-8") == "someone else's tool"
    assert PROFILE_MARKER not in (home / ".zshrc").read_text(encoding="utf-8")
    assert any("PATH line" in line for line in removed)


def test_posix_bin_dir_is_the_xdg_user_bin(tmp_path):
    assert cli_path.bin_dir(tmp_path, platform_name="linux", home=tmp_path / "h") == (
        tmp_path / "h" / ".local" / "bin"
    )
    assert cli_path.bin_dir(tmp_path, platform_name="win32") == tmp_path / "bin"


def test_no_command_collides_with_the_branded_windows_launcher():
    """Windows paths ignore case: a `personaljarvis` console script would be
    the same file as the branded launcher that pip and the app both write."""
    from jarvis.core.instance import current_instance

    branded = Path(current_instance().windows_branded_launcher_file_name).stem.casefold()
    assert branded not in {command.casefold() for command in COMMAND_NAMES}
