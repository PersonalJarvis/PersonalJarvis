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

from jarvis.ui.desktop_app import SingleInstanceError
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
        if not self.terminated or not self.killed:
            raise SingleInstanceError("Jarvis is already running (pid=4242).")
        return _Lock()


def _meta_pid(_error, meta):
    if meta and meta.get("pid") is not None:
        return int(meta["pid"])
    return None


def _recover(calls: _Calls):
    return launcher._recover_from_already_running(
        RuntimeError("Jarvis is already running (pid=4242)."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=calls.acquire,
        process_age=lambda _pid: None,  # a long-lived stuck holder, not a boot
        discover_pid=_meta_pid,
    )


def test_health_answers_treats_a_timeout_as_dead(monkeypatch):
    """A bound port that never replies is the frozen desktop, not 'alive'."""
    import sys
    from types import ModuleType

    class _Timeout(Exception):
        pass

    fake = ModuleType("httpx")

    def _get(url, timeout=2.0):
        raise _Timeout("read timeout")

    fake.get = _get  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httpx", fake)
    assert launcher._health_answers(47821) is False


def test_a_healthy_holder_is_focused_and_never_asked_about(monkeypatch):
    calls = _Calls(focused=True, meta={"pid": 4242, "port": 47821}, consent=True)
    assert _recover(calls) is None
    assert calls.asked == []
    assert calls.terminated == []


def test_a_frozen_window_is_asked_about_not_just_raised():
    """A window we can raise is not recovery when the server is silent.

    Live 2026-08-28: the GIL-stalled desktop still had a title, so the
    second click brought the frozen window forward and exited. The user
    kept staring at a corpse.
    """
    calls = _Calls(focused=True, meta={"pid": 4242, "port": 47821}, consent=False)
    result = launcher._recover_from_already_running(
        RuntimeError("Jarvis is already running (pid=4242)."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=calls.acquire,
        process_age=lambda _pid: None,
        discover_pid=_meta_pid,
        health=lambda _port: False,
    )
    assert result is None
    assert calls.asked
    assert calls.terminated == []
    title, message = calls.asked[0]
    assert "frozen" in message
    assert "4242" in message


def test_a_frozen_window_is_evicted_on_yes():
    calls = _Calls(focused=True, meta={"pid": 4242, "port": 47821}, consent=True)
    lock = launcher._recover_from_already_running(
        RuntimeError("Jarvis is already running (pid=4242)."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=calls.acquire,
        process_age=lambda _pid: None,
        discover_pid=_meta_pid,
        health=lambda _port: False,
    )
    assert isinstance(lock, _Lock)
    assert calls.terminated == [4242]


def test_a_young_frozen_window_is_waited_for_until_health_returns():
    """Boot can miss a 2 s health check. Do not offer to kill it yet."""

    class _Booting(_Calls):
        def __init__(self):
            super().__init__(focused=True, meta={"pid": 4242, "port": 47821}, consent=True)
            self.health_n = 0

        def health(self, _port):
            self.health_n += 1
            return self.health_n >= 4

    calls = _Booting()
    clock = {"t": 0.0}
    result = launcher._recover_from_already_running(
        RuntimeError("Jarvis is already running (pid=4242)."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=calls.acquire,
        process_age=lambda _pid: 2.0,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        now=lambda: clock["t"],
        booting_grace=5.0,
        health=calls.health,
    )
    assert result is None
    assert calls.asked == []
    assert calls.terminated == []
    assert calls.health_n >= 4


def test_a_young_holder_is_waited_for_not_killed():
    """A restart sibling that lands here while the real copy is still booting
    must not offer to kill it. Wait until a window appears, then focus it."""

    class _Booting(_Calls):
        def __init__(self):
            super().__init__(focused=False, meta={"pid": 4242}, consent=True)
            self.focus_n = 0

        def focus(self):
            self.focus_n += 1
            if self.focus_n == 2:
                raise RuntimeError("transient focus miss")
            return self.focus_n >= 4

    calls = _Booting()
    clock = {"t": 0.0}
    result = launcher._recover_from_already_running(
        RuntimeError("Jarvis is already running (pid=4242)."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=calls.acquire,
        process_age=lambda _pid: 2.0,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        now=lambda: clock["t"],
        booting_grace=5.0,
    )
    assert result is None
    assert calls.asked == []
    assert calls.terminated == []
    assert calls.focus_n >= 4


def test_a_young_holder_that_never_grows_a_window_is_still_asked():
    calls = _Calls(focused=False, meta={"pid": 4242, "port": 47821}, consent=False)
    clock = {"t": 0.0}
    result = launcher._recover_from_already_running(
        RuntimeError("Jarvis is already running (pid=4242)."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=calls.acquire,
        process_age=lambda _pid: 1.0,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        now=lambda: clock["t"],
        booting_grace=2.0,
    )
    assert result is None
    assert calls.asked
    assert calls.terminated == []


def test_a_holder_without_a_window_is_evicted_only_on_yes(monkeypatch):
    calls = _Calls(focused=False, meta={"pid": 4242, "port": 47821}, consent=True)
    lock = _recover(calls)
    assert isinstance(lock, _Lock)
    assert calls.terminated == [4242]
    assert calls.acquired == 3
    title, message = calls.asked[0]
    assert "4242" in message and "stuck" in message


def test_declining_the_dialog_keeps_the_holder_alive():
    calls = _Calls(focused=False, meta={"pid": 4242, "port": 47821}, consent=False)
    assert _recover(calls) is None
    assert calls.terminated == []
    assert calls.acquired == 1


def test_an_unknown_holder_is_reported_not_killed(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(launcher, "_report_startup_failure", seen.append)
    calls = _Calls(focused=False, meta=None, consent=True)
    assert _recover(calls) is None
    assert calls.asked == []
    assert seen and "already running" in seen[0]


def test_a_holder_named_only_in_the_lock_error_can_still_be_evicted():
    """Headless never wrote the sidecar, but the lock error still names the pid."""
    calls = _Calls(focused=False, meta=None, consent=True)
    lock = launcher._recover_from_already_running(
        RuntimeError("Jarvis is already running (pid=4242)."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=calls.acquire,
        process_age=lambda _pid: None,
        discover_pid=launcher._discover_holder_pid,
    )
    assert isinstance(lock, _Lock)
    assert calls.terminated == [4242]


def test_a_holder_found_on_the_admin_port_can_still_be_evicted(monkeypatch):
    """No sidecar, no pid in the error — the process bound to 47821 is the holder."""
    monkeypatch.setattr(launcher, "_holder_pid_from_error", lambda _error: None)
    monkeypatch.setattr("jarvis.ui.desktop_app._pid_listening_on_port", lambda _port: 4242)
    monkeypatch.setattr("jarvis.ui.desktop_app._fallback_admin_port", lambda: 47821)
    calls = _Calls(focused=False, meta=None, consent=True)
    lock = launcher._recover_from_already_running(
        RuntimeError("Jarvis lock is held but the holder is not responding."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=calls.acquire,
        process_age=lambda _pid: None,
        discover_pid=launcher._discover_holder_pid,
    )
    assert isinstance(lock, _Lock)
    assert calls.terminated == [4242]


def test_a_kill_that_did_not_take_is_reported(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(launcher, "_report_startup_failure", seen.append)
    calls = _Calls(focused=False, meta={"pid": 4242}, consent=True, killed=False)
    assert _recover(calls) is None
    assert calls.acquired == 2
    assert seen and "4242" in seen[0]


def test_lock_error_pid_takes_precedence_over_stale_sidecar():
    assert (
        launcher._discover_holder_pid(
            SingleInstanceError("Jarvis is already running (pid=47896)."),
            {"pid": 36580, "port": 47821},
        )
        == 47896
    )


@pytest.mark.parametrize("released_on", [1, 2])
def test_released_lock_is_taken_without_terminating_old_holder(released_on):
    calls = _Calls(focused=False, meta={"pid": 4242}, consent=True)

    def acquire():
        calls.acquired += 1
        if calls.acquired < released_on:
            raise SingleInstanceError("Jarvis is already running (pid=4242).")
        return _Lock()

    calls.acquire = acquire
    assert isinstance(_recover(calls), _Lock)
    assert calls.terminated == []
    assert len(calls.asked) == released_on - 1


@pytest.mark.parametrize("replaced_on", [1, 2, 3])
def test_replacement_during_recovery_is_focused_without_reusing_consent(monkeypatch, replaced_on):
    calls = _Calls(focused=False, meta={"pid": 4242}, consent=True)
    monkeypatch.setattr(launcher, "_report_startup_failure", calls.reported.append)
    clock = {"t": 0.0}
    focus_times = []

    def acquire():
        calls.acquired += 1
        holder = 47896 if calls.acquired >= replaced_on else 4242
        raise SingleInstanceError(f"Jarvis is already running (pid={holder}).")

    def focus():
        focus_times.append(clock["t"])
        return clock["t"] >= 1.0

    result = launcher._recover_from_already_running(
        SingleInstanceError("Jarvis is already running (pid=4242)."),
        focus=focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        acquire=acquire,
        process_age=lambda _pid: None,
        sleep=lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
        now=lambda: clock["t"],
        booting_grace=2.0,
        health=lambda _port: False,
    )
    assert result is None
    assert calls.terminated == ([4242] if replaced_on == 3 else [])
    assert len(calls.asked) == (0 if replaced_on == 1 else 1)
    assert calls.reported == []
    assert focus_times[-1] == 1.0


def test_default_recovery_acquisition_disables_automatic_eviction(monkeypatch):
    from jarvis.ui import desktop_app

    calls = _Calls(focused=False, meta={"pid": 4242}, consent=False)

    def acquire(*, terminate):
        assert terminate(47896) is False
        return _Lock()

    monkeypatch.setattr(desktop_app, "acquire_single_instance_lock", acquire)
    lock = launcher._recover_from_already_running(
        SingleInstanceError("Jarvis is already running (pid=4242)."),
        focus=calls.focus,
        read_meta=calls.read_meta,
        ask=calls.ask,
        terminate=calls.terminate,
        process_age=lambda _pid: None,
    )
    assert isinstance(lock, _Lock)
    assert calls.asked == []
    assert calls.terminated == []


def test_replacement_without_window_has_a_bounded_wait_and_is_not_killed(monkeypatch):
    calls = _Calls(focused=False, meta={"pid": 4242}, consent=True)
    monkeypatch.setattr(launcher, "_report_startup_failure", calls.reported.append)
    clock = {"t": 0.0}

    def acquire():
        raise SingleInstanceError("Jarvis is already running (pid=47896).")

    assert (
        launcher._recover_from_already_running(
            SingleInstanceError("Jarvis is already running (pid=4242)."),
            focus=calls.focus,
            read_meta=calls.read_meta,
            ask=calls.ask,
            terminate=calls.terminate,
            acquire=acquire,
            process_age=lambda _pid: None,
            sleep=lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
            now=lambda: clock["t"],
            booting_grace=1.0,
        )
        is None
    )
    assert clock["t"] == 1.0
    assert calls.terminated == []
    assert calls.asked == []
    assert len(calls.reported) == 1
    assert "47896" in calls.reported[0]
    assert "stopped process" not in calls.reported[0]


def test_lock_io_failure_is_reported_without_terminating_holder(monkeypatch):
    calls = _Calls(focused=False, meta={"pid": 4242}, consent=True)
    monkeypatch.setattr(launcher, "_report_startup_failure", calls.reported.append)

    def acquire():
        raise OSError("lock directory is unavailable")

    calls.acquire = acquire
    assert _recover(calls) is None
    assert calls.asked == []
    assert calls.terminated == []
    assert "lock directory is unavailable" in calls.reported[0]


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
