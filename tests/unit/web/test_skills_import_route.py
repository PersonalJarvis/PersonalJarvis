"""AP-15 guard: a skill imported from a link lands as DRAFT, never live.

`POST /api/skills/import` downloads a third-party SKILL.md the user has not
reviewed. `parse_skill` treats a missing `state:` key as VALIDATED — which is
the active pool — so writing the download verbatim would auto-activate remote
content. Worse, the remote file can *declare itself* `state: active`.

Both paths are pinned here: whatever the remote says, the stored skill is a
draft, and promotion stays an explicit human act.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from jarvis.ui.web import skills_routes as routes

_NO_STATE = (
    "---\n"
    'schema_version: "1"\n'
    "name: remote-skill\n"
    "description: a skill downloaded from the internet\n"
    "---\n\n"
    "## Body\n\nJust a marker body.\n"
)

_SELF_DECLARED_ACTIVE = (
    "---\n"
    'schema_version: "1"\n'
    "name: remote-skill\n"
    "description: a skill that declares itself active\n"
    "state: active\n"
    "---\n\n"
    "## Body\n\nJust a marker body.\n"
)


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def _client_returning(payload: str):
    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def get(self, _url: str) -> _Response:
            return _Response(payload)

    return _Client


class _Registry:
    """Minimal stand-in: nothing installed, and it records what was added."""

    def __init__(self) -> None:
        self._skills: dict[str, object] = {}

    def get(self, name: str) -> object:
        raise KeyError(name)


def _request(registry: _Registry) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(skill_registry=registry))
    )


async def _import(payload: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("httpx.AsyncClient", _client_returning(payload))
    monkeypatch.setattr(routes, "user_skills_dir", lambda: tmp_path)
    registry = _Registry()
    body = routes.SkillImportBody(
        input="https://example.invalid/skills/remote-skill/SKILL.md"
    )
    detail = await routes.import_skill(body, _request(registry))
    stored = tmp_path / "remote-skill" / "SKILL.md"
    frontmatter = yaml.safe_load(stored.read_text(encoding="utf-8").split("---", 2)[1])
    return detail, frontmatter


@pytest.mark.asyncio
async def test_import_without_state_lands_as_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail, frontmatter = await _import(_NO_STATE, tmp_path, monkeypatch)

    assert frontmatter["state"] == "draft"
    assert detail["state"] == "draft"


@pytest.mark.asyncio
async def test_import_cannot_self_declare_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote file asking for `state: active` is downgraded, not honored."""
    detail, frontmatter = await _import(_SELF_DECLARED_ACTIVE, tmp_path, monkeypatch)

    assert frontmatter["state"] == "draft"
    assert detail["state"] == "draft"


@pytest.mark.asyncio
async def test_import_preserves_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stamping the state must not disturb the rest of the file."""
    monkeypatch.setattr("httpx.AsyncClient", _client_returning(_NO_STATE))
    monkeypatch.setattr(routes, "user_skills_dir", lambda: tmp_path)

    body = routes.SkillImportBody(input="https://example.invalid/SKILL.md")
    await routes.import_skill(body, _request(_Registry()))

    stored = (tmp_path / "remote-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "Just a marker body." in stored
    assert "description: a skill downloaded from the internet" in stored


@pytest.mark.asyncio
async def test_import_refuses_a_traversal_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 2026-08-12 (BLOCKER class): a remote SKILL.md declaring
    ``name: ../../evil`` must never turn the install path into a write
    outside the skills root.
    """
    payload = _NO_STATE.replace("name: remote-skill", 'name: "../../evil"')
    monkeypatch.setattr("httpx.AsyncClient", _client_returning(payload))
    skills_root = tmp_path / "skills-root"
    skills_root.mkdir()
    monkeypatch.setattr(routes, "user_skills_dir", lambda: skills_root)

    from fastapi import HTTPException

    body = routes.SkillImportBody(input="https://example.invalid/SKILL.md")
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill(body, _request(_Registry()))
    assert exc.value.status_code == 400
    assert not (tmp_path / "evil").exists()
    assert not list(skills_root.iterdir())
