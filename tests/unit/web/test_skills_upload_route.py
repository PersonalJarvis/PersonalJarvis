"""``POST /api/skills/upload`` — dropping a skill folder onto the UI.

Until now a skill could only reach Jarvis by typing a path or pasting a link;
neither was wired into any screen. This route is the drop-a-folder path, and
what it has to get right is the gap between "what the owner dropped" and "what
a skill folder looks like": a wrapper folder, a repository ZIP where the skill
sits three levels down, a Finder drag carrying ``.DS_Store``.

The trust model is the one ``/import-local`` established — these are the
owner's own files, so a lint-clean skill installs as parsed and an unsafe body
lands as a draft. What is new here is that the bytes arrive over HTTP, so a
crafted archive must never write outside the staging folder.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from jarvis.skills.registry import SkillRegistry
from jarvis.ui.web import skills_routes as routes

_SKILL_MD = (
    "---\n"
    "name: dropped-skill\n"
    "description: A skill the owner dropped onto the window.\n"
    "---\n\n"
    "# Dropped Skill\n\nDo the steps.\n"
)

_UNSAFE_SKILL_MD = (
    "---\n"
    "name: sketchy-skill\n"
    "description: carries a disallowed call in a code block\n"
    "---\n\n"
    "```python\n"
    "import os\n"
    "os.system('curl evil')\n"
    "```\n"
)


def _upload(path: str, data: bytes) -> UploadFile:
    """One multipart part, named the way the browser names it."""
    return UploadFile(filename=Path(path).name, file=io.BytesIO(data))


def _parts(entries: dict[str, bytes]) -> tuple[list[UploadFile], str]:
    """The ``(files, paths)`` pair the UI sends for a folder drop."""
    files = [_upload(path, data) for path, data in entries.items()]
    return files, json.dumps(list(entries))


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries.items():
            archive.writestr(path, data)
    return buffer.getvalue()


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


# ----------------------------------------------------------------------
# Installing
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_dropped_folder_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root, registry, request = _setup(tmp_path, monkeypatch)
    files, paths = _parts(
        {
            "dropped-skill/SKILL.md": _SKILL_MD.encode(),
            "dropped-skill/references/guide.md": b"ref text",
        }
    )

    detail = await routes.upload_skill(request, files, paths)

    assert detail["name"] == "dropped-skill"
    assert detail["state"] == "validated"
    assert (skills_root / "dropped-skill" / "SKILL.md").is_file()
    assert (skills_root / "dropped-skill" / "references" / "guide.md").is_file()
    assert registry.get("dropped-skill").state.value == "validated"


@pytest.mark.asyncio
async def test_a_dropped_zip_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root, _, request = _setup(tmp_path, monkeypatch)
    archive = _zip_bytes(
        {
            "dropped-skill/SKILL.md": _SKILL_MD.encode(),
            "dropped-skill/scripts/run.py": b"print('hi')",
        }
    )

    detail = await routes.upload_skill(request, [_upload("skill.zip", archive)], None)

    assert detail["name"] == "dropped-skill"
    assert (skills_root / "dropped-skill" / "scripts" / "run.py").is_file()


@pytest.mark.asyncio
async def test_the_wrapper_folder_does_not_change_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root, _, request = _setup(tmp_path, monkeypatch)
    files, paths = _parts({"SKILL.md": _SKILL_MD.encode()})

    detail = await routes.upload_skill(request, files, paths)

    assert detail["name"] == "dropped-skill"
    assert (skills_root / "dropped-skill" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_a_skill_buried_in_a_repository_zip_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case: someone downloads a repo ZIP from GitHub."""
    skills_root, _, request = _setup(tmp_path, monkeypatch)
    archive = _zip_bytes(
        {
            "repo-main/README.md": b"# repo",
            "repo-main/skills/dropped-skill/SKILL.md": _SKILL_MD.encode(),
        }
    )

    detail = await routes.upload_skill(request, [_upload("repo.zip", archive)], None)

    assert detail["name"] == "dropped-skill"
    # Only the skill folder travels — the repository README stays behind.
    assert (skills_root / "dropped-skill" / "SKILL.md").is_file()
    assert not (skills_root / "dropped-skill" / "README.md").exists()


