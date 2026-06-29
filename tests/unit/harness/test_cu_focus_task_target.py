"""Proactively focus the goal's target app across ALL monitors (live 2026-06-28).

The user's case: Chrome is already open on a SECONDARY monitor (not foreground);
"do something in Chrome" — CU only ever looked at the current foreground screen,
never found Chrome, and "didn't get it". ``_focus_task_target_window`` finds the
named app among ALL open windows and brings it to the front first, deterministic
and conservative (a distinctive goal token, not a generic verb; already-foreground
is a no-op).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from jarvis.harness import screenshot_only_loop as sol
from jarvis.platform import window_state as ws

_REAL = sol._focus_task_target_window


def _win(title: str) -> Any:
    return SimpleNamespace(title=title, minimized=False, handle=1)


def _patch(monkeypatch, *, windows, foreground="", raised=True):
    monkeypatch.setattr(ws, "list_windows", lambda: list(windows))
    monkeypatch.setattr(ws, "get_foreground_title", lambda: foreground)
    calls = {"raised": []}

    def _raise(win):
        calls["raised"].append(win.title)
        return (raised, win.title)

    monkeypatch.setattr(ws, "raise_window", _raise)
    return calls


def _ctx() -> Any:
    return SimpleNamespace(window_awareness=True)


def test_focuses_named_app_open_on_another_monitor(monkeypatch):
    # Chrome open (not foreground) + goal mentions chrome -> raise it.
    calls = _patch(
        monkeypatch,
        windows=[_win("Inbox - Visual Studio Code"), _win("Reddit - Google Chrome")],
        foreground="Inbox - Visual Studio Code",
    )
    note = _REAL(_ctx(), "search the last post in chrome")
    assert note is not None
    assert "front" in note.lower()
    assert calls["raised"] == ["Reddit - Google Chrome"]


def test_no_op_when_target_already_foreground(monkeypatch):
    calls = _patch(
        monkeypatch,
        windows=[_win("Reddit - Google Chrome")],
        foreground="Reddit - Google Chrome",
    )
    assert _REAL(_ctx(), "do something in chrome") is None
    assert calls["raised"] == []


def test_no_match_when_app_not_open(monkeypatch):
    calls = _patch(
        monkeypatch,
        windows=[_win("Inbox - Visual Studio Code")],
        foreground="Inbox - Visual Studio Code",
    )
    assert _REAL(_ctx(), "do something in chrome") is None
    assert calls["raised"] == []


def test_generic_verbs_do_not_focus_a_window(monkeypatch):
    # "mach"/"bitte"/"etwas" must not match a window title and focus it wrongly.
    calls = _patch(
        monkeypatch,
        windows=[_win("machen.txt - Editor")],  # contains "mach" but goal has no app
        foreground="Other",
    )
    assert _REAL(_ctx(), "mach bitte etwas") is None
    assert calls["raised"] == []


def test_disabled_when_window_awareness_off(monkeypatch):
    _patch(monkeypatch, windows=[_win("Reddit - Google Chrome")])
    ctx = SimpleNamespace(window_awareness=False)
    assert _REAL(ctx, "do something in chrome") is None


def test_never_raises_on_error(monkeypatch):
    def _boom():
        raise OSError("enum failed")

    monkeypatch.setattr(ws, "list_windows", _boom)
    assert _REAL(_ctx(), "do something in chrome") is None
