"""Plumbing tests for the [computer_use] click-correction flags.

Restore-to-good (2026-06-27): both proactive zoom-before-click and the UIA
snap fallback DEFAULT OFF. Making zoom default-on (and the 2026-06-24 wild-snap)
stacked correction layers onto every click and degraded accuracy, so the
known-good pipeline (coarse click -> verify -> LLM refine on miss) is restored.
The flags stay as opt-in escape hatches, hot-reloadable per [computer_use].
"""
from __future__ import annotations

from jarvis.core.config import ComputerUseConfig
from jarvis.harness.computer_use_context import (
    ComputerUseContext,
    _RELOADABLE_FIELDS,
)


def test_config_zoom_before_click_defaults_off() -> None:
    # Default OFF (restore-to-good). The model_fields check guards against the
    # field being silently absorbed as an extra under the model's extra="allow".
    assert "zoom_before_click" in ComputerUseConfig.model_fields
    assert ComputerUseConfig().zoom_before_click is False


def test_config_uia_click_fallback_defaults_off() -> None:
    # The 2026-06-24 wild-snap layer (BUG-CU-UIASNAP) — off by default.
    assert "uia_click_fallback" in ComputerUseConfig.model_fields
    assert ComputerUseConfig().uia_click_fallback is False


def test_correction_flags_can_be_enabled() -> None:
    assert ComputerUseConfig(zoom_before_click=True).zoom_before_click is True
    assert ComputerUseConfig(uia_click_fallback=True).uia_click_fallback is True


def test_context_correction_flags_default_off() -> None:
    ctx = ComputerUseContext(
        vision_engine=None, brain_manager=None, tool_executor=None,
    )
    assert ctx.zoom_before_click is False
    assert ctx.uia_click_fallback is False


def test_correction_flags_are_hot_reloadable() -> None:
    # Listed in _RELOADABLE_FIELDS so a config / Self-Mod toggle applies to the
    # next mission without an app restart (mirrors verify_after_each_step).
    assert "zoom_before_click" in _RELOADABLE_FIELDS
    assert "uia_click_fallback" in _RELOADABLE_FIELDS
