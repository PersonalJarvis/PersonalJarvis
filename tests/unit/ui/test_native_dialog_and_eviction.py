"""Consent dialog semantics and the process-tree fallback of lock eviction.

``ask_yes_no`` may only ever return True on an explicit Yes: a missing helper,
no display, a closed box or an error all count as No, so the destructive
branch behind it (stopping a stuck instance) needs a real click. The Windows
``MessageBoxW`` path cannot run unattended, so these tests exercise the helper
protocol through the injectable runner on the non-Windows branches.
"""

from __future__ import annotations

import sys

import pytest

from jarvis.ui import native_dialog


@pytest.mark.skipif(sys.platform == "win32", reason="Win32 branch opens a real box")
def test_yes_is_exit_zero_from_the_first_installed_helper(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(sys, "platform", "linux")
    seen: list[list[str]] = []

    def _run(cmd):
        seen.append(cmd)
        return (True, 0)

    assert native_dialog.ask_yes_no("t", "m", _run=_run) is True
    assert seen[0][0] == "zenity"


@pytest.mark.skipif(sys.platform == "win32", reason="Win32 branch opens a real box")
def test_no_and_missing_helpers_are_both_no(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(sys, "platform", "linux")
    assert native_dialog.ask_yes_no("t", "m", _run=lambda cmd: (True, 1)) is False
    assert native_dialog.ask_yes_no("t", "m", _run=lambda cmd: (False, -1)) is False


@pytest.mark.skipif(sys.platform == "win32", reason="Win32 branch opens a real box")
def test_no_display_means_no_without_running_anything(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")

    def _run(cmd):
        raise AssertionError("must not run a helper without a display")

    assert native_dialog.ask_yes_no("t", "m", _run=_run) is False


@pytest.mark.skipif(sys.platform == "win32", reason="Win32 branch opens a real box")
def test_macos_yes_is_a_normal_osascript_return(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    seen: list[list[str]] = []

    def _run(cmd):
        seen.append(cmd)
        return (True, 0)

    assert native_dialog.ask_yes_no("t", 'say "hi"', _run=_run) is True
    assert seen[0][0] == "osascript"
    assert '\\"hi\\"' in seen[0][2]  # the quote is escaped for AppleScript
    assert native_dialog.ask_yes_no("t", "m", _run=lambda cmd: (True, 1)) is False


def test_eviction_falls_back_to_the_process_tree_kill(monkeypatch):
    """psutil's kill did not take → the tree kill runs → liveness is re-checked."""
    from jarvis.ui import desktop_app

    alive = {"value": True}
    tree_kills: list[int] = []

    class _Proc:
        def __init__(self, pid):
            self.pid = pid

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            raise TimeoutError("still there")

    class _FakePsutil:
        Process = _Proc

    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil())
    monkeypatch.setattr(desktop_app, "_pid_alive", lambda pid: alive["value"])

    def _tree_kill(pid):
        tree_kills.append(pid)
        alive["value"] = False
        return True

    assert desktop_app._terminate_pid(4242, _tree_kill=_tree_kill) is True
    assert tree_kills == [4242]


def test_eviction_reports_honestly_when_nothing_took(monkeypatch):
    from jarvis.ui import desktop_app

    class _Proc:
        def __init__(self, pid):
            pass

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            raise TimeoutError("still there")

    class _FakePsutil:
        Process = _Proc

    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil())
    monkeypatch.setattr(desktop_app, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(desktop_app.time, "sleep", lambda s: None)
    assert desktop_app._terminate_pid(4242, _tree_kill=lambda pid: True) is False


def test_a_bound_but_slow_port_is_a_busy_holder_not_a_zombie(monkeypatch):
    """Only a refused connection (nothing bound) may count as dead."""
    import httpx

    from jarvis.ui import desktop_app

    monkeypatch.setattr(desktop_app.time, "sleep", lambda s: None)

    def _slow(url, timeout=None):
        raise httpx.ReadTimeout("busy")

    monkeypatch.setattr(httpx, "get", _slow)
    assert desktop_app._default_lock_holder_health(47821) is True

    calls = {"n": 0}

    def _refused(url, timeout=None):
        calls["n"] += 1
        raise httpx.ConnectError("nothing listens")

    monkeypatch.setattr(httpx, "get", _refused)
    assert desktop_app._default_lock_holder_health(47821) is False
    assert calls["n"] == 4  # probed briefly, then declared dead
