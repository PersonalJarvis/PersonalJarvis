"""LinuxAutostart: XDG .desktop write / status / drift / uninstall (CI-provable)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.autostart.linux import LinuxAutostart
from jarvis.autostart.protocol import LaunchSpec
from jarvis.core.desktop_entry import escape_value


def _spec(
    program: str = "/usr/bin/python3",
    working_dir: str = "/home/u/Personal Jarvis",
) -> LaunchSpec:
    return LaunchSpec(
        program=program,
        args=("-m", "jarvis.ui.web.launcher"),
        working_dir=working_dir,
        minimized=True,
    )


def test_install_writes_desktop_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    status = mgr.install(_spec())

    entry = tmp_path / "autostart" / "personal-jarvis.desktop"
    assert entry.exists()
    text = entry.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text
    assert "Exec=/usr/bin/python3 -m jarvis.ui.web.launcher" in text
    assert "X-GNOME-Autostart-enabled=true" in text
    assert status.installed is True
    assert status.matches_spec is True


def test_install_brands_entry_with_icon_and_wmclass(monkeypatch, tmp_path: Path) -> None:
    """The .desktop must carry an Icon= (the bundled PNG) and a StartupWMClass so
    the app menu / taskbar shows Jarvis, not the generic python3 interpreter icon."""
    from jarvis.assets import bundled_app_icon_png

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    LinuxAutostart().install(_spec())
    text = (tmp_path / "autostart" / "personal-jarvis.desktop").read_text(encoding="utf-8")

    png = bundled_app_icon_png()
    assert png is not None, "bundled jarvis.png must ship for the Linux Icon= key"
    # Every value goes through the key-file encoding. On a real Linux host that
    # is the identity for an icon path; on a Windows dev/CI host the bundled
    # path carries backslashes, and NOT escaping them would be the bug.
    assert f"Icon={escape_value(str(png))}" in text
    assert "StartupWMClass=personal-jarvis" in text


def test_status_detects_path_drift(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    mgr.install(_spec(program="/old/python3"))

    # The running install now resolves to a different interpreter path.
    drifted = mgr.status(_spec(program="/new/python3"))
    assert drifted.installed is True
    assert drifted.matches_spec is False


def test_program_with_spaces_is_quoted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    spec = _spec(program="/opt/My Apps/python3")
    mgr.install(spec)
    text = (tmp_path / "autostart" / "personal-jarvis.desktop").read_text(encoding="utf-8")
    assert 'Exec="/opt/My Apps/python3" -m jarvis.ui.web.launcher' in text
    # And the round-trip still matches.
    assert mgr.status(spec).matches_spec is True


def test_uninstall_removes_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    mgr.install(_spec())
    status = mgr.uninstall()
    assert not (tmp_path / "autostart" / "personal-jarvis.desktop").exists()
    assert status.installed is False


def test_status_supported_even_when_absent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    status = LinuxAutostart().status(_spec())
    assert status.supported is True
    assert status.installed is False


# ---- Desktop Entry escaping (both nested layers) ----------------------------


def _entry_text(tmp_path: Path) -> str:
    return (tmp_path / "autostart" / "personal-jarvis.desktop").read_text(
        encoding="utf-8"
    )


def test_percent_in_the_install_path_is_written_as_a_double_percent(
    monkeypatch, tmp_path: Path
) -> None:
    """A ``%`` starts a field code — unescaped it invalidates the whole entry.

    The desktop then ignores the file without a word, and the old status check
    still reported "enabled and current" because it compared the same unescaped
    string it had just written: dead autostart, invisible from both sides.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    spec = _spec(program="/opt/100%pure/python3", working_dir="/srv/50%off")
    status = mgr.install(spec)

    assert "Exec=/opt/100%%pure/python3 -m jarvis.ui.web.launcher" in _entry_text(
        tmp_path
    )
    assert status.matches_spec is True
    assert mgr.status(spec).matches_spec is True


