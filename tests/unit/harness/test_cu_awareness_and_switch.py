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


# --- 1c + audit #13: switch_window routes through the ToolExecutor ------------


class _RecordingExecutor:
    """Captures execute() calls and returns a pre-set ToolResult-shaped object."""

    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[tuple[str | None, dict]] = []

    async def execute(self, tool: object, args: dict, *,
                      user_utterance: str = "", trace_id: object = None) -> object:
        self.calls.append((getattr(tool, "name", None), dict(args)))
        return self._result


class _FakeSwitchTool:
    name = "switch_window"


def _ctx_with_switch(executor: object) -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=None, brain_manager=None,
        tool_executor=executor, tools={"switch_window": _FakeSwitchTool()},
    )


async def test_execute_switch_window_routes_through_executor():
    # When the switch_window tool is wired, the action must go through the
    # ToolExecutor (tier/blacklist/audit parity) with the tool's own arg key.
    from types import SimpleNamespace

    ex = _RecordingExecutor(
        SimpleNamespace(success=True, output="Fokus auf Fenster: OBS", error=None)
    )
    ok, msg = await _execute_action(
        {"action": "switch_window", "name": "OBS"}, _ctx_with_switch(ex),
        trace_id=None, user_goal="x",
    )
    assert ok is True
    assert ex.calls == [("switch_window", {"title_contains": "OBS"})]
    assert "OBS" in msg


async def test_execute_switch_window_executor_failure_is_reported():
    from types import SimpleNamespace

    ex = _RecordingExecutor(
        SimpleNamespace(success=False, output=None, error="no window")
    )
    ok, msg = await _execute_action(
        {"action": "switch_window", "name": "Ghost"}, _ctx_with_switch(ex),
        trace_id=None, user_goal="x",
    )
    assert ok is False
    assert "no window" in msg


async def test_execute_switch_window_falls_back_to_inline_without_tool(monkeypatch):
    # No switch_window tool wired -> graceful inline focus fallback (preserves the
    # module's degradation contract; the executor path is production-only).
    monkeypatch.setattr(
        "jarvis.platform.window_state.focus_window", lambda t: (True, f"inline:{t}")
    )
    ok, msg = await _execute_action(
        {"action": "switch_window", "name": "OBS"}, _ctx(),  # _ctx() has tools={}
        trace_id=None, user_goal="x",
    )
    assert ok is True
    assert "inline:OBS" in msg


# --- audit #13: drag routes through the ToolExecutor -------------------------


class _FakeDragTool:
    name = "drag"


def _ctx_with_drag(executor: object) -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=None, brain_manager=None,
        tool_executor=executor, tools={"drag": _FakeDragTool()},
    )


async def test_execute_drag_routes_through_executor():
    # 0-1000 normalized coords against a 1000x1000 monitor map 1:1 to pixels, so
    # the executor must receive the resolved pixel endpoints under the tool's keys.
    from types import SimpleNamespace

    ex = _RecordingExecutor(
        SimpleNamespace(success=True, output="dragged (100,200)->(300,400)", error=None)
    )
    ok, msg = await _execute_action(
        {"action": "drag", "x": 100, "y": 200, "x2": 300, "y2": 400},
        _ctx_with_drag(ex),
        trace_id=None, user_goal="x", monitor_geom=(0, 0, 1000, 1000),
    )
    assert ok is True
    assert ex.calls and ex.calls[0][0] == "drag"
    sent = ex.calls[0][1]
    assert (sent["x1"], sent["y1"], sent["x2"], sent["y2"]) == (100, 200, 300, 400)


async def test_execute_drag_falls_back_to_inline_without_tool(monkeypatch):
    # No drag tool wired -> inline _perform_drag fallback (degradation contract).
    calls: list[tuple] = []
    monkeypatch.setattr(
        "jarvis.harness.screenshot_only_loop._perform_drag",
        lambda x1, y1, x2, y2, d: calls.append((x1, y1, x2, y2)),
    )
    ok, _msg = await _execute_action(
        {"action": "drag", "x": 100, "y": 200, "x2": 300, "y2": 400},
        _ctx(),  # tools={}
        trace_id=None, user_goal="x", monitor_geom=(0, 0, 1000, 1000),
    )
    assert ok is True
    assert calls == [(100, 200, 300, 400)]
