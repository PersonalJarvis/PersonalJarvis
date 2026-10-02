"""Integration tests for Accessibility-first click_element on macOS."""
from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.protocols import ExecutionContext, Observation, UIANode
from jarvis.cu.actuate import HumanInputTakeover
from jarvis.cu.macos_semantic import SemanticPressResult
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
    def __init__(self, node: UIANode | None = None) -> None:
        self._node = node or UIANode(
            role="Button",
            name="Save",
            automation_id="save-button",
            bounds=(10, 20, 100, 40),
            enabled=True,
        )

    async def observe(self) -> Observation:
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=0,
            screenshot_path=None,
            screenshot_hash="",
            nodes=(self._node,),
            window_title="Test",
        )


def _stable_signature() -> tuple[object, ...]:
    return ("handle", 11, (0, 0, 800, 600))


def _macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.plugins.tool.click_element.sys.platform", "darwin")
    monkeypatch.setattr("jarvis.plugins.tool.click_element.os.name", "posix")
    monkeypatch.setattr(
        "jarvis.plugins.tool.click_element._foreground_window_signature",
        _stable_signature,
    )
    monkeypatch.setattr(
        "jarvis.plugins.tool.click_element._window_signature_matches",
        lambda _expected: True,
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.handoff.human_input_allows_automation",
        lambda: (True, "physical input idle"),
    )


@pytest.mark.asyncio
async def test_native_axpress_short_circuits_pointer_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _macos(monkeypatch)
    monkeypatch.setattr(
        "jarvis.cu.macos_semantic.try_press_at",
        lambda *_args, **_kwargs: SemanticPressResult(
            "performed",
            "performed native AXPress",
        ),
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.base.get_actuator",
        lambda: pytest.fail("pointer fallback must not run after AXPress"),
    )

    result = await ClickElementTool(vision_source=_Vision()).execute(
        {"name": "save", "role": "Button"},
        _ctx(),
    )

    assert result.success is True
    assert "AXPress" in (result.output or "")


@pytest.mark.asyncio
async def test_edit_field_uses_axfocused_when_axpress_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _macos(monkeypatch)
    monkeypatch.setattr(
        "jarvis.cu.macos_semantic.try_press_at",
        lambda *_args, **_kwargs: SemanticPressResult(
            "unsupported",
            "no AXPress",
        ),
    )
    monkeypatch.setattr(
        "jarvis.cu.macos_semantic.try_focus_at",
        lambda *_args, **_kwargs: SemanticPressResult(
            "performed",
            "focused with AXFocused",
        ),
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.base.get_actuator",
        lambda: pytest.fail("pointer fallback must not run after AXFocused"),
    )
    edit = UIANode(
        role="Edit",
        name="Search",
        automation_id="search-field",
        bounds=(10, 20, 200, 40),
        enabled=True,
    )

    result = await ClickElementTool(vision_source=_Vision(edit)).execute(
        {"name": "search", "role": "Edit"},
        _ctx(),
    )

    assert result.success is True
    assert "AXFocused" in (result.output or "")


@pytest.mark.asyncio
async def test_semantic_identity_mismatch_refuses_pointer_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _macos(monkeypatch)
    monkeypatch.setattr(
        "jarvis.cu.macos_semantic.try_press_at",
        lambda *_args, **_kwargs: SemanticPressResult(
            "mismatch",
            "target changed identity",
        ),
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.base.get_actuator",
        lambda: pytest.fail("stale semantic target must not fall back to pixels"),
    )

    result = await ClickElementTool(vision_source=_Vision()).execute(
        {"name": "save"},
        _ctx(),
    )

    assert result.success is False
    assert result.error == "target changed identity"


@pytest.mark.asyncio
async def test_takeover_during_semantic_lookup_returns_structured_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _macos(monkeypatch)

    def _late_takeover(*_args, **_kwargs):
        raise HumanInputTakeover("user moved the mouse during AX lookup")

    monkeypatch.setattr(
        "jarvis.cu.macos_semantic.try_press_at",
        _late_takeover,
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.get_actuator",
        lambda: pytest.fail("late semantic takeover must not fall back to pixels"),
    )

    result = await ClickElementTool(vision_source=_Vision()).execute(
        {"name": "save", "role": "Button"},
        _ctx(),
    )

    assert result.success is False
    assert result.output["outcome"] == "human_takeover"
    assert "during AX lookup" in (result.error or "")
