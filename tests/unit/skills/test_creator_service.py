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
from jarvis.skills.schema import SkillLifecycleState


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


def test_render_draft_stamps_state_draft_in_frontmatter() -> None:
    """AP-15: the AI creator's rendered preview must carry ``state: draft`` so
    the loader never resolves LLM-generated content to VALIDATED/"on"."""
    draft = {
        "name": "Rendered Skill",
        "description": "desc",
        "category": "general",
        "body": "## Rendered Skill\n\nHello.\n",
    }
    skill_md = render_skill_md(draft)
    _, frontmatter = validate_skill_md(skill_md)
    assert frontmatter is not None
    assert frontmatter["state"] == "draft"


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

    # AP-15: an AI-generated skill must land as DRAFT, never auto-active —
    # both on the returned Skill object and in the persisted file the loader
    # will re-parse on the next reload.
    assert created.state == SkillLifecycleState.DRAFT
    assert fetched.state == SkillLifecycleState.DRAFT
    on_disk = (skills_root / "committed-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "state: draft" in on_disk


# ----------------------------------------------------------------------
# 2026-08-18 — voice authoring: brain ladder, live inventory, author()
# ----------------------------------------------------------------------


class _RecordingBrain(_FakeBrain):
    """A fake brain that also keeps the request it was asked with."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.requests: list = []

    def complete(self, request):  # noqa: ANN001
        self.requests.append(request)
        return super().complete(request)


class _DeadBrain:
    """A provider that is configured but cannot answer (no key)."""

    def complete(self, request):  # noqa: ANN001, ARG002
        raise RuntimeError("No API key found")


_ROUTINE_BRAIN_JSON = """{
  "name": "Morgenroutine",
  "description": "Reads mail, tickets and calendar in the morning, then plays a song.",
  "category": "productivity",
  "tags": ["morning", "routine"],
  "triggers": [
    {"type": "voice", "pattern": "(morgenroutine|morning routine)", "language": ["de", "en"]},
    {"type": "voice", "pattern": ".*"},
    {"type": "voice", "pattern": "(unclosed"},
    {"type": "schedule", "cron": "not a cron"}
  ],
  "requires_tools": ["gmail", "made_up_tool"],
  "risk_policy": {"default_tier": "monitor"},
  "body": "Short morning briefing.\\n\\n## Steps\\n\\n1. gmail: unread mail.\\n2. youtube_music: an 80s classic.\\n",
  "assumptions": ["Lenya Tickets = Linear"]
}"""


@pytest.mark.asyncio
async def test_brain_ladder_crosses_a_dead_active_provider(registry, monkeypatch) -> None:
    """The user's active provider is tried first (AP-21) — and when it cannot
    answer (no key), the ladder crosses to the Tool Model instead of handing
    the user a skeleton (AP-22)."""
    from types import SimpleNamespace

    from jarvis.brain import resolver as resolver_mod

    dead = _DeadBrain()
    bm = _FakeBrainManager(dead, name="openrouter")
    tool_model = _RecordingBrain(_GOOD_BRAIN_JSON)
    tool_model.name = "vertex"
    monkeypatch.setattr(resolver_mod, "resolve_tool_model_brain", lambda cfg, bus=None: tool_model)
    monkeypatch.setattr(resolver_mod, "resolve_quality_brain", lambda cfg, bus=None: None)
    monkeypatch.setattr(resolver_mod, "resolve_frontier_brain", lambda cfg, bus=None: None)

    svc = SkillCreatorService(brain=bm, registry=registry, config=SimpleNamespace())
    result = await svc.draft(SkillCreatorInput(intent="something"))
    assert bm.requested == ["openrouter"]  # the active provider was tried first
    assert result.brain_used is True
    assert result.brain_source.startswith("tool_model")
    assert result.draft["name"] == "Brain Made Skill"


@pytest.mark.asyncio
async def test_prompt_carries_the_live_inventory_and_the_skill_contract(registry) -> None:
    """What makes the draft a WORKING skill: the model sees which connectors
    are attached (by name) and the trigger/schedule contract."""
    from jarvis.skills.creator_service import AuthoringContext

    brain = _RecordingBrain(_GOOD_BRAIN_JSON)
    ctx = AuthoringContext(
        tools=(("gmail", "Read and send Gmail."), ("youtube_music", "Play music.")),
        skills=(("morning-routine", "Morning briefing."),),
    )
    svc = SkillCreatorService(brain=brain, registry=registry, context=ctx)
    await svc.draft(
        SkillCreatorInput(intent="jeden morgen um 6 mails vorlesen", schedule_hint="0 6 * * *", language="de")  # i18n-allow: test input
    )
    request = brain.requests[0]
    user = request.messages[0].content
    assert "AVAILABLE TOOLS" in user
    assert "gmail" in user and "youtube_music" in user
    assert "INSTALLED SKILLS" in user and "morning-routine" in user
    assert "Schedule hint: 0 6 * * *" in user
    assert "language: de" in user
    assert "cron" in request.system and "schedule" in request.system
    assert "AVAILABLE TOOLS" in request.system


@pytest.mark.asyncio
async def test_brain_draft_is_normalised_triggers_and_tools(registry) -> None:
    """Overbroad / invalid voice patterns and invalid crons are dropped, the
    caller's cron hint is added when the model forgot it, and requires_tools is
    filtered to connectors that exist."""
    from jarvis.skills.creator_service import AuthoringContext

    ctx = AuthoringContext(tools=(("gmail", ""), ("youtube_music", "")))
    svc = SkillCreatorService(brain=_FakeBrain(_ROUTINE_BRAIN_JSON), registry=registry, context=ctx)
    result = await svc.draft(SkillCreatorInput(intent="x", schedule_hint="0 6 * * *"))
    assert result.brain_used is True
    triggers = result.draft["triggers"]
    voice = [t["pattern"] for t in triggers if t["type"] == "voice"]
    crons = [t["cron"] for t in triggers if t["type"] == "schedule"]
    assert voice == ["(morgenroutine|morning routine)"]
    assert crons == ["0 6 * * *"]
    assert result.draft["requires_tools"] == ["gmail"]
    assert result.validation["ok"] is True


@pytest.mark.asyncio
async def test_author_commits_the_brain_draft_as_a_draft_skill(registry, skills_root) -> None:
    svc = SkillCreatorService(
        brain=_FakeBrain(_ROUTINE_BRAIN_JSON), registry=registry, user_skills_root=skills_root
    )
    authored = await svc.author(SkillCreatorInput(intent="morgenroutine", schedule_hint="0 6 * * *"))  # i18n-allow: test input
    # The committed KEY is the slug — a key like "Morning Routine 2" is one
    # ``run-skill`` cannot find by any name a human would say (live
    # 2026-08-20) — while the reported NAME stays the display name, because
    # that is what the spoken confirmation reads back to the user.
    assert authored.name == "Morgenroutine"
    assert authored.slug == "morgenroutine"
    assert authored.skill.name == "morgenroutine"
    assert authored.skill.state == SkillLifecycleState.DRAFT  # AP-15
    on_disk = (skills_root / "morgenroutine" / "SKILL.md").read_text(encoding="utf-8")
    assert "state: draft" in on_disk
    assert "cron: 0 6 * * *" in on_disk
    assert "youtube_music" in on_disk


@pytest.mark.asyncio
async def test_author_refuses_to_commit_a_skeleton(registry, skills_root) -> None:
    """No brain → no skill. The skeleton is the user's sentence pasted into a
    template; on the voice path nobody edits it, so nothing must be written."""
    from jarvis.skills.creator_service import SkillCreatorUnavailable

    svc = SkillCreatorService(brain=None, registry=registry, user_skills_root=skills_root)
    with pytest.raises(SkillCreatorUnavailable):
        await svc.author(SkillCreatorInput(intent="pause spotify when I talk"))
    assert not any(skills_root.iterdir())


@pytest.mark.asyncio
async def test_author_suffixes_the_name_on_a_collision(registry, skills_root) -> None:
    svc = SkillCreatorService(
        brain=_FakeBrain(_ROUTINE_BRAIN_JSON), registry=registry, user_skills_root=skills_root
    )
    first = await svc.author(SkillCreatorInput(intent="a"))
    second = await svc.author(SkillCreatorInput(intent="a"))
    assert first.name == "Morgenroutine"
    # The KEY takes "-2" so it stays a slug; the spoken name takes " 2" so
    # the user hears which of the two was created.
    assert second.name == "Morgenroutine 2"
    assert second.skill.name == "morgenroutine-2"
    assert (skills_root / "morgenroutine-2" / "SKILL.md").exists()
    assert second.slug == "morgenroutine-2"
