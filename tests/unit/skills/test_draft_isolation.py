"""Tests für Plan-§AD-8 + §AP-6: Draft-Skills triggern NIEMALS automatisch.

Akzeptanzkriterien aus Phase 7.5:
- list_active() ignoriert DRAFT-Skills komplett
- list_drafts() zeigt sie an
- promote() bringt sie auf state=active
- promote() blockiert bei unsafe Skill-Body (eval/exec/...)
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
        description="Test-Skill für Draft-Isolation",
        intent="test intent",
        triggers_yaml="[{type: voice, pattern: '^test'}]",
        body_markdown="## Test Skill\n\nNur ein Marker-Body.",
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
        # 1 Draft anlegen
        write_draft(_draft(slug="draft-skill"), user_skills_root=skills_root)
        registry = SkillRegistry(skills_root, bus=None)
        registry.reload_sync()
        assert len(registry.list_drafts()) == 1
        assert len(registry.list_active()) == 0

    def test_list_active_includes_validated_skill(
        self, skills_root: Path
    ) -> None:
        # Skill OHNE state-Field im Frontmatter → Loader setzt VALIDATED
        skill_dir = skills_root / "active-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            'schema_version: "1"\n'
            "name: active-skill\n"
            "description: legacy skill ohne state field\n"
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
        # Skill ist jetzt aktiv (oder validated, beides ist active-Pool)
        assert promoted.state in (
            SkillLifecycleState.ACTIVE,
            SkillLifecycleState.VALIDATED,
        )
        assert any(
            s.path.parent.name == "promo-test" for s in registry.list_active()
        )
        # Frontmatter hat state: active
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
            "description: bereits aktiv\n"
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
        # Skill bleibt im Draft-Status
        registry.reload_sync()
        assert any(
            s.path.parent.name == "unsafe-skill"
            for s in registry.list_drafts()
        )
