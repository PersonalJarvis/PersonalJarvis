"""Standalone community skills install from the feed, not from a link.

The 2026-08-14 outage is the reason this path exists: the registry repo went
private while GitHub Pages kept serving the index, so every `raw_url` in a
live feed answered 404 and no skill could be installed. An embedded
`skill_md` cannot go missing while the feed advertising it is reachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.marketplace.agent_plugins_loader import AgentPluginError, BundledSkill
from jarvis.marketplace.bundled_skills import write_bundled_skills
from jarvis.marketplace.community_install import install_community_skill
from jarvis.marketplace.community_source import CommunityIndex

SKILL_MD = (
    "---\n"
    'schema_version: "1"\n'
    "name: three-bullet-brief\n"
    "description: Summarise anything in three bullets.\n"
    "---\n\n"
    "Answer in exactly three bullets, shortest first.\n"
)


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr("jarvis.core.paths.user_skills_dir", lambda: root)
    monkeypatch.setattr("jarvis.marketplace.bundled_skills.user_skills_dir", lambda: root)
    return root


def test_embedded_skill_is_written(skills_root: Path) -> None:
    path = install_community_skill("three-bullet-brief", SKILL_MD)

    assert path == skills_root / "three-bullet-brief" / "SKILL.md"
    assert path.read_text(encoding="utf-8") == SKILL_MD


def test_standalone_skill_carries_no_ownership_marker(skills_root: Path) -> None:
    """The user owns it outright and removes it from the Skills view."""
    install_community_skill("three-bullet-brief", SKILL_MD)

    marker = skills_root / "three-bullet-brief" / ".jarvis-plugin.json"
    assert not marker.exists()


def test_the_same_rules_apply_as_to_a_bundled_skill(skills_root: Path) -> None:
    """A rule enforced on only one publishing route is a rule authors route
    around: a standalone skill may not self-declare its risk tier either."""
    escalating = SKILL_MD.replace("---\n\n", "risk_policy:\n  default_tier: safe\n---\n\n")

    with pytest.raises(AgentPluginError, match="risk_policy"):
        install_community_skill("three-bullet-brief", escalating)


def test_a_plugins_skill_cannot_be_taken_over(skills_root: Path) -> None:
    write_bundled_skills(
        "todo-fox", [BundledSkill(name="three-bullet-brief", skill_md=SKILL_MD)]
    )

    with pytest.raises(ValueError, match="todo-fox"):
        install_community_skill("three-bullet-brief", SKILL_MD)


def test_reinstall_updates_a_users_own_skill(skills_root: Path) -> None:
    install_community_skill("three-bullet-brief", SKILL_MD)
    newer = SKILL_MD + "\nPrefer numbers over adjectives.\n"

    install_community_skill("three-bullet-brief", newer)

    body = (skills_root / "three-bullet-brief" / "SKILL.md").read_text(encoding="utf-8")
    assert "Prefer numbers" in body


def test_index_accepts_an_embedded_skill_and_keeps_raw_url_optional() -> None:
    index = CommunityIndex.model_validate(
        {
            "revision": 3,
            "skills": [
                {"name": "three-bullet-brief", "skill_md": SKILL_MD},
                {"name": "legacy-skill", "raw_url": "https://example.test/SKILL.md"},
            ],
        }
    )

    assert index.skills[0].skill_md == SKILL_MD
    assert index.skills[0].raw_url is None
    assert index.skills[1].skill_md is None
