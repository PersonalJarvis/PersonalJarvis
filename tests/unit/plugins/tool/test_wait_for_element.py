from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.protocols import ExecutionContext, Observation, UIANode
from jarvis.plugins.tool.wait_for_element import WaitForElementTool


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
        approved_by="auto",
    )


def _observation(nodes: tuple[UIANode, ...], title: str = "Dialog") -> Observation:
    return Observation(
        trace_id=uuid4(),
        timestamp_ns=0,
        screenshot_path=None,
        screenshot_hash="",
        nodes=nodes,
        window_title=title,
    )


class _FakeVisionSource:
    """Returns a no-match Observation first, then a matching one (call counter)."""

    def __init__(self, no_match: Observation, match: Observation) -> None:
        self._no_match = no_match
        self._match = match
        self.calls = 0

    async def observe(self) -> Observation:
        self.calls += 1
        return self._no_match if self.calls == 1 else self._match


class _NeverMatchSource:
    def __init__(self, observation: Observation) -> None:
        self._observation = observation
        self.calls = 0

    async def observe(self) -> Observation:
        self.calls += 1
        return self._observation


@pytest.mark.asyncio
async def test_returns_center_coords_after_polling_loop() -> None:
    """First observe has no match, second observe surfaces the target node.

    The tool must keep polling and return the matched node's center coords.
    """
    no_match = _observation(
        nodes=(UIANode(role="Text", name="Loading...", bounds=(0, 0, 50, 20)),),
    )
    target = UIANode(
        role="Button",
        name="OK",
        automation_id="okBtn",
        bounds=(100, 200, 40, 20),
        enabled=True,
    )
    match = _observation(nodes=(target,), title="Confirm")

    source = _FakeVisionSource(no_match, match)
    tool = WaitForElementTool(vision_source=source)

    result = await tool.execute(
        {"role": "Button", "name_contains": "ok", "timeout_s": 5.0}, _ctx()
    )

    assert result.success is True
    assert source.calls == 2  # polling loop exercised
    assert result.output["found"] is True
    # center = (100 + 40//2, 200 + 20//2) = (120, 210)
    assert result.output["x"] == 120
    assert result.output["y"] == 210
    assert result.output["name"] == "OK"
    assert result.output["role"] == "Button"
    assert result.output["automation_id"] == "okBtn"
    assert result.output["bounds"] == [100, 200, 40, 20]
    assert result.output["window_title"] == "Confirm"


@pytest.mark.asyncio
async def test_timeout_when_no_node_ever_matches() -> None:
    obs = _observation(
        nodes=(UIANode(role="Text", name="Spinner", bounds=(0, 0, 10, 10)),),
        title="Busy",
    )
    source = _NeverMatchSource(obs)
    tool = WaitForElementTool(vision_source=source)

    result = await tool.execute(
        {"name_contains": "Save", "timeout_s": 0.3}, _ctx()
    )

    assert result.success is False
    assert "Timeout" in (result.error or "")
    assert source.calls >= 1


@pytest.mark.asyncio
async def test_rejects_when_no_filter_provided() -> None:
    tool = WaitForElementTool(vision_source=_NeverMatchSource(_observation(())))
    result = await tool.execute({}, _ctx())
    assert result.success is False
    assert "at least one" in (result.error or "")


@pytest.mark.asyncio
async def test_enabled_required_skips_disabled_node() -> None:
    disabled = UIANode(
        role="Button", name="Submit", bounds=(10, 10, 30, 30), enabled=False
    )
    obs = _observation(nodes=(disabled,))
    source = _NeverMatchSource(obs)
    tool = WaitForElementTool(vision_source=source)

    result = await tool.execute(
        {"name_contains": "Submit", "enabled_required": True, "timeout_s": 0.3},
        _ctx(),
    )

    assert result.success is False
    assert "Timeout" in (result.error or "")
