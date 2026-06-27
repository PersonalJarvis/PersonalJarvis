"""Plumbing tests for the opt-in [computer_use].zoom_before_click flag."""
from __future__ import annotations

from jarvis.core.config import ComputerUseConfig
from jarvis.harness.computer_use_context import (
    ComputerUseContext,
    _RELOADABLE_FIELDS,
)


def test_config_zoom_before_click_defaults_off() -> None:
    assert ComputerUseConfig().zoom_before_click is False


def test_config_zoom_before_click_parses_true() -> None:
    assert ComputerUseConfig(zoom_before_click=True).zoom_before_click is True


def test_context_zoom_before_click_defaults_off() -> None:
    ctx = ComputerUseContext(
        vision_engine=None, brain_manager=None, tool_executor=None,
    )
    assert ctx.zoom_before_click is False


def test_zoom_before_click_is_hot_reloadable() -> None:
    # Listed in _RELOADABLE_FIELDS so a voice / Self-Mod toggle applies to the
    # next mission without an app restart (mirrors verify_after_each_step).
    assert "zoom_before_click" in _RELOADABLE_FIELDS
