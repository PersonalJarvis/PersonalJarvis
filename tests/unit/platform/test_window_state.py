"""Phase 0: jarvis/platform/window_state.py — reusable, cross-platform window
enumeration / focus / already-running detection behind the platform seam.

Seam-level only: the platform is forced via detect_platform/probes and the
ctypes/Quartz/AppKit/AX/wmctrl backends are faked or monkeypatched — this proves the
dispatch + parsing + matching logic, NOT that real OS APIs behave as assumed on
real hardware (SIGNOFF-LOG honesty).
"""
from __future__ import annotations

import subprocess
import sys
import types

from jarvis.platform import window_state as ws
from jarvis.platform.window_state import WindowInfo


def _cp(returncode: int, stdout: str = "", stderr: str = ""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _patch_macos_accessibility(monkeypatch, *, granted: bool) -> None:
    from jarvis.platform.permissions import PermissionState

    port = types.SimpleNamespace(
        runtime_access_granted=lambda _permission_id: granted,
        state=lambda _permission_id: (
            PermissionState.GRANTED
            if granted
            else PermissionState.NOT_GRANTED
        ),
    )
    monkeypatch.setattr(
        "jarvis.platform.permissions.get_system_permission_port",
        lambda: port,
    )


# --- WindowInfo basics ------------------------------------------------------


def test_windowinfo_defaults():
    w = WindowInfo("Some Title")
    assert w.title == "Some Title"
    assert w.minimized is False
    assert w.handle is None


# --- is_app_running (pure matching over list_windows) -----------------------


def test_is_app_running_matches_title_substring(monkeypatch):
    monkeypatch.setattr(
        ws, "list_windows",
        lambda: [WindowInfo("OBS 30.0.0 - Profil: Stream"), WindowInfo("Chrome")],
    )
    hit = ws.is_app_running("obs")
    assert hit is not None
    assert "OBS" in hit.title


def test_is_app_running_returns_none_when_no_match(monkeypatch):
    monkeypatch.setattr(ws, "list_windows", lambda: [WindowInfo("New Tab - Google Chrome")])
    assert ws.is_app_running("obs") is None


def test_is_app_running_alias_calc_to_calculator(monkeypatch):
    monkeypatch.setattr(ws, "list_windows", lambda: [WindowInfo("Calculator")])
    assert ws.is_app_running("calc") is not None


def test_is_app_running_short_token_is_not_fuzzy_matched(monkeypatch):
    # A 1-2 char app token must not match arbitrary titles (false-positive guard:
    # wrongly focusing instead of launching is the worse error).
    monkeypatch.setattr(ws, "list_windows", lambda: [WindowInfo("Notepad")])
    assert ws.is_app_running("n") is None


def test_is_app_running_keeps_handle_for_focus(monkeypatch):
    monkeypatch.setattr(ws, "list_windows", lambda: [WindowInfo("OBS 30", handle=4242)])
    hit = ws.is_app_running("obs")
    assert hit is not None
    assert hit.handle == 4242


def test_is_app_running_accepts_exe_path_basename(monkeypatch):
    # app_name may arrive as a path/exe; the basename stem is used as the token.
    monkeypatch.setattr(ws, "list_windows", lambda: [WindowInfo("Discord")])
    assert ws.is_app_running(r"C:\Users\x\AppData\Local\Discord\Discord.exe") is not None


def test_is_app_running_empty_name_is_none(monkeypatch):
    monkeypatch.setattr(ws, "list_windows", lambda: [WindowInfo("Anything")])
    assert ws.is_app_running("") is None
    assert ws.is_app_running("   ") is None


# --- focus_window dispatch --------------------------------------------------


def test_focus_window_dispatches_windows(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    monkeypatch.setattr(ws, "_find_and_focus_windows", lambda t: (True, "Notepad"))
    assert ws.focus_window("Notepad") == (True, "Notepad")


def test_focus_window_dispatches_macos(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(ws, "_find_and_focus_macos", lambda t: (True, "My Editor"))
    assert ws.focus_window("Editor") == (True, "My Editor")


def test_focus_window_wayland_degrades(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: True)
    ok, msg = ws.focus_window("Editor")
    assert ok is False
    assert "Wayland" in msg


def test_focus_window_headless_linux_degrades(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: False)
    monkeypatch.setattr(ws, "display_present", lambda: False)
    ok, msg = ws.focus_window("Editor")
    assert ok is False
    assert "display" in msg.lower()


def test_focus_window_never_raises(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")

    def boom(_t):
        raise RuntimeError("ctypes blew up")

    monkeypatch.setattr(ws, "_find_and_focus_windows", boom)
    ok, msg = ws.focus_window("X")
    assert ok is False
    assert "X" in msg or "failed" in msg.lower() or msg


# --- list_windows dispatch + parsing ----------------------------------------


def test_list_windows_windows_dispatch(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    monkeypatch.setattr(
        ws, "_list_windows_windows",
        lambda: [WindowInfo("OBS 30", minimized=True), WindowInfo("Chrome")],
    )
    titles = [w.title for w in ws.list_windows()]
    assert titles == ["OBS 30", "Chrome"]
    assert ws.list_windows()[0].minimized is True


def test_list_windows_linux_parses_wmctrl(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: False)
    monkeypatch.setattr(ws, "display_present", lambda: True)
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _cp(0, stdout="0x01 0 host My Editor — file.py\n0x02 0 host Terminal\n"),
    )
    titles = [w.title for w in ws.list_windows()]
    assert "My Editor — file.py" in titles
    assert "Terminal" in titles


def test_list_windows_macos_parses_quartz_catalog(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(
        ws,
        "_quartz_window_list",
        lambda **_kwargs: [
            {"kCGWindowLayer": 0, "kCGWindowName": "OBS", "kCGWindowNumber": 1},
            {
                "kCGWindowLayer": 0,
                "kCGWindowName": "Safari — Apple",
                "kCGWindowNumber": 2,
                "kCGWindowIsOnscreen": False,
            },
            {"kCGWindowLayer": 8, "kCGWindowName": "Overlay", "kCGWindowNumber": 3},
        ],
    )
    titles = [w.title for w in ws.list_windows()]
    assert "OBS" in titles
    assert "Safari — Apple" in titles
    assert "Overlay" not in titles
    safari = next(window for window in ws.list_windows() if window.title == "Safari — Apple")
    assert safari.minimized is False, "another Space is not the same as minimized"


def test_list_windows_macos_reads_ax_minimized_instead_of_onscreen_flag(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    _patch_macos_accessibility(monkeypatch, granted=True)
    monkeypatch.setattr(
        ws,
        "_quartz_window_list",
        lambda **_kwargs: [{
            "kCGWindowLayer": 0,
            "kCGWindowName": "Hidden notes",
            "kCGWindowOwnerPID": 42,
            "kCGWindowNumber": 9,
            "kCGWindowIsOnscreen": False,
        }],
    )
    root = object()
    window = object()

    def copy_attr(element, attribute, _out):
        if element is root and attribute == "AXWindows":
            return 0, [window]
        if element is window and attribute == "AXTitle":
            return 0, "Hidden notes"
        if element is window and attribute == "AXMinimized":
            return 0, True
        return 1, None

    monkeypatch.setitem(sys.modules, "ApplicationServices", types.SimpleNamespace(
        AXUIElementCreateApplication=lambda _pid: root,
        AXUIElementCopyAttributeValue=copy_attr,
    ))

    windows = ws.list_windows()

    assert len(windows) == 1
    assert windows[0].minimized is True


def test_list_windows_linux_decodes_non_utf8_locale_titles(monkeypatch):
    # AP-23 wave-2 finding 8: wmctrl output is parsed with text=True; without a
    # pinned encoding="utf-8", errors="replace", a title byte sequence that the
    # ambient locale can't decode raises UnicodeDecodeError instead of just
    # degrading the one unparsable character. The fake below mimics that
    # contract: it only returns cleanly when both kwargs are set exactly as the
    # fix requires, so this test is RED on the pre-fix code and GREEN after.
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: False)
    monkeypatch.setattr(ws, "display_present", lambda: True)
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")

    def _fake_run(*args, **kwargs):
        if kwargs.get("encoding") != "utf-8" or kwargs.get("errors") != "replace":
            raise UnicodeDecodeError("cp1252", b"\xff\xfe", 0, 1, "invalid byte")
        return _cp(0, stdout="0x01 0 host café — 日本語.txt\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    titles = [w.title for w in ws.list_windows()]
    assert any("café" in t for t in titles)


def test_list_windows_macos_preserves_unicode_quartz_titles(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(
        ws,
        "_quartz_window_list",
        lambda **_kwargs: [{
            "kCGWindowLayer": 0,
            "kCGWindowName": "Safäri — 日本語",  # i18n-allow: non-ASCII title fixture under test
            "kCGWindowNumber": 1,
        }],
    )
    titles = [w.title for w in ws.list_windows()]
    assert any("Safäri" in t for t in titles)  # i18n-allow: non-ASCII title fixture under test


def test_list_windows_headless_linux_is_empty(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: False)
    monkeypatch.setattr(ws, "display_present", lambda: False)
    assert ws.list_windows() == []


def test_list_windows_never_raises(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")

    def boom():
        raise RuntimeError("enum blew up")

    monkeypatch.setattr(ws, "_list_windows_windows", boom)
    assert ws.list_windows() == []


# --- get_foreground_title ---------------------------------------------------


def test_get_foreground_title_never_raises(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")

    def boom():
        raise RuntimeError("foreground blew up")

    monkeypatch.setattr(ws, "_foreground_title_windows", boom)
    assert ws.get_foreground_title() == ""


def test_get_foreground_title_macos_empty_without_tool(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    monkeypatch.setattr("shutil.which", lambda n: None)
    # Block the native backends so the degrade path is exercised even on a
    # real macOS host where pyobjc is installed.
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setitem(sys.modules, "Quartz", None)
    assert ws.get_foreground_title() == ""


def test_get_foreground_title_macos_uses_frontmost_app_pid(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")

    class _App:
        def processIdentifier(self):
            return 42

        def localizedName(self):
            return "Finder"

    workspace = types.SimpleNamespace(frontmostApplication=lambda: _App())
    appkit = types.SimpleNamespace(
        NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: workspace),
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setattr(
        ws,
        "_quartz_window_list",
        lambda: [
            {
                "kCGWindowLayer": 0,
                "kCGWindowOwnerPID": 7,
                "kCGWindowName": "Wrong app",
            },
            {
                "kCGWindowLayer": 0,
                "kCGWindowOwnerPID": 42,
                "kCGWindowName": "Overview — document.txt",
            },
        ],
    )

    assert ws.get_foreground_title() == "Overview — document.txt"


def test_get_foreground_title_linux_decodes_non_utf8_locale_title(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: False)
    monkeypatch.setattr(ws, "display_present", lambda: True)
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")

    def _fake_run(*args, **kwargs):
        if kwargs.get("encoding") != "utf-8" or kwargs.get("errors") != "replace":
            raise UnicodeDecodeError("cp1252", b"\xff\xfe", 0, 1, "invalid byte")
        return _cp(
            0,
            stdout="Übersicht — Müller.txt",  # i18n-allow: umlaut title fixture
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert (
        ws.get_foreground_title()
        == "Übersicht — Müller.txt"  # i18n-allow: umlaut title fixture
    )


# --- raise_window (raise a known window via the hardened path) ---------------


def test_raise_window_windows_uses_handle(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    forced: list = []
    monkeypatch.setattr(ws, "_force_foreground_windows", lambda h: forced.append(h) or True)
    monkeypatch.setattr(
        ws,
        "focus_window",
        lambda t: (_ for _ in ()).throw(AssertionError("title path used")),
    )
    ok, title = ws.raise_window(WindowInfo("WhatsApp", handle=777))
    assert ok is True
    assert title == "WhatsApp"
    assert forced == [777]


def test_raise_window_non_windows_uses_title(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    seen: list = []
    monkeypatch.setattr(ws, "focus_window", lambda t: (seen.append(t), (True, t))[1])
    ok, _title = ws.raise_window(WindowInfo("WhatsApp", handle=None))
    assert ok is True
    assert seen == ["WhatsApp"]


def test_raise_window_windows_without_handle_falls_back_to_title(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    seen: list = []
    monkeypatch.setattr(ws, "focus_window", lambda t: (seen.append(t), (True, t))[1])
    ok, _title = ws.raise_window(WindowInfo("WhatsApp", handle=None))
    assert ok is True
    assert seen == ["WhatsApp"]


def test_raise_window_never_raises(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")

    def boom(_h):
        raise RuntimeError("foreground blew up")

    monkeypatch.setattr(ws, "_force_foreground_windows", boom)
    ok, _msg = ws.raise_window(WindowInfo("WhatsApp", handle=5))
    assert ok is False


# --- raise_after_launch (poll for the new window, then actively foreground it) -


def test_raise_after_launch_windows_uses_hardened_path(monkeypatch):
    # The window appears on the 3rd poll; on Windows we raise it by hwnd via the
    # hardened foreground path, never the title path.
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    polls: list[int] = []

    def _list():
        polls.append(1)
        if len(polls) < 3:
            return []
        return [WindowInfo("New Tab - Google Chrome", handle=4242)]

    monkeypatch.setattr(ws, "list_windows", _list)
    forced: list[int] = []
    monkeypatch.setattr(
        ws, "_force_foreground_windows", lambda h: (forced.append(h), True)[1]
    )
    # focus_window (title path) must NOT be used on Windows.
    monkeypatch.setattr(
        ws,
        "focus_window",
        lambda t: (_ for _ in ()).throw(AssertionError("title path used on win32")),
    )

    ok, title = ws.raise_after_launch("chrome", timeout_s=1.0, poll_s=0.001)
    assert ok is True
    assert title == "New Tab - Google Chrome"
    assert forced == [4242]
    assert len(polls) >= 3


def test_raise_after_launch_macos_uses_title_focus(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(ws, "list_windows", lambda: [WindowInfo("Spotify Premium")])
    focused: list[str] = []
    monkeypatch.setattr(
        ws, "focus_window", lambda t: (focused.append(t), (True, t))[1]
    )
    ok, title = ws.raise_after_launch("spotify", timeout_s=0.1, poll_s=0.001)
    assert ok is True
    assert focused == ["Spotify Premium"]


def test_raise_after_launch_gives_up_when_window_never_appears(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    monkeypatch.setattr(ws, "list_windows", lambda: [WindowInfo("Program Manager")])
    monkeypatch.setattr(ws, "_force_foreground_windows", lambda h: True)
    import time as _t

    start = _t.monotonic()
    ok, _msg = ws.raise_after_launch("spotify", timeout_s=0.05, poll_s=0.005)
    assert ok is False
    assert _t.monotonic() - start < 1.0  # bounded by timeout, never wedges


def test_raise_after_launch_short_token_skips_polling(monkeypatch):
    # A 1-2 char token must not poll/match arbitrary windows.
    called: list[int] = []
    monkeypatch.setattr(ws, "list_windows", lambda: called.append(1) or [])
    ok, _msg = ws.raise_after_launch("x", timeout_s=1.0, poll_s=0.001)
    assert ok is False
    assert called == []  # returned before any enumeration


def test_raise_after_launch_never_raises(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")

    def boom():
        raise RuntimeError("enum blew up")

    monkeypatch.setattr(ws, "list_windows", boom)
    ok, _msg = ws.raise_after_launch("chrome", timeout_s=0.1, poll_s=0.001)
    assert ok is False


def test_raise_after_launch_maximizes_the_exact_window_when_requested(monkeypatch):
    # maximize=True → the freshly raised window is filled to its monitor, on the
    # EXACT WindowInfo just found (user request 2026-07-23: a launched app must
    # not sit tiny in a big desktop).
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    win = WindowInfo("New Tab - Google Chrome", handle=4242)
    monkeypatch.setattr(ws, "list_windows", lambda: [win])
    monkeypatch.setattr(ws, "_force_foreground_windows", lambda h: True)
    maximized: list[WindowInfo] = []
    monkeypatch.setattr(
        ws, "maximize_window", lambda w: (maximized.append(w), (True, "maximized"))[1]
    )

    ok, _title = ws.raise_after_launch(
        "chrome", timeout_s=1.0, poll_s=0.001, maximize=True
    )
    assert ok is True
    assert maximized == [win], "maximize must target the exact window just raised"


def test_raise_after_launch_does_not_maximize_by_default(monkeypatch):
    # Default (no maximize) must NOT touch the window size — protects every
    # existing caller and the already-running focus path.
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    monkeypatch.setattr(
        ws, "list_windows", lambda: [WindowInfo("Spotify Premium", handle=7)]
    )
    monkeypatch.setattr(ws, "_force_foreground_windows", lambda h: True)
    monkeypatch.setattr(
        ws,
        "maximize_window",
        lambda w: (_ for _ in ()).throw(AssertionError("maximized without opt-in")),
    )

    ok, _title = ws.raise_after_launch("spotify", timeout_s=1.0, poll_s=0.001)
    assert ok is True


def test_raise_after_launch_skips_maximize_when_raise_fails(monkeypatch):
    # A failed raise must never maximize — otherwise a stale/sibling foreground
    # window would be blown up instead of the intended app.
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    monkeypatch.setattr(
        ws, "list_windows", lambda: [WindowInfo("New Tab - Google Chrome", handle=9)]
    )
    monkeypatch.setattr(ws, "_force_foreground_windows", lambda h: False)  # raise fails
    monkeypatch.setattr(
        ws,
        "maximize_window",
        lambda w: (_ for _ in ()).throw(AssertionError("maximized after a failed raise")),
    )

    ok, _title = ws.raise_after_launch(
        "chrome", timeout_s=0.05, poll_s=0.001, maximize=True
    )
    assert ok is False


# --- native macOS maximize -------------------------------------------------


def test_maximize_window_macos_uses_native_axzoomed(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    _patch_macos_accessibility(monkeypatch, granted=True)
    monkeypatch.setattr(
        ws,
        "_quartz_window_list",
        lambda **_kwargs: [{
            "kCGWindowNumber": 77,
            "kCGWindowOwnerPID": 42,
            "kCGWindowOwnerName": "Editor",
            "kCGWindowName": "notes.txt",
        }],
    )
    root = object()
    window = object()
    writes: list[tuple[object, str, object]] = []

    def copy_attr(element, attribute, _out):
        if element is root and attribute == "AXWindows":
            return 0, [window]
        if element is window and attribute == "AXTitle":
            return 0, "notes.txt"
        return 1, None

    services = types.SimpleNamespace(
        AXIsProcessTrusted=lambda: True,
        AXUIElementCreateApplication=lambda pid: root,
        AXUIElementCopyAttributeValue=copy_attr,
        AXUIElementSetAttributeValue=lambda element, attribute, value: (
            writes.append((element, attribute, value)) or 0
        ),
    )
    appkit = types.SimpleNamespace(NSWorkspace=object())
    monkeypatch.setitem(sys.modules, "ApplicationServices", services)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("native macOS maximize must not invoke osascript"),
        ),
    )

    ok, message = ws.maximize_window(WindowInfo("notes.txt", handle=77))

    assert ok is True
    assert "zoomed" in message
    assert writes == [(window, "AXZoomed", True)]


def test_normalize_window_macos_reads_and_sets_native_axzoomed(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    _patch_macos_accessibility(monkeypatch, granted=True)
    target_info = WindowInfo("notes.txt", handle=77)
    monkeypatch.setattr(ws, "foreground_window", lambda: target_info)
    monkeypatch.setattr(ws, "is_shell_window", lambda _window: False)
    monkeypatch.setattr(
        ws,
        "_quartz_window_list",
        lambda **_kwargs: [{
            "kCGWindowNumber": 77,
            "kCGWindowOwnerPID": 42,
            "kCGWindowName": "notes.txt",
        }],
    )
    root = object()
    window = object()
    writes: list[tuple[object, str, object]] = []

    def copy_attr(element, attribute, _out):
        if element is root and attribute == "AXWindows":
            return 0, [window]
        if element is window and attribute == "AXTitle":
            return 0, "notes.txt"
        if element is window and attribute == "AXZoomed":
            return 0, False
        return 1, None

    services = types.SimpleNamespace(
        AXIsProcessTrusted=lambda: True,
        AXUIElementCreateApplication=lambda _pid: root,
        AXUIElementCopyAttributeValue=copy_attr,
        AXUIElementSetAttributeValue=lambda element, attribute, value: (
            writes.append((element, attribute, value)) or 0
        ),
    )
    monkeypatch.setitem(sys.modules, "ApplicationServices", services)
    monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace(NSWorkspace=object()))

    ok, message = ws.normalize_foreground_window()

    assert ok is True
    assert "zoomed" in message
    assert writes == [(window, "AXZoomed", True)]


def test_macos_full_screen_counts_as_maximized(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    _patch_macos_accessibility(monkeypatch, granted=True)
    target = object()
    monkeypatch.setattr(ws, "_resolve_macos_ax_window", lambda _win: (target, ""))
    monkeypatch.setattr(
        ws,
        "_macos_ax_attr",
        lambda element, attribute: (
            True
            if element is target and attribute == "AXFullScreen"
            else False if element is target and attribute == "AXZoomed"
            else None
        ),
    )

    assert ws.window_is_maximized(WindowInfo("Full-screen document", handle=77)) is True


def test_resolve_macos_ax_window_uses_bounds_for_duplicate_titles(monkeypatch):
    first = object()
    second = object()
    root = object()
    entry = {
        "kCGWindowNumber": 77,
        "kCGWindowOwnerPID": 42,
        "kCGWindowName": "notes.txt",
        "kCGWindowBounds": {"X": 400, "Y": 50, "Width": 800, "Height": 600},
    }
    monkeypatch.setattr(ws, "_quartz_window_list", lambda **_kwargs: [entry])

    def copy_attr(element, attribute, _out):
        values = {
            (root, "AXWindows"): [first, second],
            (first, "AXTitle"): "notes.txt",
            (second, "AXTitle"): "notes.txt",
            (first, "AXPosition"): (10, 20),
            (first, "AXSize"): (300, 200),
            (second, "AXPosition"): (400, 50),
            (second, "AXSize"): (800, 600),
        }
        value = values.get((element, attribute))
        return (0, value) if value is not None else (1, None)

    monkeypatch.setitem(sys.modules, "ApplicationServices", types.SimpleNamespace(
        AXUIElementCreateApplication=lambda _pid: root,
        AXUIElementCopyAttributeValue=copy_attr,
    ))
    monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace(NSWorkspace=object()))

    target, error = ws._resolve_macos_ax_window(
        WindowInfo("notes.txt", handle=77),
    )

    assert error == ""
    assert target is second


def test_maximize_window_macos_fails_closed_without_accessibility(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    _patch_macos_accessibility(monkeypatch, granted=False)
    services = types.SimpleNamespace(AXIsProcessTrusted=lambda: False)
    monkeypatch.setitem(sys.modules, "ApplicationServices", services)

    ok, message = ws.maximize_window(WindowInfo("notes.txt", handle=77))

    assert ok is False
    assert "Accessibility" in message


# --- real smoke on the host platform (non-deterministic, just must not crash)


def test_list_windows_smoke_does_not_crash():
    # On the real host this exercises the actual backend; result shape only.
    result = ws.list_windows()
    assert isinstance(result, list)
    assert all(isinstance(w, WindowInfo) for w in result)
