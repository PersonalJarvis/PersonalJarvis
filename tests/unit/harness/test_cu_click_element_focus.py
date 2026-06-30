"""Post-click focus confirmation for click_element (audit 🔴 #1B).

click_element used to return success the moment a node matched, with no check that
the click had any effect. It now re-reads the tree and CONFIRMS the element took
keyboard focus. The confirmation is deliberately positive-only: focus proves a
field/control was clicked, but its ABSENCE never flips a button/menu click to a
false failure (those legitimately don't retain focus).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.harness import screenshot_only_loop as sol
from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.harness.screenshot_only_loop import _element_is_focused, _execute_action


def _node(name: str, *, focused: bool = False, role: str = "Edit") -> Any:
    return SimpleNamespace(
        role=role, name=name, value="", focused=focused,
        bounds=(0, 0, 10, 10), enabled=True,
    )


# --- pure helper: _element_is_focused (positive-only) ------------------------


def test_focused_match_returns_true():
    nodes = (_node("Search", focused=True),)
    assert _element_is_focused(nodes, "Search") is True


def test_substring_match_counts():
    nodes = (_node("Username field", focused=True),)
    assert _element_is_focused(nodes, "username") is True


def test_matching_node_not_focused_returns_none_not_false():
    # The positive-only contract: a found-but-unfocused control is "can't
    # confirm" (None), NEVER False — clicking a button rarely keeps focus.
    nodes = (_node("Submit", focused=False, role="Button"),)
    assert _element_is_focused(nodes, "Submit") is None


def test_no_match_returns_none():
    nodes = (_node("Other", focused=True),)
    assert _element_is_focused(nodes, "Search") is None


def test_short_name_returns_none():
    assert _element_is_focused((_node("X", focused=True),), "X") is None


def test_never_returns_false():
    # Whatever the input, the helper is True-or-None, never False.
    for nodes in [(), (_node("A", focused=False),), (_node("B", focused=True),)]:
        assert _element_is_focused(nodes, "search") in (True, None)


# --- dispatch wiring ---------------------------------------------------------


class _OkExecutor:
    async def execute(self, tool: Any, args: dict, *,
                      user_utterance: str = "", trace_id: Any = None) -> Any:
        return SimpleNamespace(success=True, output="clicked 'Search'", error=None)


class _FakeClickElementTool:
    name = "click_element"


class _FakeSource:
    def __init__(self, nodes: tuple) -> None:
        self._nodes = nodes

    async def observe(self) -> Any:
        return SimpleNamespace(nodes=self._nodes)


def _ctx() -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=None, brain_manager=None,
        tool_executor=_OkExecutor(), tools={"click_element": _FakeClickElementTool()},
    )


@pytest.mark.asyncio
async def test_dispatch_appends_focus_confirmation(monkeypatch):
    monkeypatch.setattr(sol, "_get_ui_tree_source", lambda: _FakeSource((_node("Search", focused=True),)))
    ok, msg = await _execute_action(
        {"action": "click_element", "name": "Search"}, _ctx(),
        trace_id=None, user_goal="x",
    )
    assert ok is True
    assert "focus" in msg.lower()


@pytest.mark.asyncio
async def test_dispatch_unconfirmed_focus_leaves_success_unchanged(monkeypatch):
    # A button that doesn't retain focus: still success, NO false failure, and no
    # bogus focus claim appended.
    monkeypatch.setattr(sol, "_get_ui_tree_source", lambda: _FakeSource((_node("Submit", focused=False, role="Button"),)))
    ok, msg = await _execute_action(
        {"action": "click_element", "name": "Submit"}, _ctx(),
        trace_id=None, user_goal="x",
    )
    assert ok is True
    assert "now has focus" not in msg


@pytest.mark.asyncio
async def test_dispatch_tree_error_leaves_success_unchanged(monkeypatch):
    class _BoomSource:
        async def observe(self):
            raise OSError("tree gone")

    monkeypatch.setattr(sol, "_get_ui_tree_source", lambda: _BoomSource())
    ok, msg = await _execute_action(
        {"action": "click_element", "name": "Search"}, _ctx(),
        trace_id=None, user_goal="x",
    )
    assert ok is True  # a flaky tree never turns a good click into a failure