@pytest.mark.asyncio
async def test_a_lowercase_skill_md_still_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root, _, request = _setup(tmp_path, monkeypatch)
    files, paths = _parts({"skill.md": _SKILL_MD.encode()})

    detail = await routes.upload_skill(request, files, paths)

    assert detail["name"] == "dropped-skill"
    assert (skills_root / "dropped-skill" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_an_unsafe_body_lands_as_a_draft_with_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root, _, request = _setup(tmp_path, monkeypatch)
    files, paths = _parts({"SKILL.md": _UNSAFE_SKILL_MD.encode()})

    detail = await routes.upload_skill(request, files, paths)

    assert detail["state"] == "draft"
    assert detail["lint_findings"], "the disallowed call must be reported"
    stored = (skills_root / "sketchy-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "state: draft" in stored


@pytest.mark.asyncio
async def test_the_clutter_that_was_dropped_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file count shrinks — the owner is told why, not left guessing."""
    _, _, request = _setup(tmp_path, monkeypatch)
    files, paths = _parts(
        {
            "dropped-skill/SKILL.md": _SKILL_MD.encode(),
            "dropped-skill/.DS_Store": b"junk",
        }
    )

    detail = await routes.upload_skill(request, files, paths)

    assert detail["upload"]["ignored"] == [".DS_Store"]
    assert detail["upload"]["stripped_root"] == "dropped-skill"


# ----------------------------------------------------------------------
# Refusing
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_upload_without_a_skill_md_is_a_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, request = _setup(tmp_path, monkeypatch)
    files, paths = _parts({"notes.md": b"just notes"})

    with pytest.raises(HTTPException) as exc:
        await routes.upload_skill(request, files, paths)
    assert exc.value.status_code == 400
    assert "SKILL.md" in exc.value.detail


@pytest.mark.asyncio
async def test_an_upload_holding_several_skills_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, request = _setup(tmp_path, monkeypatch)
    archive = _zip_bytes(
        {
            "catalog/one/SKILL.md": _SKILL_MD.encode(),
            "catalog/two/SKILL.md": _SKILL_MD.replace(
                "dropped-skill", "other-skill"
            ).encode(),
        }
    )

    with pytest.raises(HTTPException) as exc:
        await routes.upload_skill(request, [_upload("catalog.zip", archive)], None)
    assert exc.value.status_code == 400
    assert "2 skills" in exc.value.detail
    # The paths are listed, so the owner knows which two collided.
    assert "one/SKILL.md" in exc.value.detail


@pytest.mark.asyncio
async def test_a_zip_that_walks_out_of_the_folder_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root, _, request = _setup(tmp_path, monkeypatch)
    archive = _zip_bytes(
        {"SKILL.md": _SKILL_MD.encode(), "../../escaped.md": b"evil"}
    )

    with pytest.raises(HTTPException) as exc:
        await routes.upload_skill(request, [_upload("evil.zip", archive)], None)
    assert exc.value.status_code == 400
    assert not (tmp_path / "escaped.md").exists()
    assert not list(skills_root.iterdir())


@pytest.mark.asyncio
async def test_a_name_collision_is_a_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root, registry, request = _setup(tmp_path, monkeypatch)
    installed = skills_root / "dropped-skill"
    installed.mkdir()
    (installed / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    registry.reload_sync()

    files, paths = _parts({"SKILL.md": _SKILL_MD.encode()})
    with pytest.raises(HTTPException) as exc:
        await routes.upload_skill(request, files, paths)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_paths_that_do_not_line_up_with_the_files_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silent fallback would flatten a folder into loose files."""
    _, _, request = _setup(tmp_path, monkeypatch)
    files = [_upload("SKILL.md", _SKILL_MD.encode())]

    with pytest.raises(HTTPException) as exc:
        await routes.upload_skill(request, files, json.dumps(["a.md", "b.md"]))
    assert exc.value.status_code == 400
    assert "line up" in exc.value.detail


# ----------------------------------------------------------------------
# Inspecting
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inspect_reports_the_skill_without_installing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root, registry, request = _setup(tmp_path, monkeypatch)
    files, paths = _parts(
        {
            "dropped-skill/SKILL.md": _SKILL_MD.encode(),
            "dropped-skill/references/guide.md": b"ref",
            "dropped-skill/.DS_Store": b"junk",
        }
    )

    report = await routes.inspect_skill_upload(request, files, paths)

    assert report["ready"] is True
    assert report["problems"] == []
    assert report["skill"]["name"] == "dropped-skill"
    assert report["skill"]["description"].startswith("A skill the owner dropped")
    assert set(report["files"]) == {"SKILL.md", "references/guide.md"}
    assert report["ignored"] == [".DS_Store"]
    assert report["limits"]["max_file_bytes"] > 0
    # Nothing was written and nothing was registered.
    assert not list(skills_root.iterdir())
    with pytest.raises(KeyError):
        registry.get("dropped-skill")


@pytest.mark.asyncio
async def test_inspect_reports_a_collision_as_a_problem_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The form shows every blocker at once instead of dying on the first."""
    skills_root, registry, request = _setup(tmp_path, monkeypatch)
    installed = skills_root / "dropped-skill"
    installed.mkdir()
    (installed / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    registry.reload_sync()

    files, paths = _parts({"SKILL.md": _SKILL_MD.encode()})
    report = await routes.inspect_skill_upload(request, files, paths)

    assert report["ready"] is False
    assert any("already exists" in problem for problem in report["problems"])


@pytest.mark.asyncio
async def test_inspect_announces_the_draft_landing_of_an_unsafe_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An informed yes: the state shown is the state the install will produce."""
    _, _, request = _setup(tmp_path, monkeypatch)
    files, paths = _parts({"SKILL.md": _UNSAFE_SKILL_MD.encode()})

    report = await routes.inspect_skill_upload(request, files, paths)

    assert report["skill"]["state"] == "draft"
    assert report["lint_findings"]
    # A lint finding is not a blocker — it changes where the skill lands.
    assert report["ready"] is True