def test_reserved_characters_are_quoted_and_backslash_escaped(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    spec = _spec(program="/opt/My $Apps/python3")
    assert mgr.install(spec).matches_spec is True
    # Two layers again: the Exec layer writes ``\$``, the key-file layer doubles
    # its backslash — the reader unwinds both back to a literal ``$``.
    assert 'Exec="/opt/My \\\\$Apps/python3" -m' in _entry_text(tmp_path)


def test_a_backslash_in_the_working_dir_is_escaped_and_still_round_trips(
    monkeypatch, tmp_path: Path
) -> None:
    """A directory name may legally contain a backslash on Linux.

    Written raw it turns the rest of ``Path=`` into a bogus escape sequence; the
    drift check has to decode the value back to compare it honestly.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    spec = _spec(working_dir="/home/u/odd\\dir")
    assert mgr.install(spec).matches_spec is True
    assert "Path=/home/u/odd\\\\dir" in _entry_text(tmp_path)
    assert mgr.status(spec).matches_spec is True


# ---- Status honesty ---------------------------------------------------------


def test_status_reports_drift_when_gnome_switched_the_entry_off(
    monkeypatch, tmp_path: Path
) -> None:
    """GNOME's Startup Applications writes ``X-GNOME-Autostart-enabled=false``.

    The file stays in place, so ignoring the key reported installed + current
    for an autostart that no longer runs — green toggle, no login start, no
    diagnosis anywhere.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    spec = _spec()
    mgr.install(spec)
    entry = tmp_path / "autostart" / "personal-jarvis.desktop"
    entry.write_text(
        _entry_text(tmp_path).replace(
            "X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-enabled=false"
        ),
        encoding="utf-8",
    )

    status = mgr.status(spec)
    assert status.installed is True
    assert status.matches_spec is False
    assert "switched off" in status.detail


def test_status_reports_drift_for_a_hidden_entry(monkeypatch, tmp_path: Path) -> None:
    """``Hidden=true`` is the spec's own "the user deleted this entry" marker."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    spec = _spec()
    mgr.install(spec)
    entry = tmp_path / "autostart" / "personal-jarvis.desktop"
    entry.write_text(
        _entry_text(tmp_path).replace("Hidden=false", "Hidden=true"), encoding="utf-8"
    )

    assert mgr.status(spec).matches_spec is False


def test_status_ignores_an_exec_from_another_group(monkeypatch, tmp_path: Path) -> None:
    """A ``[Desktop Action …]`` group legally carries its own ``Exec=``.

    A line-prefix scan can answer the drift check with that command and rewrite
    a perfectly good entry on every boot.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    spec = _spec()
    mgr.install(spec)
    entry = tmp_path / "autostart" / "personal-jarvis.desktop"
    entry.write_text(
        _entry_text(tmp_path)
        + "\n[Desktop Action new-window]\nExec=/somewhere/else --new\n",
        encoding="utf-8",
    )

    assert mgr.status(spec).matches_spec is True


def test_status_tolerates_spaces_around_the_equals_sign(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    spec = _spec()
    mgr.install(spec)
    entry = tmp_path / "autostart" / "personal-jarvis.desktop"
    entry.write_text(
        _entry_text(tmp_path).replace("Path=", "Path = "), encoding="utf-8"
    )

    assert mgr.status(spec).matches_spec is True


# ---- XDG base-directory compliance ------------------------------------------


def test_relative_xdg_config_home_is_ignored(monkeypatch, tmp_path: Path) -> None:
    """The XDG spec says a relative value is invalid and must be ignored.

    Honouring it wrote the entry under the process' working directory — a path
    no desktop environment reads, so autostart was dead while install()
    reported success.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", "~/.config")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))

    status = LinuxAutostart().install(_spec())

    assert (home / ".config" / "autostart" / "personal-jarvis.desktop").exists()
    assert status.entry_path == str(
        home / ".config" / "autostart" / "personal-jarvis.desktop"
    )


# ---- Write / remove failure honesty -----------------------------------------


def test_failed_install_leaves_no_temp_file_behind(monkeypatch, tmp_path: Path) -> None:
    """The successful replace() consumes the temp file — debris means a failure.

    A stray ``personal-jarvis.desktop.tmp`` next to the real entry is confusing
    at best; the macOS LaunchAgent writer already cleaned up after itself.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()

    def _boom(self: Path, target: object) -> None:
        raise OSError("simulated read-only home")

    monkeypatch.setattr(Path, "replace", _boom)
    with pytest.raises(OSError, match="simulated read-only home"):
        mgr.install(_spec())

    assert not (tmp_path / "autostart" / "personal-jarvis.desktop.tmp").exists()


def test_uninstall_reports_honestly_when_the_entry_survives(
    monkeypatch, tmp_path: Path
) -> None:
    """Claiming "Autostart disabled." while the .desktop is still there is a lie.

    The desktop keeps launching Jarvis at the next login, and the Settings
    toggle showed off with nothing to explain the mismatch (AP-30) — the same
    fix the macOS LaunchAgent already got.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    mgr.install(_spec())

    def _boom(self: Path, *, missing_ok: bool = False) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", _boom)
    status = mgr.uninstall()

    assert status.installed is True
    assert status.matches_spec is False
    assert "could not be removed" in status.detail
    assert (tmp_path / "autostart" / "personal-jarvis.desktop").exists()


def test_uninstall_cleans_up_leftover_temp_debris(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    mgr.install(_spec())
    debris = tmp_path / "autostart" / "personal-jarvis.desktop.tmp"
    debris.write_text("half written", encoding="utf-8")

    status = mgr.uninstall()

    assert not debris.exists()
    assert status.installed is False
