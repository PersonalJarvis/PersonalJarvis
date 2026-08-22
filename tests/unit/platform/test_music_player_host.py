"""The player host answers at once while its page is still loading.

pywebview gates ``evaluate_js`` on the window's ``loaded`` event and blocks up
to 20 s when a navigation has not reported back. The host's command loop is
sequential, so one blocked ``state`` read holds every later command — including
the ``show`` that would bring the window forward — hostage. Live 2026-08-22
20:01:52: the second ``load`` of a session left ``loaded`` clear; eighteen
``state`` reads each sat out the parent's 10 s timeout, the ``show`` behind them
did too, and one "play some music" took 199 s while the player window stayed
minimized. The host now answers "loading" after a short bounded wait and the
Windows fallback un-minimizes the window by its title when pywebview's own
``restore``/``show`` leave it iconic.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from jarvis.platform import music_player_host as host


class _Events:
    def __init__(self, loaded: bool) -> None:
        self.loaded = threading.Event()
        if loaded:
            self.loaded.set()


class FakeWindow:
    def __init__(self, *, loaded: bool = True, title: str = "Music") -> None:
        self.events = _Events(loaded)
        self.title = title
        self.evaluated: list[str] = []
        self.loaded_urls: list[str] = []
        self.restored = 0
        self.shown = 0
        self.minimized = 0

    def evaluate_js(self, script: str) -> str:
        self.evaluated.append(script)
        return '{"has_video": true, "paused": false, "position": 3.0, "consent": false}'

    def load_url(self, url: str) -> None:
        self.loaded_urls.append(url)

    def restore(self) -> None:
        self.restored += 1

    def show(self) -> None:
        self.shown += 1

    def minimize(self) -> None:
        self.minimized += 1


def test_state_answers_loading_at_once_instead_of_waiting_in_the_gate(monkeypatch):
    monkeypatch.setattr(host, "_LOADED_WAIT_S", 0.05)
    window = FakeWindow(loaded=False)

    started = time.monotonic()
    state = host._dispatch(window, "state", {})
    elapsed = time.monotonic() - started

    assert state["loading"] is True and state["ready"] is False
    assert state["has_video"] is False and state["consent"] is False
    assert window.evaluated == []  # never touched the gate
    assert elapsed < 1.0


def test_state_reads_the_page_once_it_has_loaded():
    window = FakeWindow(loaded=True)
    state = host._dispatch(window, "state", {})
    assert state["has_video"] is True and "loading" not in state
    assert len(window.evaluated) == 1


def test_state_waits_briefly_for_a_load_that_finishes_in_time(monkeypatch):
    monkeypatch.setattr(host, "_LOADED_WAIT_S", 2.0)
    window = FakeWindow(loaded=False)
    threading.Timer(0.1, window.events.loaded.set).start()

    state = host._dispatch(window, "state", {})

    assert "loading" not in state and state["has_video"] is True


def test_page_commands_fail_fast_while_loading_instead_of_blocking(monkeypatch):
    monkeypatch.setattr(host, "_LOADED_WAIT_S", 0.05)
    window = FakeWindow(loaded=False)
    for cmd in ("play", "pause", "next", "volume"):
        try:
            host._dispatch(window, cmd, {"level": 30})
        except RuntimeError as exc:
            assert "still loading" in str(exc)
        else:  # pragma: no cover — the guard must raise
            raise AssertionError(f"{cmd} did not fail fast")
    assert window.evaluated == []


def test_show_restores_then_shows_and_nudges_the_foreground(monkeypatch):
    window = FakeWindow()
    nudged: list[str] = []
    monkeypatch.setattr(host, "_force_foreground", lambda w: nudged.append(w.title))

    assert host._dispatch(window, "show", {}) is True
    assert window.restored == 1 and window.shown == 1
    assert nudged == ["Music"]


def test_force_foreground_is_a_quiet_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "linux")
    host._force_foreground(SimpleNamespace(title="Music"))  # must not raise


def test_a_window_without_the_loaded_event_is_read_directly():
    window = FakeWindow(loaded=True)
    del window.events  # a backend that exposes no events at all
    state = host._dispatch(window, "state", {})
    assert state["has_video"] is True
