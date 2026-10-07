"""OS duties of the Qt sidecars: no Dock icon, a named missing library, a stderr log."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from jarvis.platform import qt_sidecar


@pytest.fixture
def x11(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


def test_a_missing_xcb_cursor_is_named(x11, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qt_sidecar.ctypes.util, "find_library", lambda _name: None)
    assert "libxcb-cursor0" in qt_sidecar.missing_system_library()


def test_a_present_xcb_cursor_says_nothing(x11, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qt_sidecar.ctypes.util, "find_library", lambda _n: "libxcb-cursor.so.0")
    assert qt_sidecar.missing_system_library() == ""


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_other_systems_never_need_it(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(qt_sidecar.ctypes.util, "find_library", lambda _name: None)
    assert qt_sidecar.missing_system_library() == ""


def test_a_crash_on_a_signal_points_at_the_cause(x11, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qt_sidecar.ctypes.util, "find_library", lambda _name: None)
    assert "libxcb-cursor0" in qt_sidecar.crash_hint(-6, "appshot-picker")
    assert qt_sidecar.crash_hint(1, "appshot-picker") == ""
    monkeypatch.setattr(qt_sidecar.ctypes.util, "find_library", lambda _n: "libxcb-cursor.so.0")
    assert "appshot-picker.log" in qt_sidecar.crash_hint(-11, "appshot-picker")


def test_stderr_goes_to_one_log_per_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qt_sidecar, "stderr_log_path", lambda name: tmp_path / f"{name}.log")
    first = qt_sidecar.stderr_sink("appshot-picker")
    second = qt_sidecar.stderr_sink("appshot-picker")

    assert first is not subprocess.DEVNULL and first.closed  # the earlier run's handle
    second.write(b"qt.qpa.plugin: could not load xcb\n")
    second.flush()
    assert b"xcb" in (tmp_path / "appshot-picker.log").read_bytes()
    second.close()


def test_the_dock_icon_is_hidden_on_a_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    policies: list[int] = []
    app = SimpleNamespace(setActivationPolicy_=policies.append)
    appkit = ModuleType("AppKit")
    appkit.NSApplication = SimpleNamespace(sharedApplication=lambda: app)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)

    monkeypatch.setattr(sys, "platform", "darwin")
    qt_sidecar.hide_from_dock()
    monkeypatch.setattr(sys, "platform", "linux")
    qt_sidecar.hide_from_dock()

    assert policies == [1]  # accessory, and only on macOS
