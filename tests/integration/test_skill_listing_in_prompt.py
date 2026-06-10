"""Integration tests for AVAILABLE SKILLS injection into BrainManager system prompt.

Skills-Brain-Integration (Track B): the Markdown section produced by
``jarvis.skills.prompt_injection.render_available_skills_section`` must
land in the assembled system prompt that ``BrainManager._build_system_prompt``
returns — when (and only when) a process-wide ``SkillContext`` is set.

These tests:
1. Verify the section appears with real skills loaded from disk.
2. Verify the section is absent when the context is unset (Headless-Mock-Mode).
3. Verify a renderer crash does not break the prompt build (defense-in-depth).

Pattern reference: ``tests/unit/awareness/test_system_prompt_injection.py``
mirrors this same structure for the Awareness snapshot.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.skill_context import (
    SkillContext,
    set_skill_context,
)


_VALID_SKILL_TEMPLATE = """---
schema_version: "1"
name: {name}
version: "1.0.0"
description: |
  {description}
category: testing
author: integration-test
license: MIT
triggers:
  - type: voice
    pattern: "^trigger {name}$"
    language: [de, en]
---

# {name}

Body of the {name} skill.
"""


def _write_skill(root: Path, name: str, description: str) -> Path:
    """Create ``<root>/<name>/SKILL.md`` with a minimal valid frontmatter."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        _VALID_SKILL_TEMPLATE.format(name=name, description=description),
        encoding="utf-8",
    )
    return skill_md


class _StubRunner:
    """Marker only — BrainManager never invokes it for prompt building."""


@pytest.fixture(autouse=True)
def _reset_skill_context() -> Iterator[None]:
    """Each test starts and ends with a clean global SkillContext."""
    set_skill_context(None)
    yield
    set_skill_context(None)


def _make_brain_manager() -> BrainManager:
    """BrainManager with no awareness/memory — minimal harness for prompt tests."""
    return BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})


def test_skill_section_present_in_prompt_when_context_set(tmp_path: Path) -> None:
    """With a populated registry + active SkillContext, the prompt contains the section."""
    _write_skill(
        tmp_path, "memory-save", "Speichert einen Fakt im Long-Term-Memory."
    )
    _write_skill(
        tmp_path, "morning-routine", "Tagesueberblick: Mail, Kalender, Wetter."
    )

    registry = SkillRegistry(root=tmp_path)
    registry.reload_sync()
    # Sanity: both skills loaded as VALIDATED/ACTIVE-equivalent.
    assert len(registry.list_active()) == 2

    set_skill_context(SkillContext(registry=registry, runner=_StubRunner()))  # type: ignore[arg-type]

    brain = _make_brain_manager()
    prompt = brain._build_system_prompt()

    assert "## AVAILABLE SKILLS" in prompt
    assert "`run-skill`" in prompt
    assert "memory-save" in prompt
    assert "morning-routine" in prompt
    assert "Speichert einen Fakt im Long-Term-Memory." in prompt
    assert "Tagesueberblick: Mail, Kalender, Wetter." in prompt


def test_skill_section_absent_when_skill_context_unset(tmp_path: Path) -> None:
    """Without a global SkillContext, ``## AVAILABLE SKILLS`` must NOT appear.

    This is the Headless-Mock-Mode invariant: the prompt builder cannot
    require Skill infrastructure to be wired in.
    """
    # Don't call set_skill_context — autouse fixture already cleared it.
    brain = _make_brain_manager()
    prompt = brain._build_system_prompt()

    assert "## AVAILABLE SKILLS" not in prompt


def test_skill_section_failure_doesnt_crash_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the renderer raises, the prompt build must still succeed (defensive try/except)."""
    _write_skill(tmp_path, "memory-save", "Speichert einen Fakt.")
    registry = SkillRegistry(root=tmp_path)
    registry.reload_sync()
    set_skill_context(SkillContext(registry=registry, runner=_StubRunner()))  # type: ignore[arg-type]

    def _boom(*_args: object, **_kwargs: object) -> str:  # noqa: D401
        raise RuntimeError("simulated renderer failure")

    # Patch at the module the manager imports from (lazy import inside
    # _build_system_prompt resolves to this attribute).
    import jarvis.skills.prompt_injection as pi_mod

    monkeypatch.setattr(pi_mod, "render_available_skills_section", _boom)

    brain = _make_brain_manager()
    # Must not raise.
    prompt = brain._build_system_prompt()

    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "## AVAILABLE SKILLS" not in prompt
