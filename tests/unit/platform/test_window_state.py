"""Phase 0: jarvis/platform/window_state.py — reusable, cross-platform window
enumeration / focus / already-running detection behind the platform seam.

Seam-level only: the platform is forced via detect_platform/probes and the
ctypes/osascript/wmctrl backends are faked or monkeypatched — this proves the
dispatch + parsing + matching logic, NOT that real OS APIs behave as assumed on
real hardware (SIGNOFF-LOG honesty).
"""
from __future__ import annotations

import subprocess
import types

import pytest

from jarvis.platform import window_state as ws
from jarvis.platform.window_state import WindowInfo


def _cp(returncode: int, stdout: str = "", stderr: str = ""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


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


def test_list_windows_macos_parses_osascript(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    # newline-separated window titles, one per line
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp(0, stdout="OBS\nSafari — Apple\n"))
    titles = [w.title for w in ws.list_windows()]
    assert "OBS" in titles
    assert "Safari — Apple" in titles


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
    assert ws.get_foreground_title() == ""


# --- real smoke on the host platform (non-deterministic, just must not crash)


def test_list_windows_smoke_does_not_crash():
    # On the real host this exercises the actual backend; result shape only.
    result = ws.list_windows()
    assert isinstance(result, list)
    assert all(isinstance(w, WindowInfo) for w in result)
