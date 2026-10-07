"""Reopening during an orderly desktop quit must wait, not offer to kill it."""

import json
import os

import pytest
from filelock import FileLock

from jarvis.ui import desktop_app
from jarvis.ui.web import launcher


def test_closing_marks_the_owned_instance_before_teardown(monkeypatch, tmp_path):
    path = tmp_path / ".jarvis-running"
    original = {"pid": os.getpid(), "port": 47821, "started_at": 10.0}
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(desktop_app, "META_FILE_PATH", path)
    app = desktop_app.DesktopApp.__new__(desktop_app.DesktopApp)
    app._begin_quit_from_close()
    marked = json.loads(path.read_text(encoding="utf-8"))
    assert marked.items() >= original.items()
    assert marked["quitting_at"] > 0
    app._begin_quit_from_close()
    assert json.loads(path.read_text(encoding="utf-8")) == marked


def test_quit_does_not_mark_another_instances_sidecar(monkeypatch, tmp_path):
    path = tmp_path / ".jarvis-running"
    original = {"pid": os.getpid() + 1, "port": 47821}
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(desktop_app, "META_FILE_PATH", path)
    desktop_app.DesktopApp.__new__(desktop_app.DesktopApp)._begin_quit_from_close()
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_shutdown_retains_quit_marker_until_the_process_releases_its_lock(monkeypatch, tmp_path):
    path = tmp_path / ".jarvis-running"
    original = {"pid": os.getpid(), "port": 47821}
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(desktop_app, "META_FILE_PATH", path)
    app = desktop_app.DesktopApp.__new__(desktop_app.DesktopApp)
    app._shutdown_done = False
    app._destroy_background_keeper = lambda: None
    app._stop_overlay = lambda: None
    app._virtual_cursor = app._jarvis_cursor = None
    app._backend_loop = app._server = app._backend_thread = app._tray = None
    assert app.shutdown() == 0
    assert json.loads(path.read_text(encoding="utf-8"))["quitting_at"] > 0
    # Sidecar state is advisory: only the OS lock authorizes a fresh start.
    lock = desktop_app.acquire_single_instance_lock(
        lock_path=tmp_path / "free-lock", meta_path=path,
    )
    lock.release()
    desktop_app._write_meta(47821, os.getpid(), meta_path=path)
    assert "quitting_at" not in json.loads(path.read_text(encoding="utf-8"))


def test_silent_quitting_holder_is_never_automatically_evicted(monkeypatch, tmp_path):
    lock_path, meta_path = tmp_path / "lock", tmp_path / "meta"
    meta_path.write_text(json.dumps({"pid": 4242, "port": 47821, "quitting_at": 99.0}))
    monkeypatch.setattr(desktop_app.time, "time", lambda: 100.0)
    monkeypatch.setattr(desktop_app, "_pid_alive", lambda _pid: True)
    killed = []
    with FileLock(str(lock_path)):
        with pytest.raises(desktop_app.SingleInstanceError, match="pid=4242"):
            desktop_app.acquire_single_instance_lock(
                lock_path=lock_path, meta_path=meta_path,
                health_probe=lambda _port: False,
                terminate=lambda pid: killed.append(pid) or False,
            )
    assert killed == []


@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("process_age", [1.0, 1000.0])
def test_reopen_waits_for_quit_and_follows_a_replacement(monkeypatch, replacement, process_age):
    monkeypatch.setattr(desktop_app.time, "time", lambda: 100.0)
    clock = {"t": 0.0}
    asked, killed = [], []
    lock = object()

    def acquire():
        if clock["t"] < 2.0:
            raise desktop_app.SingleInstanceError("already running (pid=4242)")
        if replacement:
            raise desktop_app.SingleInstanceError("already running (pid=4243)")
        return lock

    result = launcher._recover_from_already_running(
        desktop_app.SingleInstanceError("already running (pid=4242)"),
        focus=lambda: replacement and clock["t"] >= 2.0,
        read_meta=lambda: {"pid": 4242, "port": 47821, "quitting_at": 99.0},
        acquire=acquire, ask=lambda *a, **kw: asked.append(a) or False,
        terminate=lambda pid: killed.append(pid) or True,
        process_age=lambda _pid: process_age,
        sleep=lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
        now=lambda: clock["t"],
    )
    assert result is (None if replacement else lock)
    assert clock["t"] == 2.0
    assert asked == killed == []


