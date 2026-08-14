"""Ownership rules for the skills a plugin package brings with it.

The whole point of the marker file is that installing and uninstalling a
plugin must never touch a skill the plugin does not own. These tests are the
two accidents that would otherwise happen: overwriting the user's own skill
on install, and carrying it away on uninstall.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.marketplace.agent_plugins_loader import BundledSkill
from jarvis.marketplace.bundled_skills import (
    MARKER_NAME,
    SkillOwnershipError,
    owner_of,
    remove_bundled_skills,
    write_bundled_skills,
)

SKILL_MD = "---\nname: todo-triage\ndescription: Sort the inbox.\n---\n\nGroup by due date.\n"


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr("jarvis.marketplace.bundled_skills.user_skills_dir", lambda: root)
    return root


def _skill() -> BundledSkill:
    return BundledSkill(name="todo-triage", skill_md=SKILL_MD)


def test_install_writes_skill_and_marker(skills_root: Path) -> None:
    result = write_bundled_skills("todo-fox", [_skill()], version="1.2.0")

    assert result.written == ["todo-triage"]
    assert result.conflicts == []
    assert (skills_root / "todo-triage" / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    assert owner_of(skills_root / "todo-triage") == "todo-fox"


def test_reinstall_overwrites_its_own_skill(skills_root: Path) -> None:
    write_bundled_skills("todo-fox", [_skill()])
    updated = BundledSkill(name="todo-triage", skill_md=SKILL_MD + "\nNewer guidance.\n")

    result = write_bundled_skills("todo-fox", [updated], version="1.3.0")

    assert result.conflicts == []
    body = (skills_root / "todo-triage" / "SKILL.md").read_text(encoding="utf-8")
    assert "Newer guidance." in body


def test_a_users_own_skill_is_never_overwritten(skills_root: Path) -> None:
    mine = skills_root / "todo-triage"
    mine.mkdir()
    (mine / "SKILL.md").write_text("---\nname: todo-triage\n---\nMy own work.\n", encoding="utf-8")

    result = write_bundled_skills("todo-fox", [_skill()])

    assert result.written == []
    assert result.conflicts == ["todo-triage"]
    assert "My own work." in (mine / "SKILL.md").read_text(encoding="utf-8")


def test_uninstall_removes_only_owned_skills(skills_root: Path) -> None:
    write_bundled_skills("todo-fox", [_skill()])
    foreign = skills_root / "note-taker"
    foreign.mkdir()
    (foreign / "SKILL.md").write_text("---\nname: note-taker\n---\nMine.\n", encoding="utf-8")

    removed = remove_bundled_skills("todo-fox", ["todo-triage", "note-taker"])

    assert removed == ["todo-triage"]
    assert not (skills_root / "todo-triage").exists()
    assert foreign.exists()


def test_uninstall_leaves_a_skill_another_plugin_took_over(skills_root: Path) -> None:
    write_bundled_skills("other-plugin", [_skill()])

    removed = remove_bundled_skills("todo-fox", ["todo-triage"])

    assert removed == []
    assert (skills_root / "todo-triage" / MARKER_NAME).exists()


def test_name_escaping_the_skills_root_is_refused(skills_root: Path) -> None:
    """Second line of defence: the loader already applies the spec name rules,
    but the guard sits next to the write so the guarantee cannot drift away
    from it."""
    with pytest.raises(SkillOwnershipError):
        write_bundled_skills("todo-fox", [BundledSkill(name="../escape", skill_md=SKILL_MD)])
