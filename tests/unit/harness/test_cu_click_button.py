"""Right/middle/double-click on the CU click action (audit 🟢 #21).

The actuation tool always supported button + double-click; the CU action never
exposed them, so the agent could only ever left-single-click (no context menus,
no double-click-to-open). These pin: the flags are parsed/validated, default to a
left single-click (purely additive), and thread through to the click dispatch.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.harness.screenshot_only_loop import (
    CULoopError,
    _dispatch_raw_click,
    _normalize_click_button,
    _parse_action,
    _validate_action_dict,
)


# --- _normalize_click_button (pure) -----------------------------------------


def test_defaults_to_left_single_click():
    obj: dict[str, Any] = {"action": "click", "x": 1, "y": 2}
    _normalize_click_button(obj)
    assert obj["button"] == "left"
    assert obj["double"] is False


def test_accepts_right_and_middle():
    for b in ("right", "RIGHT", "middle"):
        obj = {"button": b}
        _normalize_click_button(obj)
        assert obj["button"] == b.lower()


def test_double_flag_is_coerced_to_bool():
    obj = {"double": 1}
    _normalize_click_button(obj)
    assert obj["double"] is True


def test_invalid_button_raises():
    with pytest.raises(CULoopError):
        _normalize_click_button({"button": "scroll"})


# --- validation entry points carry the flags --------------------------------


def test_validate_action_dict_carries_button_double():
    obj = _validate_action_dict(
        {"action": "click", "x": 5, "y": 6, "button": "right", "double": True}
    )
    assert obj["button"] == "right"
    assert obj["double"] is True


def test_parse_action_defaults_left_single():
    obj = _parse_action('{"action":"click","x":5,"y":6}')
    assert obj["button"] == "left"
    assert obj["double"] is False


def test_parse_action_rejects_bad_button():
    with pytest.raises(CULoopError):
        _parse_action('{"action":"click","x":5,"y":6,"button":"nope"}')


# --- dispatch threads the flags to the tool ---------------------------------


class _RecordingExecutor:
    def __init__(self) -> None:
        self.args: dict[str, Any] | None = None

    async def execute(self, tool: Any, args: dict, *,
                      user_utterance: str = "", trace_id: Any = None) -> Any:
        self.args = dict(args)
        return SimpleNamespace(success=True, output="clicked", error=None)


async def test_dispatch_defaults_left_single():
    ex = _RecordingExecutor()
    await _dispatch_raw_click(ex, SimpleNamespace(name="click"), 10, 20, None)
    assert ex.args == {"x": 10, "y": 20, "button": "left", "double": False}


async def test_dispatch_threads_right_double():
    ex = _RecordingExecutor()
    await _dispatch_raw_click(
        ex, SimpleNamespace(name="click"), 10, 20, None, button="right", double=True
    )
    assert ex.args["button"] == "right"
    assert ex.args["double"] is True
