"""New optional frontmatter fields: when_to_use + execution (AD-S5/S7).

Part of the skill-system rebuild (instruction-skill model) — see
docs/superpowers/specs/2026-06-09-skill-system-rebuild-design.md.
"""
from __future__ import annotations

import pytest

from jarvis.skills.schema import SkillFrontmatter


def _minimal(**kw):
    return SkillFrontmatter.model_validate({"schema_version": "1", "name": "x", **kw})


def test_when_to_use_defaults_to_none():
    assert _minimal().when_to_use is None


def test_when_to_use_roundtrip():
    fm = _minimal(when_to_use="Use when the user asks for a morning briefing.")
    assert fm.when_to_use is not None
    assert fm.when_to_use.startswith("Use when")


def test_execution_defaults_to_inline():
    assert _minimal().execution == "inline"


def test_execution_mission_accepted():
    assert _minimal(execution="mission").execution == "mission"


def test_execution_invalid_rejected():
    with pytest.raises(Exception):
        _minimal(execution="background")
