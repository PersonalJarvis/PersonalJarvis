"""Tests for the AI Skill Creator service (``/api/skills/creator/*``).

The creator turns a free-text intent into a structured SKILL.md draft. It is
brain-assisted but MUST degrade gracefully: with no brain (headless VPS, brain
not yet built, provider down) ``draft`` still returns a valid deterministic
skeleton so the user can edit and commit it. ``commit`` persists the reviewed
draft through the same deterministic writer as the manual form.

Contract (mirrors the frontend types in ``useSkills.ts``):
- ``draft(SkillCreatorInput) -> SkillCreatorResult(draft, skill_md, validation,
  brain_used)``; ``draft`` is a dict with name/description/category/tags/
  triggers/requires_tools/risk_policy/body/questions/assumptions/test_prompts.
- ``validate_skill_md(content) -> (validation, frontmatter)`` where validation is
  ``{ok, state, errors, warnings, parse_error}``.
- ``commit(draft) -> Skill`` that appears in the registry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.protocols import BrainDelta
from jarvis.skills import prefs
from jarvis.skills.creator_service import (
    SkillCreatorInput,
    SkillCreatorService,
    render_skill_md,
    validate_skill_md,
)
from jarvis.skills.registry import SkillRegistry


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


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


class _FakeBrain:
    """Minimal brain stub: ``complete`` streams one delta with fixed text."""

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, request):  # noqa: ANN001 — duck-typed
        async def _gen():
            yield BrainDelta(content=self._text, finish_reason="stop")

        return _gen()


class _FakeBrainManager:
    """Mimics the live BrainManager: exposes the user's ACTIVE provider via
    ``active_provider`` + ``_get_or_create`` (the multi-provider contract — the
    creator must use the provider the user selected, not a frontier favourite)."""

    def __init__(self, provider, name: str = "gemini") -> None:
        self._provider = provider
        self.active_provider = name
        self.requested: list[str] = []

    def _get_or_create(self, name: str):  # noqa: ANN001
        self.requested.append(name)
        return self._provider


_GOOD_BRAIN_JSON = """{
  "name": "Brain Made Skill",
  "description": "A skill the brain designed.",
  "category": "automation",
  "tags": ["ai"],
  "triggers": [{"type": "voice", "pattern": "^do brain thing"}],
  "requires_tools": ["run-shell"],
  "risk_policy": {"default_tier": "ask"},
  "body": "## Brain Made Skill\\n\\nDo the brain thing.\\n"
}"""


def _service(registry, *, brain=None) -> SkillCreatorService:
    return SkillCreatorService(brain=brain, registry=registry)


# ----------------------------------------------------------------------
# validate_skill_md / render_skill_md
# ----------------------------------------------------------------------


def test_validate_accepts_valid_skill_md() -> None:
    content = (
        "---\n"
        'schema_version: "1"\n'
        "name: Good Skill\n"
        "description: fine\n"
        "---\n\n## Body\n"
    )
    validation, frontmatter = validate_skill_md(content)
    assert validation["ok"] is True
    assert validation["errors"] == []
    assert frontmatter is not None
    assert frontmatter["name"] == "Good Skill"


def test_validate_rejects_missing_name() -> None:
    content = "---\nschema_version: \"1\"\ndescription: no name\n---\n\n## Body\n"
    validation, frontmatter = validate_skill_md(content)
    assert validation["ok"] is False
    assert validation["errors"]


def test_validate_reports_parse_error_for_garbage() -> None:
    validation, frontmatter = validate_skill_md("not a skill at all")
    assert validation["ok"] is False
    assert validation["parse_error"]
    assert frontmatter is None


def test_render_draft_produces_parseable_skill_md() -> None:
    draft = {
        "name": "Rendered Skill",
        "description": "desc",
        "category": "general",
        "tags": [],
        "triggers": [],
        "requires_tools": [],
        "risk_policy": {"default_tier": "ask"},
        "body": "## Rendered Skill\n\nHello.\n",
    }
    skill_md = render_skill_md(draft)
    assert "Rendered Skill" in skill_md
    validation, _ = validate_skill_md(skill_md)
    assert validation["ok"] is True


# ----------------------------------------------------------------------
# draft() — deterministic fallback + brain path
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_without_brain_is_deterministic_and_valid(registry) -> None:
    svc = _service(registry, brain=None)
    result = await svc.draft(
        SkillCreatorInput(
            intent="pause spotify when I start talking",
            name_hint="Spotify Pause",
        )
    )
    assert result.brain_used is False
    assert result.draft["name"]  # non-empty name
    assert result.draft["body"].strip()  # non-empty body
    assert result.validation["ok"] is True


@pytest.mark.asyncio
async def test_draft_with_brain_uses_brain_output(registry) -> None:
    svc = _service(registry, brain=_FakeBrain(_GOOD_BRAIN_JSON))
    result = await svc.draft(
        SkillCreatorInput(intent="something", name_hint="ignored hint")
    )
    assert result.brain_used is True
    assert result.draft["name"] == "Brain Made Skill"
    assert result.draft["requires_tools"] == ["run-shell"]
    assert result.validation["ok"] is True


@pytest.mark.asyncio
async def test_draft_uses_active_provider_of_brain_manager(registry) -> None:
    """When the injected brain is the live BrainManager, the creator must use
    the user's ACTIVE provider — never a frontier favourite (which on this user's
    box is an unkeyed Claude-API → 401). AP-21: follow the user's selection."""
    provider = _FakeBrain(_GOOD_BRAIN_JSON)
    bm = _FakeBrainManager(provider, name="gemini")
    svc = SkillCreatorService(brain=bm, registry=registry)
    result = await svc.draft(SkillCreatorInput(intent="something"))
    assert result.brain_used is True
    assert result.draft["name"] == "Brain Made Skill"
    assert bm.requested == ["gemini"]  # used the active provider


@pytest.mark.asyncio
async def test_draft_falls_back_when_brain_returns_garbage(registry) -> None:
    svc = _service(registry, brain=_FakeBrain("I cannot help with that."))
    result = await svc.draft(
        SkillCreatorInput(intent="make a thing", name_hint="My Thing")
    )
    # Garbage brain output → deterministic skeleton, brain_used False.
    assert result.brain_used is False
    assert result.draft["name"]
    assert result.validation["ok"] is True


# ----------------------------------------------------------------------
# commit()
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_persists_draft_to_registry(registry, skills_root) -> None:
    svc = SkillCreatorService(
        brain=None, registry=registry, user_skills_root=skills_root
    )
    draft = {
        "name": "Committed Skill",
        "description": "desc",
        "category": "general",
        "tags": ["x"],
        "triggers": [{"type": "voice", "pattern": "^go"}],
        "requires_tools": ["run-shell"],
        "risk_policy": {"default_tier": "ask"},
        "body": "## Committed Skill\n\nDo it.\n",
    }
    created = await svc.commit(draft)
    assert created.name == "Committed Skill"
    fetched = registry.get("Committed Skill")
    assert fetched.frontmatter is not None
    assert "run-shell" in fetched.frontmatter.requires_tools
    assert (skills_root / "committed-skill" / "SKILL.md").exists()
