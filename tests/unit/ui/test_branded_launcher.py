"""Regression guard for the mascot-branded launcher exe (taskbar-icon fix).

The Windows taskbar button takes its icon from the executable that OWNS the
window — NOT the window/class icon, AUMID, Start-Menu shortcut, registry, or
icon cache (all verified to have no effect on the button). A bare
``pythonw.exe`` launch therefore shows the Python logo on the taskbar. The fix
re-execs the launcher through ``PersonalJarvis.exe`` — a copy of the *base*
interpreter's ``pythonw`` (the real window-owner; a venv launcher only
redirects to it) carrying the mascot icon, with the venv re-attached via
``__PYVENV_LAUNCHER__``. These tests pin the contract without spawning a GUI.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

from jarvis.ui import icon_utils


def test_branded_launcher_api_is_importable() -> None:
    for name in (
        "ensure_branded_launcher_exe",
        "maybe_reexec_through_branded_launcher",
        "BRANDED_LAUNCHER_EXE_NAME",
    ):
        assert hasattr(icon_utils, name)
    assert icon_utils.BRANDED_LAUNCHER_EXE_NAME.lower().endswith(".exe")


def test_launcher_wires_the_reexec() -> None:
    """main() must call the re-exec chokepoint, or nothing brands the taskbar."""
    import jarvis.ui.web.launcher as launcher

    src = inspect.getsource(launcher)
    assert "maybe_reexec_through_branded_launcher" in src


def test_non_windows_never_brands() -> None:
    """Everything is a no-op off Windows (Linux/macOS brand via .desktop/iconphoto)."""
    if sys.platform == "win32":
        pytest.skip("Windows path covered elsewhere")
    assert icon_utils.ensure_branded_launcher_exe() is None
    assert icon_utils.maybe_reexec_through_branded_launcher([]) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_reexec_is_guarded_against_relaunch_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env marker set on the child must short-circuit a second re-exec."""
    monkeypatch.setenv(icon_utils._BRANDED_LAUNCH_ENV, "1")
    assert icon_utils.maybe_reexec_through_branded_launcher(["--x"]) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_reexec_skips_a_debug_console_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A visible-console/debug run must not be swallowed by a windowless re-exec."""
    monkeypatch.delenv(icon_utils._BRANDED_LAUNCH_ENV, raising=False)
    monkeypatch.setenv("JARVIS_DEBUG", "1")
    assert icon_utils.maybe_reexec_through_branded_launcher([]) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_branded_target_sits_next_to_the_base_interpreter() -> None:
    """The branded copy must live beside the BASE pythonw (so it finds its DLLs)."""
    target = icon_utils._branded_launcher_path()
    if target is None:
        pytest.skip("no base pythonw resolvable in this environment")
    assert Path(target).parent == Path(sys.base_prefix)
    assert target.name == icon_utils.BRANDED_LAUNCHER_EXE_NAME


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_ms_store_alias_base_is_not_branded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 0-byte base exe (the MS Store app-execution alias) must bail gracefully."""
    fake_base = icon_utils._base_pythonw_executable()
    if fake_base is None:
        pytest.skip("no base pythonw resolvable")

    real_stat = os.stat

    class _ZeroStat:
        def __init__(self, st):
            self._st = st

        def __getattr__(self, k):
            if k == "st_size":
                return 0
            return getattr(self._st, k)

    def _fake_stat(path, *a, **k):
        st = real_stat(path, *a, **k)
        if Path(path) == fake_base:
            return _ZeroStat(st)
        return st

    monkeypatch.setattr(icon_utils.os, "stat", _fake_stat, raising=False)
    # Path.stat() is used in the code; patch that too.
    orig_path_stat = Path.stat

    def _fake_path_stat(self, *a, **k):
        st = orig_path_stat(self, *a, **k)
        if self == fake_base:
            return _ZeroStat(st)
        return st

    monkeypatch.setattr(Path, "stat", _fake_path_stat, raising=False)
    assert icon_utils.ensure_branded_launcher_exe() is None
