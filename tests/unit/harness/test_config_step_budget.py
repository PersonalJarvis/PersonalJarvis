"""Tests for the Computer-Use step-budget config knobs and their bounds.

A hard task must not be cut off just for taking many steps, so the defaults
are generous (100) and the ceiling is high (1000). A genuinely stuck session
is stopped early by the loop's no-progress and repeated-failure guards, not by
a tight step count -- see ``jarvis/harness/screenshot_only_loop.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.core.config import ComputerUseConfig


def test_step_budget_default_is_generous():
    cfg = ComputerUseConfig()
    assert cfg.step_budget == 100  # was 12 -- too low, cut off hard tasks


def test_max_steps_default_is_generous():
    cfg = ComputerUseConfig()
    assert cfg.max_steps == 100  # was 20


def test_step_budget_accepts_high_ceiling():
    # Including values far above the old hard cap of 50.
    for v in (1, 25, 100, 500, 1000):
        assert ComputerUseConfig(step_budget=v).step_budget == v


def test_max_steps_accepts_high_ceiling():
    for v in (1, 25, 100, 500, 1000):
        assert ComputerUseConfig(max_steps=v).max_steps == v


def test_step_budget_rejects_zero_and_overflow():
    with pytest.raises(ValidationError):
        ComputerUseConfig(step_budget=0)
    with pytest.raises(ValidationError):
        ComputerUseConfig(step_budget=1001)


def test_max_steps_rejects_zero_and_overflow():
    with pytest.raises(ValidationError):
        ComputerUseConfig(max_steps=0)
    with pytest.raises(ValidationError):
        ComputerUseConfig(max_steps=1001)
