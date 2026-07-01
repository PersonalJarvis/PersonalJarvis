"""Tests for Plan-§AD-8 + §AP-6: draft skills NEVER auto-trigger.

Acceptance criteria from Phase 7.5:
- list_active() completely ignores DRAFT skills
- list_drafts() shows them
- promote() moves them to state=active
- promote() blocks on an unsafe skill body (eval/exec/...)
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jarvis.skills.authoring import SkillDraft, write_draft
from jarvis.skills.authoring.draft_writer import UnsafeSkillError
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.schema import SkillLifecycleState

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _draft(**overrides) -> SkillDraft:
    defaults: dict = dict(
        slug="test-skill",
        name="Test Skill",
        description="Test skill for draft isolation",
        intent="test intent",
        triggers_yaml="[{type: voice, pattern: '^test'}]",
        body_markdown="## Test Skill\n\nJust a marker body.",
        state="draft",
    )
    defaults.update(overrides)
    return SkillDraft(**defaults)


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "user_skills"
    root.mkdir()
    return root


# ----------------------------------------------------------------------
# Hot-Reload-Filter
# ----------------------------------------------------------------------


class TestDraftIsolation:
    def test_list_active_excludes_drafts(
        self, skills_root: Path
    ) -> None:
        # Create 1 draft
        write_draft(_draft(slug="draft-skill"), user_skills_root=skills_root)
        registry = SkillRegistry(skills_root, bus=None)
        registry.reload_sync()
        assert len(registry.list_drafts()) == 1
        assert len(registry.list_active()) == 0

    def test_list_active_includes_validated_skill(
        self, skills_root: Path
    ) -> None:
        # Skill WITHOUT a state field in frontmatter → loader sets VALIDATED
        skill_dir = skills_root / "active-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            'schema_version: "1"\n'
            "name: active-skill\n"
            "description: legacy skill without state field\n"
            "---\n\n"
            "## Body\n",
            encoding="utf-8",
        )
        registry = SkillRegistry(skills_root, bus=None)
        registry.reload_sync()
        active = registry.list_active()
        assert any(s.name == "active-skill" for s in active)
        assert len(registry.list_drafts()) == 0


# ----------------------------------------------------------------------
# Promote-Flow
# ----------------------------------------------------------------------


class TestPromote:
    def test_promote_draft_makes_active(self, skills_root: Path) -> None:
        write_draft(_draft(slug="promo-test"), user_skills_root=skills_root)
        registry = SkillRegistry(skills_root, bus=None)
        registry.reload_sync()
        assert registry.list_drafts()
        promoted = registry.promote("promo-test")
        # Skill is now active (or validated, both are in the active pool)
        assert promoted.state in (
            SkillLifecycleState.ACTIVE,
            SkillLifecycleState.VALIDATED,
        )
        assert any(
            s.path.parent.name == "promo-test" for s in registry.list_active()
        )
        # Frontmatter has state: active
        text = promoted.path.read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---", 2)[1])
        assert fm["state"] == "active"

    def test_promote_unknown_slug_raises(self, skills_root: Path) -> None:
        registry = SkillRegistry(skills_root, bus=None)
        registry.reload_sync()
        with pytest.raises(KeyError):
            registry.promote("nonexistent")

    def test_promote_non_draft_raises(self, skills_root: Path) -> None:
        skill_dir = skills_root / "active-already"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            'schema_version: "1"\n'
            "name: active-already\n"
            "description: already active\n"
            "---\n",
            encoding="utf-8",
        )
        registry = SkillRegistry(skills_root, bus=None)
        registry.reload_sync()
        with pytest.raises(RuntimeError):
            registry.promote("active-already")

    def test_promote_unsafe_skill_blocked(self, skills_root: Path) -> None:
        unsafe_body = (
            "## Unsafe\n\n"
            "```python\n"
            "eval('boom')\n"
            "```\n"
        )
        write_draft(
            _draft(slug="unsafe-skill", body_markdown=unsafe_body),
            user_skills_root=skills_root,
        )
        registry = SkillRegistry(skills_root, bus=None)
        registry.reload_sync()
        with pytest.raises(UnsafeSkillError):
            registry.promote("unsafe-skill")
        # Skill remains in draft status
        registry.reload_sync()
        assert any(
            s.path.parent.name == "unsafe-skill"
            for s in registry.list_drafts()
        )
