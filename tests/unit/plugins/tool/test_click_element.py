"""Unit tests for the click_element tool.

A fake vision source feeds a hand-built UIAutomation Observation so the
tool can be exercised without a live desktop. ``_click_windows`` is
patched on the click_element module namespace to record the coordinates
it would have clicked.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.protocols import ExecutionContext, Observation, UIANode
from jarvis.plugins.tool.click_element import ClickElementTool


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
        approved_by="auto",
    )


class _FakeVisionSource:
    """Returns a fixed Observation built from the given nodes."""

    def __init__(self, nodes: tuple[UIANode, ...]) -> None:
        self._nodes = nodes

    async def observe(self) -> Observation:
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=0,
            screenshot_path=None,
            screenshot_hash="",
            nodes=self._nodes,
            window_title="Test",
        )


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int, str, bool]]:
    """Patch _click_windows so the test runs cross-platform and records calls."""
    calls: list[tuple[int, int, str, bool]] = []

    def _record(x: int, y: int, button: str, double: bool) -> None:
        calls.append((x, y, button, double))

    monkeypatch.setattr(
        "jarvis.plugins.tool.click_element._click_windows", _record
    )
    # Force the Windows native path so the recorder is hit on every platform.
    monkeypatch.setattr("jarvis.plugins.tool.click_element.os.name", "nt")
    return calls


@pytest.mark.asyncio
async def test_click_by_name_hits_center_of_node(
    recorder: list[tuple[int, int, str, bool]],
) -> None:
    nodes = (
        UIANode(role="Button", name="Save", bounds=(10, 20, 100, 40), enabled=True),
        UIANode(role="Button", name="Cancel", bounds=(200, 20, 80, 40), enabled=True),
    )
    tool = ClickElementTool(vision_source=_FakeVisionSource(nodes))

    result = await tool.execute({"name": "save"}, _ctx())

    assert result.success is True
    # Center of (10, 20, 100, 40) -> (10 + 50, 20 + 20) = (60, 40).
    assert recorder == [(60, 40, "left", False)]
    assert "Save" in result.output


@pytest.mark.asyncio
async def test_role_filter_narrows_correctly(
    recorder: list[tuple[int, int, str, bool]],
) -> None:
    nodes = (
        UIANode(role="Edit", name="Item", bounds=(0, 0, 50, 50), enabled=True),
        UIANode(role="Button", name="Item", bounds=(300, 100, 60, 20), enabled=True),
    )
    tool = ClickElementTool(vision_source=_FakeVisionSource(nodes))

    result = await tool.execute({"name": "item", "role": "Button"}, _ctx())

    assert result.success is True
    # Only the Button "Item" should match -> center of (300, 100, 60, 20).
    assert recorder == [(330, 110, "left", False)]
    assert "Button" in result.output


@pytest.mark.asyncio
async def test_nth_selects_second_match(
    recorder: list[tuple[int, int, str, bool]],
) -> None:
    nodes = (
        UIANode(role="ListItem", name="Row", bounds=(0, 0, 100, 20), enabled=True),
        UIANode(role="ListItem", name="Row", bounds=(0, 30, 100, 20), enabled=True),
        UIANode(role="ListItem", name="Row", bounds=(0, 60, 100, 20), enabled=True),
    )
    tool = ClickElementTool(vision_source=_FakeVisionSource(nodes))

    result = await tool.execute({"name": "row", "nth": 1}, _ctx())

    assert result.success is True
    # Second match -> center of (0, 30, 100, 20) = (50, 40).
    assert recorder == [(50, 40, "left", False)]


@pytest.mark.asyncio
async def test_no_match_lists_available_names(
    recorder: list[tuple[int, int, str, bool]],
) -> None:
    nodes = (
        UIANode(role="Button", name="Save", bounds=(0, 0, 50, 20), enabled=True),
        UIANode(role="Button", name="Cancel", bounds=(0, 30, 50, 20), enabled=True),
        # Disabled and zero-area nodes must not appear in the candidate set.
        UIANode(role="Button", name="Hidden", bounds=(0, 0, 0, 0), enabled=True),
        UIANode(role="Button", name="Disabled", bounds=(0, 0, 50, 20), enabled=False),
    )
    tool = ClickElementTool(vision_source=_FakeVisionSource(nodes))

    result = await tool.execute({"name": "DoesNotExist"}, _ctx())

    assert result.success is False
    assert recorder == []
    assert result.error is not None
    assert "Save" in result.error
    assert "Cancel" in result.error
