"""``POST /api/skills/import-local`` — the bridge for externally-authored skills.

Live forensic 2026-08-12: every skill the maintainer "built" lived in a coding
agent's folder (``.claude/skills/<name>/``) that the Jarvis registry never
scans, so the brain could not know them. This route copies such a folder into
the user skills directory — the documented manual install path, automated.

Trust model under test: a lint-clean local skill is honored as parsed
(missing ``state:`` → VALIDATED, like the manual copy), while a body that
fails the promotion safety lint is stamped DRAFT with the findings returned.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.skills.registry import SkillRegistry
from jarvis.ui.web import skills_routes as routes

_CLAUDE_CODE_STYLE = (
    "---\n"
    "name: my-own-skill\n"
    "description: A skill the user authored in a coding agent folder.\n"
    "---\n\n"
    "# My Own Skill\n\nDo the steps.\n"
)

_UNSAFE_BODY = (
    "---\n"
    "name: sketchy-skill\n"
    "description: carries a disallowed call in a code block\n"
    "---\n\n"
    "```python\n"
    "import os\n"
    "os.system('curl evil')\n"
    "```\n"
)


def _write_source(tmp_path: Path, content: str, *, with_reference: bool = False) -> Path:
    src = tmp_path / "external" / "my-skill-folder"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(content, encoding="utf-8")
    if with_reference:
        (src / "references").mkdir()
        (src / "references" / "guide.md").write_text("ref text", encoding="utf-8")
    return src


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    skills_root = tmp_path / "user-skills"
    skills_root.mkdir()
    monkeypatch.setattr(routes, "user_skills_dir", lambda: skills_root)
    registry = SkillRegistry(root=skills_root)
    registry.reload_sync()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(skill_registry=registry))
    )
    return skills_root, registry, request


@pytest.mark.asyncio
async def test_lint_clean_local_skill_imports_as_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _write_source(tmp_path, _CLAUDE_CODE_STYLE, with_reference=True)
    skills_root, registry, request = _setup(tmp_path, monkeypatch)

    body = routes.SkillImportLocalBody(path=str(src))
    detail = await routes.import_skill_from_path(body, request)

    assert detail["name"] == "my-own-skill"
    assert detail["state"] == "validated"
    assert detail["lint_findings"] == []
    # Copied under the SKILL NAME, bundle resources included.
    assert (skills_root / "my-own-skill" / "SKILL.md").is_file()
    assert (skills_root / "my-own-skill" / "references" / "guide.md").is_file()
    # The registry was properly reloaded — the skill is live.
    assert registry.get("my-own-skill").state.value == "validated"


@pytest.mark.asyncio
async def test_direct_skill_md_path_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _write_source(tmp_path, _CLAUDE_CODE_STYLE)
    _, _, request = _setup(tmp_path, monkeypatch)

    body = routes.SkillImportLocalBody(path=str(src / "SKILL.md"))
    detail = await routes.import_skill_from_path(body, request)
    assert detail["name"] == "my-own-skill"


@pytest.mark.asyncio
async def test_unsafe_body_is_stamped_draft_with_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The promotion safety lint keeps its teeth on the import path."""
    src = _write_source(tmp_path, _UNSAFE_BODY)
    skills_root, _, request = _setup(tmp_path, monkeypatch)

    body = routes.SkillImportLocalBody(path=str(src))
    detail = await routes.import_skill_from_path(body, request)

    assert detail["state"] == "draft"
    assert detail["lint_findings"], "the disallowed call must be reported"
    stored = (skills_root / "sketchy-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "state: draft" in stored


@pytest.mark.asyncio
async def test_name_collision_is_a_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _write_source(tmp_path, _CLAUDE_CODE_STYLE)
    skills_root, registry, request = _setup(tmp_path, monkeypatch)
    installed = skills_root / "my-own-skill"
    installed.mkdir()
    (installed / "SKILL.md").write_text(_CLAUDE_CODE_STYLE, encoding="utf-8")
    registry.reload_sync()

    body = routes.SkillImportLocalBody(path=str(src))
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_from_path(body, request)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_builtin_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _CLAUDE_CODE_STYLE.replace("my-own-skill", "morning-routine")
    src = _write_source(tmp_path, content)
    _, _, request = _setup(tmp_path, monkeypatch)

    body = routes.SkillImportLocalBody(path=str(src))
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_from_path(body, request)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_missing_skill_md_is_a_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "external" / "empty-folder"
    empty.mkdir(parents=True)
    _, _, request = _setup(tmp_path, monkeypatch)

    body = routes.SkillImportLocalBody(path=str(empty))
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_from_path(body, request)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_importing_from_inside_the_skills_dir_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root, _, request = _setup(tmp_path, monkeypatch)
    inside = skills_root / "already-here"
    inside.mkdir()
    (inside / "SKILL.md").write_text(_CLAUDE_CODE_STYLE, encoding="utf-8")

    body = routes.SkillImportLocalBody(path=str(inside))
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_from_path(body, request)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evil_name",
    [
        "../../evil",
        "..\\\\..\\\\evil",
        "C:/Users/Public/evil",
        "sub/dir",
        "name with spaces",
    ],
)
async def test_traversal_name_in_frontmatter_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evil_name: str
) -> None:
    """Review finding 2026-08-12 (BLOCKER): the frontmatter ``name`` becomes
    the install folder, and pydantic only checks non-empty — a crafted
    ``name: ../../evil`` (or an absolute path, which pathlib joins by
    DISCARDING the base) must never produce a write outside the skills root.
    """
    content = _CLAUDE_CODE_STYLE.replace("my-own-skill", f'"{evil_name}"')
    src = _write_source(tmp_path, content)
    skills_root, _, request = _setup(tmp_path, monkeypatch)

    body = routes.SkillImportLocalBody(path=str(src))
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_from_path(body, request)
    assert exc.value.status_code == 400
    # Nothing escaped: the only entries under tmp_path are the fixtures.
    assert not (tmp_path / "evil").exists()
    assert not list(skills_root.iterdir())
