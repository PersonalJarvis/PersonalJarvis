"""Human-takeover integration coverage for click_element on macOS."""
from __future__ import annotations

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


class _Vision:
    async def observe(self) -> Observation:
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=0,
            screenshot_path=None,
            screenshot_hash="",
            nodes=(
                UIANode(
                    role="Button",
                    name="Save",
                    automation_id="save-button",
                    bounds=(10, 20, 100, 40),
                    enabled=True,
                ),
            ),
            window_title="Test",
        )


def _stable_signature() -> tuple[object, ...]:
    return ("handle", 11, (0, 0, 800, 600))


@pytest.mark.asyncio
async def test_recent_hardware_input_stops_before_any_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.plugins.tool.click_element.sys.platform", "darwin")
    monkeypatch.setattr("jarvis.plugins.tool.click_element.os.name", "posix")
    monkeypatch.setattr(
        "jarvis.plugins.tool.click_element._foreground_window_signature",
        _stable_signature,
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.handoff.human_input_allows_automation",
        lambda: (False, "physical input 0.020s ago; yielding to the user"),
    )
    monkeypatch.setattr(
        "jarvis.cu.macos_semantic.try_press_at",
        lambda *_args, **_kwargs: pytest.fail("AXPress must not run during human takeover"),
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.base.get_actuator",
        lambda: pytest.fail("pointer fallback must not run during human takeover"),
    )

    result = await ClickElementTool(vision_source=_Vision()).execute(
        {"name": "save", "role": "Button"},
        _ctx(),
    )

    assert result.success is False
    assert result.output["outcome"] == "human_takeover"
    assert "physical input 0.020s ago" in (result.error or "")
