"""Plumbing tests for the [computer_use].zoom_before_click flag (default ON)."""
from __future__ import annotations

from jarvis.core.config import ComputerUseConfig
from jarvis.harness.computer_use_context import (
    ComputerUseContext,
    _RELOADABLE_FIELDS,
)


def test_config_zoom_before_click_defaults_on() -> None:
    # Default ON: Computer-Use clicks are accurate out-of-the-box with no
    # opt-in. The model_fields check guards against the field being silently
    # absorbed as an extra under the model's extra="allow".
    assert "zoom_before_click" in ComputerUseConfig.model_fields
    assert ComputerUseConfig().zoom_before_click is True


def test_config_zoom_before_click_can_be_disabled() -> None:
    assert ComputerUseConfig(zoom_before_click=False).zoom_before_click is False


def test_context_zoom_before_click_defaults_on() -> None:
    ctx = ComputerUseContext(
        vision_engine=None, brain_manager=None, tool_executor=None,
    )
    assert ctx.zoom_before_click is True


def test_zoom_before_click_is_hot_reloadable() -> None:
    # Listed in _RELOADABLE_FIELDS so a voice / Self-Mod toggle applies to the
    # next mission without an app restart (mirrors verify_after_each_step).
    assert "zoom_before_click" in _RELOADABLE_FIELDS
