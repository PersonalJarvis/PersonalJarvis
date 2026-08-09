"""The backend is not allowed to die quietly.

Everything the user touches — every route, every WebSocket frame, every voice
turn — is serialized through one asyncio loop on one thread. When that thread
ends, the pywebview window keeps running on its own thread, so the app is still
on screen while being unable to answer a single request. All the user gets is a
grey OFFLINE dot in the sidebar (live report 2026-08-09): no reason, no
traceback, and no way back short of killing the app by hand.

Two holes made that possible, and these tests pin both shut:

1. An exception escaping a thread goes to ``threading.excepthook``, which
   prints to ``sys.stderr`` — and ``pythonw.exe`` has none. The loudest
   failure in the app was also its most invisible one.
2. Nothing acted on the death. ``run_forever()`` returning is either a
   shutdown someone asked for or an accident, and the two were indistinguishable.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

import pytest

from jarvis.ui import desktop_app
from jarvis.ui.desktop_app import DesktopApp

# --- the recovery decision -------------------------------------------------


class _FakeApp:
    """The attribute surface ``_note_backend_stopped`` actually reads.

    Building a real ``DesktopApp`` would boot a log sink, a config load and a
    session token; the decision under test needs none of that.
    """

    def __init__(self, **overrides: Any) -> None:
        self._shutdown_done = False
        self._user_requested_quit = False
        self._backend_serving_since = time.monotonic() - 3600.0
        self._window = object()
        self._detached_windows: dict[str, Any] = {}
        self._auto_recovery_used = False
        self.restart_calls: list[bool] = []
        self.restart_result: tuple[bool, str] = (True, "restart scheduled")
        for key, value in overrides.items():
            setattr(self, key, value)

    def _schedule_restart(self, *, drop_elevation: bool) -> tuple[bool, str]:
        self.restart_calls.append(drop_elevation)
        return self.restart_result


def _note(app: _FakeApp, reason: str = "boom") -> None:
    DesktopApp._note_backend_stopped(app, reason)  # type: ignore[arg-type]


def test_unexpected_death_restarts_the_app() -> None:
    # The case from the report: a backend that had been serving for an hour is
    # suddenly gone while the window is still up. Nobody asked for that, so the
    # app brings itself back rather than sitting there as a shell.
    app = _FakeApp()

    _note(app)

    assert app.restart_calls == [False]
    assert app._auto_recovery_used is True


def test_shutdown_is_not_a_crash() -> None:
    # The user quit or hit Restart. The loop ending is the point, and
    # relaunching here would fight the exit path.
    app = _FakeApp(_shutdown_done=True)

    _note(app, "the loop was stopped")

    assert app.restart_calls == []


def test_user_requested_quit_is_not_a_crash() -> None:
    # Same, one step earlier: the quit is marked before shutdown() runs.
    app = _FakeApp(_user_requested_quit=True)

    _note(app, "the loop was stopped")

    assert app.restart_calls == []


def test_a_boot_failure_is_not_restarted() -> None:
    # A backend that dies seconds after starting would die the same way in the
    # fresh process. Restarting it is an infinite relaunch loop with a window
    # flashing on and off — worse than staying down and saying why.
    app = _FakeApp(_backend_serving_since=time.monotonic() - 5.0)

    _note(app)

    assert app.restart_calls == []
    assert app._auto_recovery_used is False


def test_recovery_fires_at_most_once_per_process() -> None:
    # Belt to the uptime brace: even a long-lived backend that somehow reaches
    # this twice gets exactly one rescue.
    app = _FakeApp(_auto_recovery_used=True)

    _note(app)

    assert app.restart_calls == []


def test_no_window_means_nothing_to_rescue() -> None:
    # Headless, or the window is already gone: the process is on its way out
    # and a relauncher would race the exit.
    app = _FakeApp(_window=None)

    _note(app)

    assert app.restart_calls == []


def test_a_detached_window_still_counts_as_a_window() -> None:
    # The main window can be closed while a detached view (IDE / Voice) is the
    # one thing still on screen. That surface is just as dead without a
    # backend, so it earns the same rescue.
    app = _FakeApp(_window=None, _detached_windows={"agentic": object()})

    _note(app)

    assert app.restart_calls == [False]


def test_a_half_built_instance_does_not_crash_the_reporter() -> None:
    # This runs on the way OUT, including out of a backend that never finished
    # coming up — so the instance may be missing half its attributes. A crash
    # report that itself crashes reports nothing at all.
    class _Bare:
        def _schedule_restart(self, *, drop_elevation: bool) -> tuple[bool, str]:
            raise AssertionError("must not be reached without a window")

    DesktopApp._note_backend_stopped(_Bare(), "died during boot")  # type: ignore[arg-type]


def test_a_refused_restart_leaves_the_app_up() -> None:
    # The relauncher could not be spawned. Half-quitting would leave the user
    # with nothing at all; staying up (offline) is the lesser failure, and the
    # log carries the detail.
    app = _FakeApp(restart_result=(False, "relauncher spawn failed"))

    _note(app)

    assert app.restart_calls == [False]


# --- the crash hooks -------------------------------------------------------
#
# Exercised through the pure builder. Swapping the real process-wide hooks in a
# test leaks into every test that runs after it, which is a poor trade for
# coverage of two assignments.


class _Args:
    """The shape ``threading.excepthook`` is handed."""

    def __init__(self, exc: BaseException, name: str) -> None:
        self.exc_type = type(exc)
        self.exc_value = exc
        self.exc_traceback = exc.__traceback__
        self.thread = threading.Thread(name=name)


def _captured_criticals(fn: Any) -> list[str]:
    from loguru import logger

    seen: list[str] = []
    sink_id = logger.add(lambda m: seen.append(m), level="CRITICAL")
    try:
        fn()
    finally:
        logger.remove(sink_id)
    return seen


def test_a_dying_thread_reaches_the_log() -> None:
    thread_hook, _ = desktop_app._build_crash_hooks(lambda _args: None, lambda *_: None)

    seen = _captured_criticals(
        lambda: thread_hook(_Args(RuntimeError("the backend loop fell over"), "jarvis-backend"))
    )

    # The thread name and the original exception both have to survive — a bare
    # "a thread died" is not something anyone can act on.
    assert any("jarvis-backend" in line for line in seen)
    assert any("the backend loop fell over" in line for line in seen)


def test_a_thread_exiting_via_systemexit_is_not_an_incident() -> None:
    # Interpreter shutdown tears daemon threads down with SystemExit and no
    # traceback. Logging that as CRITICAL would cry wolf on every clean quit.
    thread_hook, _ = desktop_app._build_crash_hooks(lambda _args: None, lambda *_: None)

    seen = _captured_criticals(lambda: thread_hook(_Args(SystemExit(), "jarvis-backend")))

    assert seen == []


def test_the_previous_thread_hook_still_runs() -> None:
    # Chaining, not replacing: a debugger or a harness that installed its own
    # reporter must keep seeing crashes.
    calls: list[Any] = []
    thread_hook, _ = desktop_app._build_crash_hooks(calls.append, lambda *_: None)

    args = _Args(ValueError("chained"), "chained-thread")
    _captured_criticals(lambda: thread_hook(args))

    assert calls == [args]


def test_a_broken_previous_hook_does_not_swallow_the_report() -> None:
    # Our line is written first, so a fallback reporter that itself raises
    # costs nothing that matters.
    def _broken(_args: Any) -> None:
        raise RuntimeError("the old reporter is broken too")

    thread_hook, _ = desktop_app._build_crash_hooks(_broken, lambda *_: None)

    seen = _captured_criticals(
        lambda: thread_hook(_Args(RuntimeError("the real problem"), "jarvis-backend"))
    )

    assert any("the real problem" in line for line in seen)


def test_a_dying_main_thread_reaches_the_log() -> None:
    chained: list[Any] = []
    _, main_hook = desktop_app._build_crash_hooks(
        lambda _args: None, lambda t, v, tb: chained.append(v)
    )
    exc = RuntimeError("the window thread fell over")

    seen = _captured_criticals(lambda: main_hook(type(exc), exc, exc.__traceback__))

    assert any("the window thread fell over" in line for line in seen)
    assert chained == [exc]


def test_ctrl_c_is_not_an_incident() -> None:
    # KeyboardInterrupt is how a developer stops the app from a terminal.
    _, main_hook = desktop_app._build_crash_hooks(lambda _args: None, lambda *_: None)
    exc = KeyboardInterrupt()

    seen = _captured_criticals(lambda: main_hook(type(exc), exc, None))

    assert seen == []


def test_installing_twice_does_not_stack_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two DesktopApp instances (tests do this) must not double-report. The
    # process hooks are restored by monkeypatch, so this stays hermetic.
    monkeypatch.setattr(desktop_app, "_CRASH_HOOKS_INSTALLED", False)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)

    desktop_app._install_crash_hooks()
    first = threading.excepthook
    desktop_app._install_crash_hooks()

    assert threading.excepthook is first
