"""Unit-Tests für den Skill-Loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.skills.loader import discover_skills, parse_skill
from jarvis.skills.schema import SkillLifecycleState


VALID_SKILL_MD = """---
schema_version: "1"
name: test_skill
description: minimal valid
triggers:
  - type: voice
    pattern: "hallo jarvis"
    language: ["de"]
requires_tools:
  - echo
token_budget_estimate: 500
---

# Test Skill Body

Das ist der Body der Skill.

TOOL: echo {"text": "hi"}
"""


INVALID_FRONTMATTER_MD = """---
schema_version: "2"
triggers: not-a-list
---

broken body
"""


BROKEN_YAML_MD = """---
name: "test
triggers: [
---

body
"""


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    (root / "good").mkdir()
    (root / "good" / "SKILL.md").write_text(VALID_SKILL_MD, encoding="utf-8")
    (root / "bad").mkdir()
    (root / "bad" / "SKILL.md").write_text(INVALID_FRONTMATTER_MD, encoding="utf-8")
    (root / "broken").mkdir()
    (root / "broken" / "SKILL.md").write_text(BROKEN_YAML_MD, encoding="utf-8")
    return root


def test_parse_valid_skill(skill_dir: Path):
    sk = parse_skill(skill_dir / "good" / "SKILL.md")
    assert sk.frontmatter is not None
    assert sk.frontmatter.name == "test_skill"
    assert sk.state == SkillLifecycleState.VALIDATED
    assert sk.error is None
    assert sk.body_hash != ""
    assert "Das ist der Body" in sk.body


def test_parse_invalid_frontmatter(skill_dir: Path):
    sk = parse_skill(skill_dir / "bad" / "SKILL.md")
    assert sk.state == SkillLifecycleState.DRAFT
    assert sk.error is not None


def test_parse_broken_yaml(skill_dir: Path):
    sk = parse_skill(skill_dir / "broken" / "SKILL.md")
    assert sk.state == SkillLifecycleState.DRAFT
    assert sk.error is not None


def test_parse_missing_file(tmp_path: Path):
    sk = parse_skill(tmp_path / "does_not_exist.md")
    assert sk.state == SkillLifecycleState.DRAFT
    assert sk.error is not None


def test_discover_finds_all(skill_dir: Path):
    skills = discover_skills(skill_dir)
    names_or_paths = {s.path.parent.name for s in skills}
    assert names_or_paths == {"good", "bad", "broken"}
    assert len(skills) == 3


def test_discover_missing_root(tmp_path: Path):
    assert discover_skills(tmp_path / "nope") == []


def test_body_hash_stable(skill_dir: Path):
    a = parse_skill(skill_dir / "good" / "SKILL.md")
    b = parse_skill(skill_dir / "good" / "SKILL.md")
    assert a.body_hash == b.body_hash


def test_parse_skill_with_utf8_bom(tmp_path: Path):
    """A UTF-8 BOM before the frontmatter must not break parsing.

    Regression: a builtin (jarvis-doc-author/SKILL.md) shipped with a BOM,
    which made the loader miss the ``---`` delimiter and drop the skill to
    DRAFT with a 'name required' error (loaded under the fallback name
    'SKILL'). Reading with ``utf-8-sig`` strips the BOM transparently.
    """
    d = tmp_path / "bom_skill"
    d.mkdir()
    # utf-8-sig prepends the BOM bytes (ef bb bf) before the content.
    (d / "SKILL.md").write_text(VALID_SKILL_MD, encoding="utf-8-sig")

    sk = parse_skill(d / "SKILL.md")

    assert sk.error is None
    assert sk.frontmatter is not None
    assert sk.frontmatter.name == "test_skill"
    assert sk.state == SkillLifecycleState.VALIDATED
