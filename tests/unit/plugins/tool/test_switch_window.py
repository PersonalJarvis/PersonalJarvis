"""H2: switch_window must work on macOS (Quartz/AppKit/AX) and Linux/X11,
degrade cleanly on Wayland / headless / missing-tool with a clear English
message, and leave the Windows ctypes path untouched (AD-7).

Seam-level only: platform APIs and subprocesses are faked. This proves the
dispatch and parsing, not behavior on real hardware.
"""
from __future__ import annotations

import subprocess
import sys
import types

from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.switch_window import (
    SwitchWindowTool,
    _find_and_focus_linux,
    _find_and_focus_macos,
)


def _cp(returncode: int, stdout: str = "", stderr: str = ""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _ctx() -> ExecutionContext:
    # switch_window.execute does not read ctx; supply the required fields.
    return ExecutionContext(
        config={}, trace_id=None, user_utterance="", memory_read=None
    )


# --- macOS (Quartz/AppKit/AX) ----------------------------------------------


def _install_fake_macos_apis(monkeypatch, *, trusted=True, minimized=False):
    from jarvis.platform.permissions import PermissionState

    ax_window = object()
    ax_root = object()
    calls: list[tuple] = []

    class _App:
        def activateWithOptions_(self, options):
            calls.append(("activate", options))
            return True

    appkit = types.SimpleNamespace(
        NSApplicationActivateAllWindows=1,
        NSApplicationActivateIgnoringOtherApps=2,
        NSRunningApplication=types.SimpleNamespace(
            runningApplicationWithProcessIdentifier_=lambda pid: _App(),
        ),
    )

    def copy_attr(element, attribute, _out):
        if element is ax_root and attribute == "AXWindows":
            return 0, [ax_window]
        if element is ax_window and attribute == "AXTitle":
            return 0, "My Editor"
        if element is ax_window and attribute == "AXMinimized":
            return 0, minimized
        return 1, None

    services = types.SimpleNamespace(
        AXIsProcessTrusted=lambda: trusted,
        AXUIElementCreateApplication=lambda pid: ax_root,
        AXUIElementCopyAttributeValue=copy_attr,
        AXUIElementPerformAction=lambda element, action: calls.append(
            ("perform", action)
        ) or 0,
        AXUIElementSetAttributeValue=lambda element, attr, value: calls.append(
            ("set", attr)
        ) or 0,
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "ApplicationServices", services)
    permission_port = types.SimpleNamespace(
        runtime_access_granted=lambda _permission_id: trusted,
        state=lambda _permission_id: (
            PermissionState.GRANTED
            if trusted
            else PermissionState.NOT_GRANTED
        ),
    )
    monkeypatch.setattr(
        "jarvis.platform.permissions.get_system_permission_port",
        lambda: permission_port,
    )
    return calls


def _mac_window(title="My Editor", *, on_screen=True):
    return {
        "kCGWindowLayer": 0,
        "kCGWindowName": title,
        "kCGWindowOwnerName": "Editor",
        "kCGWindowOwnerPID": 42,
        "kCGWindowNumber": 7,
        "kCGWindowIsOnscreen": on_screen,
    }


def test_macos_focus_success(monkeypatch):
    calls = _install_fake_macos_apis(monkeypatch)
    monkeypatch.setattr(
        "jarvis.platform.window_state._quartz_window_list",
        lambda **_kwargs: [_mac_window()],
    )
    found, msg = _find_and_focus_macos("Editor")
    assert found is True
    assert msg == "My Editor"
    assert ("set", "AXFocusedWindow") in calls
    assert ("perform", "AXRaise") in calls


def test_macos_focus_restores_minimized_window_from_all_window_catalog(monkeypatch):
    calls = _install_fake_macos_apis(monkeypatch, minimized=True)
    catalog_requests: list[bool] = []

    def catalog(*, on_screen_only=True):
        catalog_requests.append(on_screen_only)
        return [_mac_window(on_screen=False)]

    monkeypatch.setattr(
        "jarvis.platform.window_state._quartz_window_list",
        catalog,
    )
    found, msg = _find_and_focus_macos("Editor")

    assert found is True
    assert msg == "My Editor"
    assert catalog_requests == [False]
    assert ("set", "AXMinimized") in calls
    assert ("perform", "AXRaise") in calls
    assert calls.index(("set", "AXMinimized")) < calls.index(("perform", "AXRaise"))
    assert calls.index(("perform", "AXRaise")) < calls.index(("set", "AXFocusedWindow"))


def test_macos_accessibility_denied_message(monkeypatch):
    _install_fake_macos_apis(monkeypatch, trusted=False)
    monkeypatch.setattr(
        "jarvis.platform.window_state._quartz_window_list",
        lambda **_kwargs: [_mac_window()],
    )
    found, msg = _find_and_focus_macos("Editor")
    assert found is False
    assert "Accessibility" in msg


def test_macos_missing_native_frameworks(monkeypatch):
    monkeypatch.setattr(
        "jarvis.platform.window_state._quartz_window_list",
        lambda **_kwargs: [_mac_window()],
    )
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setitem(sys.modules, "ApplicationServices", None)
    found, msg = _find_and_focus_macos("Editor")
    assert found is False
    assert "unavailable" in msg.lower()


def test_macos_no_match(monkeypatch):
    monkeypatch.setattr(
        "jarvis.platform.window_state._quartz_window_list", lambda **_kwargs: [],
    )
    found, msg = _find_and_focus_macos("Nope")
    assert found is False
    assert "Nope" in msg


def test_macos_matching_is_case_insensitive(monkeypatch):
    _install_fake_macos_apis(monkeypatch)
    monkeypatch.setattr(
        "jarvis.platform.window_state._quartz_window_list",
        lambda **_kwargs: [_mac_window()],
    )
    found, msg = _find_and_focus_macos("EdItOr")
    assert found is True
    assert msg == "My Editor"


# --- Linux (wmctrl) --------------------------------------------------------


def test_linux_focus_success(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")

    def fake_run(cmd, *a, **k):
        if "-l" in cmd:
            listing = (
                "0x0123 0 host1 My Editor — file.py\n"
                "0x0456 0 host1 Terminal\n"
            )
            return _cp(0, stdout=listing)
        if "-a" in cmd:
            assert "0x0123" in cmd  # activates the matched id, not the other one
            return _cp(0)
        return _cp(1, stderr="unexpected")

    monkeypatch.setattr(subprocess, "run", fake_run)
    found, msg = _find_and_focus_linux("editor")
    assert found is True
    assert "My Editor" in msg


def test_linux_missing_wmctrl(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    found, msg = _find_and_focus_linux("Editor")
    assert found is False
    assert "wmctrl" in msg.lower()


def test_linux_no_match(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, *a, **k: _cp(0, stdout="0x0456 0 host1 Terminal\n")
    )
    found, msg = _find_and_focus_linux("Editor")
    assert found is False
    assert "Editor" in msg


# --- execute() dispatch + degrade ------------------------------------------


async def test_execute_routes_to_macos_with_english_readback(monkeypatch):
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.detect_platform", lambda: "darwin")
    monkeypatch.setattr(
        "jarvis.plugins.tool.switch_window._find_and_focus_macos",
        lambda t: (True, "My Editor"),
    )
    res = await SwitchWindowTool().execute({"title_contains": "Editor"}, _ctx())
    assert res.success is True
    assert res.output == "Focused window: My Editor"


async def test_execute_wayland_degrades_in_english(monkeypatch):
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.detect_platform", lambda: "linux")
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.is_wayland", lambda: True)
    res = await SwitchWindowTool().execute({"title_contains": "Editor"}, _ctx())
    assert res.success is False
    assert "Wayland" in res.error


async def test_execute_headless_linux_degrades(monkeypatch):
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.detect_platform", lambda: "linux")
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.is_wayland", lambda: False)
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.display_present", lambda: False)
    res = await SwitchWindowTool().execute({"title_contains": "Editor"}, _ctx())
    assert res.success is False
    assert "display" in res.error.lower()


async def test_execute_windows_path_unchanged(monkeypatch):
    # AD-7: the Windows readback keeps its established wording.
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.detect_platform", lambda: "win32")
    monkeypatch.setattr(
        "jarvis.plugins.tool.switch_window._find_and_focus_windows",
        lambda t: (True, "Notepad"),
    )
    res = await SwitchWindowTool().execute({"title_contains": "Notepad"}, _ctx())
    assert res.success is True
    assert res.output == "Focused window: Notepad"
