"""Tests for the inspect-pointer router tool (AI Pointer step 6, pull path)."""

from __future__ import annotations

from jarvis.plugins.tool.inspect_pointer import InspectPointerTool
from jarvis.pointer.context import PointerContext
from jarvis.vision.pointer_types import PointerElement


def test_metadata_is_safe_router_tool() -> None:
    tool = InspectPointerTool()
    assert tool.name == "inspect-pointer"
    assert tool.risk_tier == "safe"
    assert isinstance(tool.schema, dict)
    assert "what is this" in tool.description.lower() or "pointing" in tool.description.lower()


def test_constructs_with_no_args() -> None:
    # The brain factory's default branch instantiates tools via cls() — the
    # tool must construct with no required arguments (factory.py:274).
    assert InspectPointerTool() is not None


async def test_execute_reports_labeled_element() -> None:
    el = PointerElement(name="Save", role="Button", app_name="chrome.exe")

    async def fake_resolve():
        return PointerContext(available=True, x=10, y=20, element=el)

    tool = InspectPointerTool(resolve_fn=fake_resolve)
    res = await tool.execute({}, None)
    assert res.success is True
    assert res.output["available"] is True
    assert res.output["name"] == "Save"
    assert res.output["role"] == "Button"
    assert res.output["app"] == "chrome.exe"


async def test_execute_reports_unavailable() -> None:
    async def fake_resolve():
        return PointerContext(available=False, reason="no_cursor")

    tool = InspectPointerTool(resolve_fn=fake_resolve)
    res = await tool.execute({}, None)
    assert res.success is True
    assert res.output["available"] is False
    assert res.output["reason"] == "no_cursor"
