"""Unit-Tests fuer den prozessweiten Skill-Context (Skills-Brain-Integration: Phase Skills-1).

Vorbild-Tests: ``tests/unit/harness/test_computer_use_context.py`` (falls
vorhanden) — selbe Mechanik (Set/Get/TryGet) wird hier verifiziert.
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
    """Marker-Stub fuer SkillRegistry — nur Identitaets-Tests, kein Verhalten."""


class _StubRunner:
    """Marker-Stub fuer SkillRunner — nur Identitaets-Tests, kein Verhalten."""


@pytest.fixture(autouse=True)
def _reset_context():
    """Garantiert sauberer State pro Test — globaler Context wird zurueckgesetzt."""
    set_skill_context(None)
    yield
    set_skill_context(None)


def test_try_get_returns_none_when_unset():
    assert try_get_skill_context() is None


def test_get_raises_runtime_error_when_unset():
    with pytest.raises(RuntimeError, match="nicht gesetzt"):
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
