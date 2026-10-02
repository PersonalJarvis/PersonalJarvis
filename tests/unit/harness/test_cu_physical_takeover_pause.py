"""Physical-input takeover pause/resume tests for Computer-Use."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.protocols import ToolResult
from jarvis.cu.actuate import (
    HUMAN_TAKEOVER_OUTCOME,
    HumanInputTakeover,
    human_takeover_detail,
    human_takeover_tool_result,
)
from jarvis.harness import screenshot_only_loop as loop


class _Token:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


def test_takeover_result_is_structured_not_error_text_parsing() -> None:
    result = human_takeover_tool_result(HumanInputTakeover("user owns input"))
    assert result.success is False
    assert result.output == {
        "outcome": HUMAN_TAKEOVER_OUTCOME,
        "detail": "user owns input",
    }
    assert human_takeover_detail(result) == "user owns input"
    assert human_takeover_detail(
        ToolResult(False, None, "human_takeover words in a plain error")
    ) is None


def test_loop_promotes_only_structured_takeover_to_control_flow() -> None:
    result = human_takeover_tool_result(HumanInputTakeover("mouse moved"))
    with pytest.raises(HumanInputTakeover, match="mouse moved"):
        loop._raise_for_human_takeover_result(result)
    loop._raise_for_human_takeover_result(ToolResult(False, None, "ordinary miss"))


@pytest.mark.asyncio
async def test_physical_takeover_resumes_after_hardware_is_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.cu import human_activity

    states = iter([
        (False, "physical input 0.01s ago"),
        (False, "physical input 0.12s ago"),
        (True, "physical input idle"),
    ])
    monkeypatch.setattr(
        human_activity,
        "human_input_allows_automation",
        lambda: next(states),
    )
    monkeypatch.setattr(loop, "_HANDOFF_WAIT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(loop, "_HANDOFF_POLL_S", 0.001)

    result = await loop._await_physical_input_clearance(
        SimpleNamespace(bus=None),
        "do it",
        2,
        None,
        detail="user took over",
    )
    assert result == "cleared"


@pytest.mark.asyncio
async def test_physical_takeover_probe_unknown_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.cu import human_activity

    def _unknown() -> tuple[bool, str]:
        raise RuntimeError("Quartz unavailable")

    monkeypatch.setattr(human_activity, "human_input_allows_automation", _unknown)
    monkeypatch.setattr(loop, "_HANDOFF_WAIT_TIMEOUT_S", 0.01)
    monkeypatch.setattr(loop, "_HANDOFF_POLL_S", 0.001)

    result = await loop._await_physical_input_clearance(
        SimpleNamespace(bus=None),
        "do it",
        2,
        None,
        detail="ownership unknown",
    )
    assert result == "timeout"


@pytest.mark.asyncio
async def test_physical_takeover_honors_cancel_before_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.cu import human_activity

    monkeypatch.setattr(
        human_activity,
        "human_input_allows_automation",
        lambda: pytest.fail("cancel must win before another HID probe"),
    )
    monkeypatch.setattr(loop, "_HANDOFF_WAIT_TIMEOUT_S", 0.1)
    monkeypatch.setattr(loop, "_HANDOFF_POLL_S", 0.001)

    result = await loop._await_physical_input_clearance(
        SimpleNamespace(bus=None),
        "do it",
        2,
        _Token(cancelled=True),
        detail="user took over",
    )
    assert result == "cancelled"
