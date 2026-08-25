"""The launcher leaves a trace of EVERY desktop launch, and never bounces mute.

Background (2026-08-25): a launch that ended before ``DesktopApp`` existed —
an "already running" bounce, a crash on an import, a lock held by a stuck
earlier instance — ran under ``pythonw`` with no console and no log sink, so
the user clicked, nothing appeared, and nothing was recorded. These tests pin
the three answers: the early log path equals the one the app uses later, a
holder with no window turns into a consent dialog (and only Yes evicts), and a
crash before the window is reported instead of swallowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.ui.web import launcher


def test_early_log_path_matches_the_data_dir_the_app_uses(monkeypatch):
    """The sink is installed once per process: both resolutions MUST agree."""
    from jarvis.core import config
    from jarvis.ui.desktop_log import desktop_log_path

    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    assert desktop_log_path() == config.DATA_DIR / "jarvis_desktop.log"


def test_early_log_path_honours_the_env_override(monkeypatch, tmp_path: Path):
    from jarvis.ui.desktop_log import desktop_log_path

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert desktop_log_path() == tmp_path / "jarvis_desktop.log"


class _Lock:
    pass


class _Calls:
    def __init__(self, *, focused: bool, meta, consent: bool, killed: bool = True):
        self.focused = focused
        self.meta = meta
        self.consent = consent
        self.killed = killed
        self.asked: list[tuple[str, str]] = []
        self.terminated: list[int] = []
        self.acquired = 0
        self.reported: list[str] = []

    def focus(self):
        return self.focused

    def read_meta(self):
        return self.meta

    def ask(self, title, message):
        self.asked.append((title, message))
        return self.consent

    def terminate(self, pid):
        self.terminated.append(pid)
        return self.killed

    def acquire(self):
        self.acquired += 1
        return _Lock()


def _recover(calls: _Calls):
    return launcher._recover_from_already_running(
        RuntimeError("Jarvis is already running (pid=4242)."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=calls.acquire,
    )


def test_a_healthy_holder_is_focused_and_never_asked_about(monkeypatch):
    calls = _Calls(focused=True, meta={"pid": 4242, "port": 47821}, consent=True)
    assert _recover(calls) is None
    assert calls.asked == []
    assert calls.terminated == []


def test_a_holder_without_a_window_is_evicted_only_on_yes(monkeypatch):
    calls = _Calls(focused=False, meta={"pid": 4242, "port": 47821}, consent=True)
    lock = _recover(calls)
    assert isinstance(lock, _Lock)
    assert calls.terminated == [4242]
    assert calls.acquired == 1
    title, message = calls.asked[0]
    assert "4242" in message and "stuck" in message


def test_declining_the_dialog_keeps_the_holder_alive():
    calls = _Calls(focused=False, meta={"pid": 4242, "port": 47821}, consent=False)
    assert _recover(calls) is None
    assert calls.terminated == []
    assert calls.acquired == 0


def test_an_unknown_holder_is_reported_not_killed(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(launcher, "_report_startup_failure", seen.append)
    calls = _Calls(focused=False, meta=None, consent=True)
    assert _recover(calls) is None
    assert calls.asked == []
    assert seen and "already running" in seen[0]


def test_a_kill_that_did_not_take_is_reported(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(launcher, "_report_startup_failure", seen.append)
    calls = _Calls(focused=False, meta={"pid": 4242}, consent=True, killed=False)
    assert _recover(calls) is None
    assert calls.acquired == 0
    assert seen and "4242" in seen[0]


def test_run_desktop_bounces_with_exit_3_when_recovery_declines(monkeypatch):
    from jarvis.ui import desktop_app

    def _raise_lock(*args, **kwargs):
        raise desktop_app.SingleInstanceError("already running")

    monkeypatch.setattr(desktop_app, "acquire_single_instance_lock", _raise_lock)
    monkeypatch.setattr(desktop_app, "focus_existing_instance_robust", lambda: False)
    monkeypatch.setattr(launcher, "_recover_from_already_running", lambda *a, **k: None)
    assert launcher._run_desktop(cfg=object(), use_lock=True) == 3


def test_a_crash_before_the_window_is_reported(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(launcher, "_report_startup_failure", seen.append)

    def _boom(argv):
        raise ValueError("no window toolkit today")

    monkeypatch.setattr(launcher, "_main", _boom)
    assert launcher.main([]) == 1
    assert seen and "ValueError" in seen[0] and "no window toolkit today" in seen[0]


def test_a_system_exit_passes_through_untouched(monkeypatch):
    def _exit(argv):
        raise SystemExit(7)

    monkeypatch.setattr(launcher, "_main", _exit)
    with pytest.raises(SystemExit) as info:
        launcher.main([])
    assert info.value.code == 7
