"""Restart and update relaunch of a FROZEN (natively installed) build.

A PyInstaller build has no ``python -m``: its executable IS the app, and its
argument parser rejects ``-m``. Before these paths existed, every Restart and
every update restart of a native install spawned ``PersonalJarvis -m
jarvis.ui.relauncher …``, which exited at once with a usage error — the app
quit and never came back, on every OS.

Everything here injects the platform, so the Windows, macOS and Linux shapes
are all checked on whichever OS runs the suite.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

import jarvis.ui.relauncher as relauncher
from jarvis.ui.relauncher import (
    RELAUNCHER_FLAG,
    build_launch_command,
    frozen_self_command,
    relauncher_command,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------- #
# relauncher_command — how the detached restart helper is started
# --------------------------------------------------------------------------- #
def test_source_install_runs_the_helper_through_python_m() -> None:
    argv = relauncher_command(
        42, "/repo", ("--port", "8000"), executable="/usr/bin/python3", frozen=False
    )
    assert argv == [
        "/usr/bin/python3",
        "-m",
        "jarvis.ui.relauncher",
        "42",
        "/repo",
        "--port",
        "8000",
    ]


def test_frozen_windows_re_enters_its_own_exe_with_the_helper_flag() -> None:
    exe = r"C:\Program Files\Personal Jarvis\PersonalJarvis.exe"
    argv = relauncher_command(
        42,
        r"C:\Program Files\Personal Jarvis\_internal",
        ("--port", "8000"),
        executable=exe,
        frozen=True,
        platform_name="win32",
        environ={},
    )
    assert argv == [exe, RELAUNCHER_FLAG, "42", r"C:\Program Files\Personal Jarvis\_internal"]
    # ``-m`` is exactly what the frozen parser rejects.
    assert "-m" not in argv


def test_frozen_linux_runs_the_helper_through_the_appimage() -> None:
    """``sys.executable`` sits in the AppImage's private mount, which vanishes
    with the old process — the helper must own a mount of its own."""
    argv = relauncher_command(
        7,
        "/run/appimage/.mount_abc/_internal",
        executable="/run/appimage/.mount_abc/PersonalJarvis",
        frozen=True,
        platform_name="linux",
        environ={"APPIMAGE": "/home/me/Apps/PersonalJarvis.AppImage"},
    )
    assert argv == [
        "/home/me/Apps/PersonalJarvis.AppImage",
        RELAUNCHER_FLAG,
        "7",
        "/run/appimage/.mount_abc/_internal",
    ]


def test_frozen_macos_runs_the_helper_binary_not_a_second_app(tmp_path: Path) -> None:
    exe = tmp_path / "Personal Jarvis.app" / "Contents" / "MacOS" / "PersonalJarvis"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    argv = relauncher_command(
        7, "/x", executable=str(exe), frozen=True, platform_name="darwin", environ={}
    )
    assert argv[0] == str(exe)
    assert argv[1] == RELAUNCHER_FLAG


# --------------------------------------------------------------------------- #
# frozen_self_command — how the fresh app is started
# --------------------------------------------------------------------------- #
def test_frozen_windows_relaunches_the_exe_itself() -> None:
    exe = r"C:\Apps\PersonalJarvis.exe"
    assert frozen_self_command(exe, platform_name="win32", environ={}) == [exe]


def test_frozen_linux_relaunches_the_appimage_file() -> None:
    assert frozen_self_command(
        "/run/appimage/.mount_x/PersonalJarvis",
        platform_name="linux",
        environ={"APPIMAGE": "/opt/PersonalJarvis.AppImage"},
    ) == ["/opt/PersonalJarvis.AppImage"]


def test_frozen_linux_without_an_appimage_relaunches_the_binary() -> None:
    """An unpacked tarball or .deb build has no $APPIMAGE; the binary is stable."""
    assert frozen_self_command(
        "/opt/jarvis/PersonalJarvis", platform_name="linux", environ={"APPIMAGE": "  "}
    ) == ["/opt/jarvis/PersonalJarvis"]


def test_frozen_macos_relaunches_through_launch_services(tmp_path: Path) -> None:
    app = tmp_path / "Personal Jarvis.app"
    exe = app / "Contents" / "MacOS" / "PersonalJarvis"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    command = frozen_self_command(str(exe), platform_name="darwin", environ={})
    # -n: a new instance even though the old one is still on its way out;
    # -W: ``open`` stays alive with the app, so the helper can verify it is up.
    assert command == ["/usr/bin/open", "-n", "-W", str(app.resolve())]


def test_frozen_macos_outside_a_bundle_falls_back_to_the_binary(tmp_path: Path) -> None:
    exe = tmp_path / "PersonalJarvis"
    exe.write_text("", encoding="utf-8")
    assert frozen_self_command(str(exe), platform_name="darwin", environ={}) == [str(exe)]


def test_build_launch_command_never_uses_python_m_when_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(relauncher, "_frozen", lambda: True)
    command = build_launch_command(sys.executable, launcher_args=("--port", "1"))
    assert "-m" not in command
    assert relauncher.LAUNCHER_MODULE not in command


def test_desktop_restart_builds_its_helper_through_relauncher_command() -> None:
    """The one place that spawns the helper must not hand-roll ``-m`` again."""
    from jarvis.ui.desktop_app import DesktopApp

    source = inspect.getsource(DesktopApp._schedule_restart)
    assert "relauncher_command(" in source
    assert '"-m"' not in source


# --------------------------------------------------------------------------- #
# The entry point really dispatches the helper flag
# --------------------------------------------------------------------------- #
def test_entry_point_dispatches_the_relauncher_flag() -> None:
    """``jarvis --relauncher`` must reach relauncher.main, not argparse.

    With a non-numeric PID relauncher.main returns 2 without spawning anything;
    argparse would ALSO exit 2, but only argparse prints a usage line.
    """
    result = subprocess.run(
        [sys.executable, "-m", "jarvis", RELAUNCHER_FLAG, "not-a-pid", str(REPO_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert result.returncode == 2
    assert "usage:" not in result.stderr.lower()


# --------------------------------------------------------------------------- #
# restart_workdir — where the helper runs and reads jarvis.toml from
# --------------------------------------------------------------------------- #
def test_source_install_restarts_from_its_checkout() -> None:
    assert relauncher.restart_workdir("/repo", frozen=False, environ={}) == "/repo"


def test_frozen_build_restarts_from_the_config_directory(tmp_path: Path) -> None:
    """An AppImage bundle is a mount that dies with the old process."""
    config = tmp_path / "jarvis.toml"
    config.write_text("", encoding="utf-8")
    assert relauncher.restart_workdir(
        "/run/appimage/.mount_abc", frozen=True, environ={"JARVIS_CONFIG": str(config)}
    ) == str(tmp_path)


def test_frozen_build_without_a_config_falls_back_to_home() -> None:
    assert relauncher.restart_workdir("/run/appimage/.mount_abc", frozen=True, environ={}) == str(
        Path.home()
    )
