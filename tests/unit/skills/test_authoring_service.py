"""Tests for the deterministic user-skill authoring service.

``SkillAuthoringService`` backs ``POST /api/skills`` — the "New skill" form in
the desktop app. Unlike the OpenClaw-author *mission* pipeline
(``SkillAuthoringRunner``), this path takes a structured request (name,
description, body, triggers) and writes a SKILL.md deterministically, with NO
brain involved, so it works on a headless €5 VPS.

Contract:
- ``create`` writes ``<user_skills_root>/<slug>/SKILL.md`` and reloads the
  registry so the new skill is immediately listable.
- A name colliding with an existing skill or a built-in is refused with status
  409. An empty/too-short name is refused with 400.
- The slug is derived kebab-case from the name; triggers land in the
  frontmatter.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jarvis.skills import prefs
from jarvis.skills.authoring import (
    SkillAuthoringError,
    SkillAuthoringService,
    SkillCreateRequest,
)
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.schema import SkillLifecycleState


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    return root


@pytest.fixture
def registry(skills_root: Path) -> SkillRegistry:
    reg = SkillRegistry(
        skills_root, bus=None, state_prefs_loader=prefs.load_state_overrides
    )
    reg.reload_sync()
    return reg


def _service(registry: SkillRegistry, skills_root: Path) -> SkillAuthoringService:
    return SkillAuthoringService(registry=registry, user_skills_root=skills_root)


@pytest.mark.asyncio
async def test_create_writes_skill_md_and_appears_in_registry(
    registry: SkillRegistry, skills_root: Path
) -> None:
    svc = _service(registry, skills_root)
    created = await svc.create(
        SkillCreateRequest(
            name="My Cool Skill",
            description="Does a cool thing",
            body="## My Cool Skill\n\nStep one.\n",
        )
    )
    assert created.name == "My Cool Skill"
    # File on disk under the kebab-case slug
    skill_md = skills_root / "my-cool-skill" / "SKILL.md"
    assert skill_md.exists()
    fm = yaml.safe_load(skill_md.read_text(encoding="utf-8").split("---", 2)[1])
    assert fm["name"] == "My Cool Skill"
    assert fm["description"] == "Does a cool thing"
    # Registry now lists it
    assert registry.get("My Cool Skill").name == "My Cool Skill"


@pytest.mark.asyncio
async def test_create_skill_is_on_by_default(
    registry: SkillRegistry, skills_root: Path
) -> None:
    """A user who fills the form and hits Create expects an active skill —
    not a draft they have to flip on. No explicit state → VALIDATED ("on")."""
    svc = _service(registry, skills_root)
    await svc.create(
        SkillCreateRequest(name="Active One", body="## Active One\n\nDo the active thing.\n")
    )
    skill = registry.get("Active One")
    assert skill.state in (
        SkillLifecycleState.VALIDATED,
        SkillLifecycleState.ACTIVE,
    )


@pytest.mark.asyncio
async def test_create_persists_voice_trigger(
    registry: SkillRegistry, skills_root: Path
) -> None:
    svc = _service(registry, skills_root)
    await svc.create(
        SkillCreateRequest(
            name="Trigger Skill",
            body="## Trigger Skill\n\nDo the thing when invoked.\n",
            triggers=({"type": "voice", "pattern": "^do the thing"},),
        )
    )
    fm = registry.get("Trigger Skill").frontmatter
    assert fm is not None
    assert len(fm.triggers) == 1
    assert fm.triggers[0].type == "voice"
    assert fm.triggers[0].pattern == "^do the thing"


@pytest.mark.asyncio
async def test_create_rejects_duplicate_name(
    registry: SkillRegistry, skills_root: Path
) -> None:
    svc = _service(registry, skills_root)
    await svc.create(
        SkillCreateRequest(name="Dup Skill", body="## Dup Skill\n\nFirst body.\n")
    )
    with pytest.raises(SkillAuthoringError) as exc:
        await svc.create(
            SkillCreateRequest(name="Dup Skill", body="## Dup Skill\n\nSecond body.\n")
        )
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_create_rejects_builtin_name(
    registry: SkillRegistry, skills_root: Path
) -> None:
    from jarvis.skills.builtin import BUILTIN_SKILL_NAMES

    builtin = sorted(BUILTIN_SKILL_NAMES)[0]
    svc = _service(registry, skills_root)
    with pytest.raises(SkillAuthoringError) as exc:
        await svc.create(SkillCreateRequest(name=builtin, body="## x\n"))
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_create_rejects_empty_name(
    registry: SkillRegistry, skills_root: Path
) -> None:
    svc = _service(registry, skills_root)
    with pytest.raises(SkillAuthoringError) as exc:
        await svc.create(SkillCreateRequest(name="  ", body="## x\n"))
    assert exc.value.status == 400


@pytest.mark.asyncio
async def test_create_rejects_name_that_slugs_to_nothing(
    registry: SkillRegistry, skills_root: Path
) -> None:
    """A name with no slug-able characters (e.g. only punctuation) is a 400,
    not a path-traversal or an empty-dir write."""
    svc = _service(registry, skills_root)
    with pytest.raises(SkillAuthoringError) as exc:
        await svc.create(
            SkillCreateRequest(name="!!!", body="## x\n\nDo something.\n")
        )
    assert exc.value.status == 400


# ----------------------------------------------------------------------
# Body must carry real instructions — the root cause of the "Hallo Hallo
# Hallo" forensic: a skill created with no instructions in its body is
# functionless (run-skill loads an empty body → the brain does nothing).
# A skill with only a heading / whitespace must be refused at 400 so no
# path (UI form, REST, AI-commit) can persist a dead skill.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rejects_empty_body(
    registry: SkillRegistry, skills_root: Path
) -> None:
    svc = _service(registry, skills_root)
    with pytest.raises(SkillAuthoringError) as exc:
        await svc.create(SkillCreateRequest(name="Empty Body", body=""))
    assert exc.value.status == 400
    assert not (skills_root / "empty-body").exists()


@pytest.mark.asyncio
async def test_create_rejects_heading_only_body(
    registry: SkillRegistry, skills_root: Path
) -> None:
    """Just ``## Title`` (the default when the form body is left blank) is not
    a usable skill — this is exactly the Hallo-Hallo-Hallo failure."""
    svc = _service(registry, skills_root)
    with pytest.raises(SkillAuthoringError) as exc:
        await svc.create(
            SkillCreateRequest(name="Hallo Hallo Hallo", body="## Hallo Hallo Hallo\n")
        )
    assert exc.value.status == 400
    assert not (skills_root / "hallo-hallo-hallo").exists()


@pytest.mark.asyncio
async def test_create_accepts_body_with_instructions(
    registry: SkillRegistry, skills_root: Path
) -> None:
    svc = _service(registry, skills_root)
    created = await svc.create(
        SkillCreateRequest(
            name="Good Skill",
            body="## Good Skill\n\nReply with a cheerful greeting.\n",
        )
    )
    assert created.name == "Good Skill"