def test_background_service_winning_during_quit_hands_back_to_the_desktop(monkeypatch):
    monkeypatch.setattr(desktop_app.time, "time", lambda: 100.0)
    clock = {"t": 0.0}
    handed_back, asked = [], []
    lock = object()

    def acquire():
        holder = 4242 if clock["t"] < 2.0 else 4243
        raise desktop_app.SingleInstanceError(f"already running (pid={holder})")

    def takeover(*, expected_pid=None):
        handed_back.append(expected_pid)
        return lock

    monkeypatch.setattr(launcher, "_take_over_from_background_service", takeover)
    result = launcher._recover_from_already_running(
        desktop_app.SingleInstanceError("already running (pid=4242)"),
        focus=lambda: False,
        read_meta=lambda: {"pid": 4242, "quitting_at": 99.0},
        acquire=acquire, ask=lambda *a, **kw: asked.append(a) or False,
        process_age=lambda _pid: 1000.0,
        sleep=lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
        now=lambda: clock["t"],
    )
    assert result is lock
    assert handed_back == [4243]
    assert asked == []


def test_service_handover_does_not_target_a_different_replacement(monkeypatch):
    from jarvis.core import background_service

    monkeypatch.setattr(background_service, "service_pid", lambda: 4243)

    def takeover(acquire, *, busy_error, find_pid):
        assert find_pid() is None
        return None

    monkeypatch.setattr(background_service, "take_over_from_service", takeover)
    assert launcher._take_over_from_background_service(expected_pid=4244) is None


@pytest.mark.parametrize("meta", [
    {"pid": 4242},
    {"pid": 4243, "quitting_at": 99.0},
    {"pid": 4242, "quitting_at": 1.0},
    {"pid": 4242, "quitting_at": 101.0},
    {"pid": 4242, "quitting_at": "bad"},
    {"pid": 4242, "quitting_at": float("nan")},
    {"pid": 4242, "quitting_at": float("inf")},
])
def test_stale_or_invalid_quit_metadata_does_not_delay_recovery(monkeypatch, meta):
    monkeypatch.setattr(desktop_app.time, "time", lambda: 100.0)
    monkeypatch.setattr(launcher, "_holder_alive", lambda _pid: True)
    asked, slept = [], []

    def acquire():
        raise desktop_app.SingleInstanceError("already running (pid=4242)")

    launcher._recover_from_already_running(
        desktop_app.SingleInstanceError("already running (pid=4242)"),
        focus=lambda: False, read_meta=lambda: meta, acquire=acquire,
        ask=lambda *a, **kw: asked.append(a) or False,
        process_age=lambda _pid: 1000.0, sleep=slept.append,
    )
    assert len(asked) == 1
    assert slept == []


def test_quitting_holder_that_never_exits_still_reaches_bounded_recovery(monkeypatch):
    monkeypatch.setattr(desktop_app.time, "time", lambda: 100.0)
    monkeypatch.setattr(launcher, "_holder_alive", lambda _pid: True)
    clock = {"t": 0.0}
    asked = []

    def acquire():
        raise desktop_app.SingleInstanceError("already running (pid=4242)")

    launcher._recover_from_already_running(
        desktop_app.SingleInstanceError("already running (pid=4242)"),
        focus=lambda: False,
        read_meta=lambda: {"pid": 4242, "quitting_at": 99.0},
        acquire=acquire, ask=lambda *a, **kw: asked.append(a) or False,
        process_age=lambda _pid: 1000.0,
        sleep=lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
        now=lambda: clock["t"],
    )
    assert 40.0 <= clock["t"] <= 50.0
    assert len(asked) == 1
