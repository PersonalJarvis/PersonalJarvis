"""The registry applies the user's on/off overrides on every (re)load.

This is what makes the on/off toggle survive a restart: the in-memory state used
to be wiped by hot-reload. The override is supplied via an injected
``state_prefs_loader`` (DI keeps the registry decoupled from the file).

AP-15 invariant: an override must NEVER force a ``DRAFT`` skill on — only the
safety-linted ``promote()`` path may activate a draft.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.skills.registry import SkillRegistry
from jarvis.skills.schema import SkillLifecycleState


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "user_skills"
    root.mkdir()
    return root


def _write_skill(root: Path, name: str, *, state: str | None = None) -> None:
    d = root / name
    d.mkdir()
    fm = (
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        "description: test skill\n"
    )
    if state:
        fm += f"state: {state}\n"
    fm += "---\n\n## Body\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")


def test_disabled_override_turns_a_skill_off(skills_root: Path) -> None:
    _write_skill(skills_root, "alpha")  # parses to VALIDATED ("on")
    reg = SkillRegistry(
        skills_root, bus=None, state_prefs_loader=lambda: {"alpha": "disabled"}
    )
    reg.reload_sync()
    assert reg.get("alpha").state == SkillLifecycleState.DISABLED


def test_active_override_turns_a_skill_on(skills_root: Path) -> None:
    _write_skill(skills_root, "alpha")
    reg = SkillRegistry(
        skills_root, bus=None, state_prefs_loader=lambda: {"alpha": "active"}
    )
    reg.reload_sync()
    assert reg.get("alpha").state == SkillLifecycleState.ACTIVE


def test_draft_is_never_forced_on(skills_root: Path) -> None:
    # AP-15: even an explicit "active" override must not activate a draft.
    _write_skill(skills_root, "drafty", state="draft")
    reg = SkillRegistry(
        skills_root, bus=None, state_prefs_loader=lambda: {"drafty": "active"}
    )
    reg.reload_sync()
    assert reg.get("drafty").state == SkillLifecycleState.DRAFT


def test_no_override_leaves_state_unchanged(skills_root: Path) -> None:
    _write_skill(skills_root, "alpha")
    reg = SkillRegistry(skills_root, bus=None, state_prefs_loader=lambda: {})
    reg.reload_sync()
    assert reg.get("alpha").state == SkillLifecycleState.VALIDATED


def test_no_loader_is_backward_compatible(skills_root: Path) -> None:
    _write_skill(skills_root, "alpha")
    reg = SkillRegistry(skills_root, bus=None)  # legacy construction, no loader
    reg.reload_sync()
    assert reg.get("alpha").state == SkillLifecycleState.VALIDATED


def test_override_survives_a_second_reload(skills_root: Path) -> None:
    _write_skill(skills_root, "alpha")
    reg = SkillRegistry(
        skills_root, bus=None, state_prefs_loader=lambda: {"alpha": "disabled"}
    )
    reg.reload_sync()
    reg.reload_sync()
    assert reg.get("alpha").state == SkillLifecycleState.DISABLED
