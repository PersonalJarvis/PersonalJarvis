"""Unit tests for the process-wide skill context (skills-brain integration: Phase Skills-1).

Model tests: ``tests/unit/harness/test_computer_use_context.py`` (if
present) — the same mechanism (set/get/tryGet) is verified here.
"""
from __future__ import annotations

import pytest

from jarvis.skills.skill_context import (
    SkillContext,
    get_skill_context,
    set_skill_context,
    try_get_skill_context,
)


class _StubRegistry:
    """Marker stub for SkillRegistry — identity tests only, no behavior."""


class _StubRunner:
    """Marker stub for SkillRunner — identity tests only, no behavior."""


@pytest.fixture(autouse=True)
def _reset_context():
    """Guarantees clean state per test — global context is reset."""
    set_skill_context(None)
    yield
    set_skill_context(None)


def test_try_get_returns_none_when_unset():
    assert try_get_skill_context() is None


def test_get_raises_runtime_error_when_unset():
    with pytest.raises(RuntimeError, match="not set"):
        get_skill_context()


def test_set_then_get_roundtrip():
    ctx = SkillContext(registry=_StubRegistry(), runner=_StubRunner())
    set_skill_context(ctx)
    assert get_skill_context() is ctx
    assert try_get_skill_context() is ctx


def test_set_none_clears_context():
    ctx = SkillContext(registry=_StubRegistry(), runner=_StubRunner())
    set_skill_context(ctx)
    assert try_get_skill_context() is ctx
    set_skill_context(None)
    assert try_get_skill_context() is None
