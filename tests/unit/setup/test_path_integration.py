"""The `jarvis` terminal command works after install — on every OS.

Field report 2026-07-21: the website advertises ``jarvis`` / ``jarvis serve``,
but a fresh PowerShell answered CommandNotFoundException — pip had put the
console scripts only inside the install venv, which is never on PATH.
``jarvis/setup/path_integration.py`` links shims into ``~/.local/bin`` and
persists that dir on PATH. Both OS branches are exercised on every host: the
Windows registry access is faked, and the POSIX branch degrades from symlink
to wrapper script on hosts without symlink support.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.setup import path_integration as pi


def _make_install(tmp_path: Path, platform: str) -> Path:
    """A fake install tree whose venv carries every console script."""
    install = tmp_path / "install"
    scripts = pi.venv_scripts_dir(install, platform=platform)
    scripts.mkdir(parents=True)
    for command in pi.COMMANDS:
        name = f"{command}.exe" if platform == "win32" else command
        (scripts / name).write_text("stub", encoding="utf-8")
    return install


def _fake_registry(monkeypatch, initial: str | None = None) -> dict:
    """Replace the HKCU read/write/broadcast trio with an in-memory store."""
    store = {"value": initial, "kind": 2, "broadcasts": 0}

    def read() -> tuple[str | None, int]:
        return store["value"], store["kind"]

    def write(value: str, kind: int) -> None:
        store["value"], store["kind"] = value, kind

    def broadcast() -> None:
        store["broadcasts"] += 1

    monkeypatch.setattr(pi, "_read_windows_user_path", read)
    monkeypatch.setattr(pi, "_write_windows_user_path", write)
    monkeypatch.setattr(pi, "_broadcast_environment_change", broadcast)
    return store


# ------------------------------------------------------------------ Windows
def test_windows_fresh_install_creates_shims_and_registry_path(tmp_path, monkeypatch):
    install = _make_install(tmp_path, "win32")
    home = tmp_path / "home"
    store = _fake_registry(monkeypatch, initial=r"C:\Windows\system32")

    report = pi.ensure_cli_on_path(
        install, platform="win32", home=home, environ={"PATH": ""}
    )

    assert report.ok
    assert set(report.installed) == set(pi.COMMANDS)
    shim = home / ".local" / "bin" / "jarvis.cmd"
    content = shim.read_text(encoding="utf-8")
    # The shim must call the venv exe (quoted — the home path may carry
    # spaces) and forward every argument.
    assert f'"{pi.venv_scripts_dir(install, platform="win32") / "jarvis.exe"}"' in content
    assert "%*" in content
    assert str(home / ".local" / "bin") in store["value"]
    assert store["broadcasts"] == 1
    assert report.path_updated and report.needs_new_terminal
    assert not report.warnings


def test_windows_update_run_is_idempotent(tmp_path, monkeypatch):
    install = _make_install(tmp_path, "win32")
    home = tmp_path / "home"
    bin_dir = str(home / ".local" / "bin")
    store = _fake_registry(monkeypatch, initial=rf"C:\Windows;{bin_dir}")

    report = pi.ensure_cli_on_path(
        install, platform="win32", home=home, environ={"PATH": bin_dir}
    )

    assert report.ok
    assert store["value"] == rf"C:\Windows;{bin_dir}"  # unchanged
    assert not report.path_updated
    assert not report.needs_new_terminal


def test_windows_registry_failure_degrades_to_warning(tmp_path, monkeypatch):
    install = _make_install(tmp_path, "win32")
    home = tmp_path / "home"

    def boom() -> tuple[str | None, int]:
        raise OSError("registry unavailable")

    monkeypatch.setattr(pi, "_read_windows_user_path", boom)

    report = pi.ensure_cli_on_path(
        install, platform="win32", home=home, environ={"PATH": ""}
    )

    assert report.ok  # the shims still landed
    assert report.needs_new_terminal
    assert any("PATH manually" in w for w in report.warnings)


def test_merge_windows_path_entry_matches_case_and_trailing_slash():
    entry = r"C:\Users\u\.local\bin"
    assert pi.merge_windows_path_entry(r"c:\users\U\.LOCAL\bin\;C:\x", entry) is None
    merged = pi.merge_windows_path_entry(r"C:\x", entry)
    assert merged == rf"C:\x;{entry}"
    assert pi.merge_windows_path_entry(None, entry) == entry


def test_merge_windows_path_entry_matches_percent_expanded_form():
    entry = r"C:\Users\u\.local\bin"

    def expand(value: str) -> str:
        return value.replace("%USERPROFILE%", r"C:\Users\u")

    current = r"%USERPROFILE%\.local\bin;C:\x"
    assert pi.merge_windows_path_entry(current, entry, expand=expand) is None


# -------------------------------------------------------------------- POSIX
def test_posix_fresh_install_links_commands_and_writes_zshrc(tmp_path):
    install = _make_install(tmp_path, "linux")
    home = tmp_path / "home"
    env = {"PATH": "/usr/bin", "SHELL": "/bin/zsh"}

    report = pi.ensure_cli_on_path(install, platform="linux", home=home, environ=env)

    assert report.ok
    shim = home / ".local" / "bin" / "jarvis"
    target = pi.venv_scripts_dir(install, platform="linux") / "jarvis"
    if shim.is_symlink():
        assert shim.resolve() == target.resolve()
    else:  # wrapper-script fallback on hosts without symlink support
        content = shim.read_text(encoding="utf-8")
        assert str(target) in content and content.startswith("#!/bin/sh")
    # The login shell's rc was created with exactly one guarded PATH line.
    zshrc = (home / ".zshrc").read_text(encoding="utf-8")
    assert zshrc.count(pi.PROFILE_MARKER) == 1
    assert 'export PATH="$HOME/.local/bin:$PATH"' in zshrc
    assert report.path_updated and report.needs_new_terminal
    # The current process PATH was prepended for immediate use.
    assert env["PATH"].startswith(str(home / ".local" / "bin"))


def test_posix_rerun_never_duplicates_the_profile_line(tmp_path):
    install = _make_install(tmp_path, "linux")
    home = tmp_path / "home"
    for _ in range(2):
        pi.ensure_cli_on_path(
            install, platform="linux", home=home,
            environ={"PATH": "/usr/bin", "SHELL": "/bin/zsh"},
        )
    zshrc = (home / ".zshrc").read_text(encoding="utf-8")
    assert zshrc.count(pi.PROFILE_MARKER) == 1


def test_posix_path_already_present_touches_no_profile(tmp_path):
    install = _make_install(tmp_path, "linux")
    home = tmp_path / "home"
    bin_dir = str(pi.user_bin_dir(home))

    report = pi.ensure_cli_on_path(
        install, platform="linux", home=home,
        environ={"PATH": bin_dir, "SHELL": "/bin/bash"},
    )

    assert report.ok
    assert not report.path_updated
    assert not report.needs_new_terminal
    assert not (home / ".bashrc").exists()
    assert not (home / ".profile").exists()


def test_posix_foreign_command_is_never_clobbered(tmp_path):
    install = _make_install(tmp_path, "linux")
    home = tmp_path / "home"
    bin_dir = pi.user_bin_dir(home)
    bin_dir.mkdir(parents=True)
    foreign = bin_dir / "jarvis"
    foreign.write_text("#!/bin/sh\necho someone else's jarvis\n", encoding="utf-8")

    report = pi.ensure_cli_on_path(
        install, platform="linux", home=home,
        environ={"PATH": str(bin_dir), "SHELL": "/bin/bash"},
    )

    assert "jarvis" not in report.installed
    assert not report.ok
    assert foreign.read_text(encoding="utf-8").count("someone else") == 1
    assert any("different program" in w for w in report.warnings)


def test_posix_login_bash_gets_bash_profile_created(tmp_path):
    """macOS Terminal.app starts bash as a LOGIN shell, which reads
    ~/.bash_profile and never ~/.bashrc — the file must be created for bash
    users, and a newly created one must keep sourcing the files it shadows."""
    install = _make_install(tmp_path, "darwin")
    home = tmp_path / "home"

    pi.ensure_cli_on_path(
        install, platform="darwin", home=home,
        environ={"PATH": "/usr/bin", "SHELL": "/bin/bash"},
    )

    bash_profile = (home / ".bash_profile").read_text(encoding="utf-8")
    assert 'export PATH="$HOME/.local/bin:$PATH"' in bash_profile
    # The fresh file shadows ~/.profile and skips ~/.bashrc for login shells —
    # it must source both so the user's existing login environment survives.
    assert '. "$HOME/.profile"' in bash_profile
    assert '. "$HOME/.bashrc"' in bash_profile


def test_posix_existing_bash_profile_is_only_appended(tmp_path):
    install = _make_install(tmp_path, "darwin")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text("# mine\n", encoding="utf-8")

    pi.ensure_cli_on_path(
        install, platform="darwin", home=home,
        environ={"PATH": "/usr/bin", "SHELL": "/bin/bash"},
    )

    bash_profile = (home / ".bash_profile").read_text(encoding="utf-8")
    assert bash_profile.startswith("# mine\n")
    assert 'export PATH="$HOME/.local/bin:$PATH"' in bash_profile
    # An existing file already runs at login — no shadow-repair sourcing lines.
    assert '. "$HOME/.profile"' not in bash_profile


def test_posix_fish_gets_fish_syntax_not_export(tmp_path):
    install = _make_install(tmp_path, "linux")
    home = tmp_path / "home"
    fish_config = home / ".config" / "fish" / "config.fish"
    fish_config.parent.mkdir(parents=True)
    fish_config.write_text("# fish\n", encoding="utf-8")

    pi.ensure_cli_on_path(
        install, platform="linux", home=home,
        environ={"PATH": "/usr/bin", "SHELL": "/usr/bin/fish"},
    )

    fish = fish_config.read_text(encoding="utf-8")
    assert "fish_add_path" in fish
    assert "export PATH" not in fish


def test_missing_venv_script_degrades_to_warning(tmp_path):
    install = tmp_path / "install"
    pi.venv_scripts_dir(install, platform="linux").mkdir(parents=True)
    (pi.venv_scripts_dir(install, platform="linux") / "jarvis").write_text(
        "stub", encoding="utf-8"
    )  # jarvisctl / jctl deliberately absent

    report = pi.ensure_cli_on_path(
        install, platform="linux", home=tmp_path / "home",
        environ={"PATH": str(pi.user_bin_dir(tmp_path / "home")), "SHELL": ""},
    )

    assert report.ok  # the main command made it
    assert "jarvisctl" not in report.installed
    assert any("jarvisctl" in w for w in report.warnings)


# ---------------------------------------------------------------- uninstall
def test_remove_cli_shims_removes_only_our_own(tmp_path):
    install = _make_install(tmp_path, "linux")
    other_install = _make_install(tmp_path / "other", "linux")
    home = tmp_path / "home"
    env = {"PATH": "/usr/bin", "SHELL": "/bin/zsh"}
    pi.ensure_cli_on_path(install, platform="linux", home=home, environ=env)
    # A foreign file sitting next to our shims must survive the removal.
    foreign = pi.user_bin_dir(home) / "not-jarvis"
    foreign.write_text("keep me", encoding="utf-8")

    assert not pi.remove_cli_shims(other_install, platform="linux", home=home)
    removed = pi.remove_cli_shims(install, platform="linux", home=home)

    assert {p.name for p in removed} == set(pi.COMMANDS)
    assert not (pi.user_bin_dir(home) / "jarvis").exists()
    assert foreign.exists()


def test_list_cli_shims_windows_ownership_is_path_bound(tmp_path):
    install = _make_install(tmp_path, "win32")
    home = tmp_path / "home"
    with_registry_noop = {"PATH": ""}
    import pytest  # local import: only this test needs the fixture-free helper

    monkeypatch = pytest.MonkeyPatch()
    try:
        _fake_registry(monkeypatch)
        pi.ensure_cli_on_path(
            install, platform="win32", home=home, environ=with_registry_noop
        )
    finally:
        monkeypatch.undo()

    ours = pi.list_cli_shims(install, platform="win32", home=home)
    assert {p.name for p in ours} == {f"{c}.cmd" for c in pi.COMMANDS}
    # Another install dir owns nothing here.
    assert not pi.list_cli_shims(tmp_path / "elsewhere", platform="win32", home=home)
