"""Tests for the capability probes (Wave 0, sub-task 0.2).

Probes are patched via monkeypatched env / ``find_spec`` so each platform branch
is exercised from any host. Acceptance: every probe runs without raising on
every OS and returns the documented type.
"""

from __future__ import annotations

import pytest

from jarvis.platform import probes


def _force_platform(monkeypatch, name: str):
    """Make both the package-level and probe-level detect_platform return name."""
    monkeypatch.setattr("jarvis.platform.detect_platform", lambda: name)
    monkeypatch.setattr("jarvis.platform.probes.detect_platform", lambda: name)


# --- display_present -------------------------------------------------------


def test_display_present_true_on_windows_and_macos(monkeypatch):
    _force_platform(monkeypatch, "win32")
    assert probes.display_present() is True
    _force_platform(monkeypatch, "darwin")
    assert probes.display_present() is True


def test_display_present_linux_depends_on_env(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert probes.display_present() is False
    monkeypatch.setenv("DISPLAY", ":0")
    assert probes.display_present() is True


# --- is_wayland ------------------------------------------------------------


def test_is_wayland_false_off_linux(monkeypatch):
    _force_platform(monkeypatch, "win32")
    assert probes.is_wayland() is False
    _force_platform(monkeypatch, "darwin")
    assert probes.is_wayland() is False


def test_is_wayland_detects_session_type(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert probes.is_wayland() is True
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert probes.is_wayland() is False
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert probes.is_wayland() is True


def test_is_wayland_runs_on_every_os_without_raising():
    # Acceptance: exits cleanly regardless of host.
    assert isinstance(probes.is_wayland(), bool)


# --- has_hotkey ------------------------------------------------------------


def test_has_hotkey_false_on_wayland(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(probes, "is_wayland", lambda: True)
    monkeypatch.setattr(probes, "_has_module", lambda n: True)
    assert probes.has_hotkey() is False  # AD-8: Wayland blocks global hotkeys


def test_has_hotkey_true_on_x11_with_pynput(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(probes, "is_wayland", lambda: False)
    monkeypatch.setattr(probes, "_has_module", lambda n: n == "pynput")
    assert probes.has_hotkey() is True


def test_has_hotkey_windows_uses_global_hotkeys(monkeypatch):
    _force_platform(monkeypatch, "win32")
    monkeypatch.setattr(probes, "_has_module", lambda n: n == "global_hotkeys")
    assert probes.has_hotkey() is True


# --- has_ax_tree -----------------------------------------------------------


@pytest.mark.parametrize(
    "plat,module",
    [("win32", "pywinauto"), ("darwin", "Quartz"), ("linux", "pyatspi")],
)
def test_has_ax_tree_per_platform_module(monkeypatch, plat, module):
    _force_platform(monkeypatch, plat)
    monkeypatch.setattr(probes, "_has_module", lambda n, m=module: n == m)
    assert probes.has_ax_tree() is True


# --- ax_permission_granted (tri-state) -------------------------------------


def test_ax_permission_windows_always_true(monkeypatch):
    _force_platform(monkeypatch, "win32")
    assert probes.ax_permission_granted() is True


def test_ax_permission_macos_unknown_without_pyobjc(monkeypatch):
    import sys

    _force_platform(monkeypatch, "darwin")
    monkeypatch.setitem(sys.modules, "ApplicationServices", None)
    assert probes.ax_permission_granted() is None


def test_ax_permission_linux_needs_bus(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.delenv("AT_SPI_BUS", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    assert probes.ax_permission_granted() is False
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    assert probes.ax_permission_granted() is True


# --- screen_recording_granted (tri-state, H1) ------------------------------


def test_screen_recording_true_off_darwin(monkeypatch):
    # Only macOS gates screenshots behind a TCC Screen-Recording grant; Windows
    # and Linux need no per-app grant.
    _force_platform(monkeypatch, "win32")
    assert probes.screen_recording_granted() is True
    _force_platform(monkeypatch, "linux")
    assert probes.screen_recording_granted() is True


def test_screen_recording_macos_reflects_preflight(monkeypatch):
    import sys
    import types

    _force_platform(monkeypatch, "darwin")
    # Replaces any already-cached Quartz module so the function-local
    # `from Quartz import ...` re-resolves to this fake (matters on a real Mac
    # with pyobjc installed, where the genuine module would otherwise win).
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        types.SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: True),
    )
    assert probes.screen_recording_granted() is True
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        types.SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: False),
    )
    assert probes.screen_recording_granted() is False


def test_screen_recording_macos_unknown_without_quartz(monkeypatch):
    import sys

    _force_platform(monkeypatch, "darwin")
    # pyobjc-Quartz absent → import raises → unknown (None), never a hard False.
    monkeypatch.setitem(sys.modules, "Quartz", None)
    assert probes.screen_recording_granted() is None


# --- has_elevation ---------------------------------------------------------


def test_has_elevation_macos_always_true(monkeypatch):
    _force_platform(monkeypatch, "darwin")
    assert probes.has_elevation() is True


def test_has_elevation_linux_needs_pkexec_or_sudo(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(probes.shutil, "which", lambda n: "/usr/bin/sudo" if n == "sudo" else None)
    assert probes.has_elevation() is True
    monkeypatch.setattr(probes.shutil, "which", lambda n: None)
    assert probes.has_elevation() is False


# --- has_pty / has_overlay -------------------------------------------------


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_has_pty_posix_requires_implemented_ptyprocess_backend(
    monkeypatch, platform
):
    _force_platform(monkeypatch, platform)
    monkeypatch.setattr(probes, "_has_module", lambda n: n == "ptyprocess")
    assert probes.has_pty() is True


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_has_pty_posix_rejects_unimplemented_stdlib_pty_fallback(
    monkeypatch, platform
):
    _force_platform(monkeypatch, platform)
    monkeypatch.setattr(probes, "_has_module", lambda n: n == "pty")
    assert probes.has_pty() is False


def test_has_overlay_requires_display_and_tkinter(monkeypatch):
    monkeypatch.setattr(probes, "display_present", lambda: True)
    monkeypatch.setattr(probes, "_has_module", lambda n: n == "tkinter")
    assert probes.has_overlay() is True
    monkeypatch.setattr(probes, "display_present", lambda: False)
    assert probes.has_overlay() is False


def test_all_probes_run_without_raising_on_host():
    # Smoke: every probe returns a bool/None on the real host.
    assert isinstance(probes.display_present(), bool)
    assert isinstance(probes.has_pty(), bool)
    assert isinstance(probes.has_ax_tree(), bool)
    assert isinstance(probes.has_hotkey(), bool)
    assert isinstance(probes.has_overlay(), bool)
    assert isinstance(probes.has_elevation(), bool)
    assert probes.ax_permission_granted() in (True, False, None)
    assert probes.screen_recording_granted() in (True, False, None)
