"""REST API for the Agentic IDE's skill library (saved Markdown prompts).

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/agentic-ide/skills              → {"skills": [...]} in the user's order.
    POST   /api/agentic-ide/skills              → save one; 201 (400 on bad input).
    POST   /api/agentic-ide/skills/derive       → title/description a text suggests.
    PUT    /api/agentic-ide/skills/order        → reorder by id.
    GET    /api/agentic-ide/skills/{id}         → one skill with its full text; 404.
    PATCH  /api/agentic-ide/skills/{id}         → partial edit; 404.
    POST   /api/agentic-ide/skills/{id}/used    → count one paste into a terminal.
    DELETE /api/agentic-ide/skills/{id}         → remove (idempotent, {"removed": bool}).

The Skills tab of the IDE side panel drives these; pasting the text into a
pane happens in the browser (a bracketed paste into that pane's terminal), so
no route here ever writes to a PTY. No Brain dependency: works headless.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis.agentic_ide.skill_library import (
    MAX_CONTENT_CHARS,
    SkillLibrary,
    derive_description,
    derive_title,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agentic-ide/skills", tags=["ide-skills"])

# Endpoints are sync ``def`` (FastAPI threadpool), so a threading.Lock
# serialises every read-modify-write of the sidecar.
_LOCK = threading.Lock()


def _library() -> SkillLibrary:
    # Per request, so user_data_dir() is resolved at call time (test sandbox).
    return SkillLibrary()


class SkillCreate(BaseModel):
    title: str = Field(max_length=400)
    content: str = Field(max_length=MAX_CONTENT_CHARS * 2)
    description: str | None = Field(default=None, max_length=2_000)
    hue: str | None = None
    icon: str | None = None


class SkillUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=400)
    content: str | None = Field(default=None, max_length=MAX_CONTENT_CHARS * 2)
    description: str | None = Field(default=None, max_length=2_000)
    hue: str | None = None
    icon: str | None = None


class DeriveRequest(BaseModel):
    content: str = Field(max_length=MAX_CONTENT_CHARS * 2)
    filename: str = Field(default="", max_length=400)


class OrderRequest(BaseModel):
    ids: list[str] = Field(max_length=2_000)


def _not_found(skill_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"No skill with id {skill_id!r}.")


@router.get("", openapi_extra={"x-jarvis-readonly": True})
def list_ide_skills() -> dict[str, Any]:
    """Every saved skill, in the order the user arranged them."""
    with _LOCK:
        skills = _library().list_all()
    return {"skills": [skill.to_dict() for skill in skills]}


@router.post("", status_code=201)
def create_ide_skill(body: SkillCreate) -> dict[str, Any]:
    """Save a titled Markdown text as a new skill at the top of the library."""
    try:
        with _LOCK:
            skill = _library().create(
                title=body.title,
                content=body.content,
                description=body.description,
                hue=body.hue,
                icon=body.icon,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("IDE skill library: saved %r (%d chars).", skill.title, len(skill.content))
    return skill.to_dict()


@router.post("/derive", openapi_extra={"x-jarvis-readonly": True})
def derive_ide_skill_fields(body: DeriveRequest) -> dict[str, Any]:
    """The title and summary a Markdown text suggests (frontmatter, heading, file name)."""
    stem = body.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    if stem.upper() == "SKILL":
        stem = ""
    return {
        "title": derive_title(body.content, stem.replace("-", " ").replace("_", " ")),
        "description": derive_description(body.content),
    }


@router.put("/order")
def reorder_ide_skills(body: OrderRequest) -> dict[str, Any]:
    """Arrange the library: the given ids first, in this order."""
    with _LOCK:
        skills = _library().reorder(body.ids)
    return {"skills": [skill.to_dict() for skill in skills]}


@router.get("/{skill_id}", openapi_extra={"x-jarvis-readonly": True})
def get_ide_skill(skill_id: str) -> dict[str, Any]:
    """One skill with its full Markdown text."""
    with _LOCK:
        skill = _library().get(skill_id)
    if skill is None:
        raise _not_found(skill_id)
    return skill.to_dict()


@router.patch("/{skill_id}")
def update_ide_skill(skill_id: str, body: SkillUpdate) -> dict[str, Any]:
    """Change a skill's title, text, summary, colour or icon."""
    fields = body.model_dump(exclude_unset=True)
    try:
        with _LOCK:
            skill = _library().update(
                skill_id,
                title=fields.get("title"),
                content=fields.get("content"),
                description=fields.get("description"),
                hue=fields.get("hue"),
                icon=fields.get("icon"),
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if skill is None:
        raise _not_found(skill_id)
    return skill.to_dict()


@router.post("/{skill_id}/used")
def mark_ide_skill_used(skill_id: str) -> dict[str, Any]:
    """Count one paste of this skill into a terminal pane."""
    with _LOCK:
        skill = _library().mark_used(skill_id)
    if skill is None:
        raise _not_found(skill_id)
    return skill.to_dict()


@router.delete("/{skill_id}")
def delete_ide_skill(skill_id: str) -> dict[str, Any]:
    """Remove one skill from the library."""
    with _LOCK:
        removed = _library().delete(skill_id)
    return {"ok": True, "removed": removed}
