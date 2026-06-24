"""Phase 1a + 1c: window-state awareness line + switch_window CU action.

1a — the CU prompt gets a compact "what is already open" line so the model stops
re-launching apps that are already running (the OBS-in-the-taskbar case).
1c — ``switch_window`` becomes a CU action so the model can focus an existing
window directly.

Unit-level: window_state.list_windows/get_foreground_title/focus_window are
monkeypatched, so these are deterministic regardless of the host's open windows.
The full run_cu_loop integration is covered by the existing harness suite as a
regression guard.
"""
from __future__ import annotations

import pytest

from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.harness.screenshot_only_loop import (
    _VALID_ACTIONS,
    CULoopError,
    _execute_action,
    _parse_action,
    _validate_action_dict,
    _window_awareness_line,
)
from jarvis.platform.window_state import WindowInfo


def _ctx() -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=None, brain_manager=None, tool_executor=object(), tools={}
    )


# --- switch_window as a CU action -------------------------------------------


def test_switch_window_in_valid_actions():
    assert "switch_window" in _VALID_ACTIONS


def test_parse_action_switch_window_ok():
    obj = _parse_action('{"action":"switch_window","name":"OBS"}')
    assert obj["action"] == "switch_window"
    assert obj["name"] == "OBS"


def test_parse_action_switch_window_accepts_title_key():
    obj = _parse_action('{"action":"switch_window","title":"OBS"}')
    assert obj["name"] == "OBS"


def test_parse_action_switch_window_requires_name():
    with pytest.raises(CULoopError):
        _parse_action('{"action":"switch_window"}')


def test_validate_action_dict_switch_window_normalizes_name():
    obj = _validate_action_dict({"action": "switch_window", "name": "  OBS  "})
    assert obj["name"] == "OBS"


def test_validate_action_dict_switch_window_requires_name():
    with pytest.raises(CULoopError):
        _validate_action_dict({"action": "switch_window", "name": "   "})


async def test_execute_switch_window_focuses(monkeypatch):
    monkeypatch.setattr(
        "jarvis.platform.window_state.focus_window", lambda t: (True, f"focused:{t}")
    )
    ok, msg = await _execute_action(
        {"action": "switch_window", "name": "OBS"}, _ctx(), trace_id=None, user_goal="x"
    )
    assert ok is True
    assert "OBS" in msg


async def test_execute_switch_window_reports_not_found(monkeypatch):
    monkeypatch.setattr(
        "jarvis.platform.window_state.focus_window", lambda t: (False, "no window")
    )
    ok, msg = await _execute_action(
        {"action": "switch_window", "name": "Ghost"}, _ctx(), trace_id=None, user_goal="x"
    )
    assert ok is False


async def test_execute_switch_window_empty_name_fails(monkeypatch):
    ok, msg = await _execute_action(
        {"action": "switch_window", "name": ""}, _ctx(), trace_id=None, user_goal="x"
    )
    assert ok is False


# --- awareness line ---------------------------------------------------------


def test_awareness_line_lists_open_windows(monkeypatch):
    monkeypatch.setattr(
        "jarvis.platform.window_state.list_windows",
        lambda: [WindowInfo("OBS 30", minimized=True), WindowInfo("Google Chrome")],
    )
    monkeypatch.setattr(
        "jarvis.platform.window_state.get_foreground_title", lambda: "Google Chrome"
    )
    line = _window_awareness_line(_ctx())
    assert "OBS 30 (minimized)" in line
    assert "Google Chrome" in line
    assert "FOREGROUND: Google Chrome" in line


def test_awareness_line_empty_when_no_windows(monkeypatch):
    monkeypatch.setattr("jarvis.platform.window_state.list_windows", lambda: [])
    assert _window_awareness_line(_ctx()) == ""


def test_awareness_line_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("enum blew up")

    monkeypatch.setattr("jarvis.platform.window_state.list_windows", boom)
    assert _window_awareness_line(_ctx()) == ""


def test_awareness_line_respects_disable_flag(monkeypatch):
    monkeypatch.setattr(
        "jarvis.platform.window_state.list_windows", lambda: [WindowInfo("X")]
    )
    ctx = _ctx()
    ctx.window_awareness = False
    assert _window_awareness_line(ctx) == ""


def test_awareness_line_dedupes_and_caps(monkeypatch):
    many = [WindowInfo(f"App {i}") for i in range(50)] + [WindowInfo("App 0")]
    monkeypatch.setattr("jarvis.platform.window_state.list_windows", lambda: many)
    monkeypatch.setattr("jarvis.platform.window_state.get_foreground_title", lambda: "")
    line = _window_awareness_line(_ctx())
    # capped well below 51 entries; "App 0" appears once despite the duplicate
    assert line.count("App 0") == 1
    assert line.count(";") < 20
