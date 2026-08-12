"""REST API for the skill system (desktop UI).

Endpoints:
- ``GET  /api/skills``                → list (no body, lean for the sidebar).
- ``GET  /api/skills/{name}``         → full skill incl. Markdown body.
- ``PUT  /api/skills/{name}``         → update body (and optionally frontmatter).
  For built-in skills: ``admin_password`` is required in the request body.
- ``POST /api/skills/{name}/enable``  → state -> ACTIVE.
- ``POST /api/skills/{name}/disable`` → state -> DISABLED.
- ``POST /api/skills/reload``         → force ``Registry.reload()``.

The router expects a ``SkillRegistry`` on ``app.state.skill_registry`` — it is
set by the ``WebServer`` at startup (after ``ensure_user_skills_dir()``).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from jarvis.core.paths import user_skills_dir
from jarvis.skills.builtin import BUILTIN_SKILL_NAMES
from jarvis.skills.finder import SearchFilters, SkillFinder
from jarvis.skills.loader import parse_skill
from jarvis.skills.schema import RESOURCE_KINDS, Skill, SkillLifecycleState

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _require_optional_module(module: str, feature: str) -> None:
    """Answer a clean 501 when an optional feature module is absent from this build.

    Some features (the AI Skill Creator, the link-health checker) live in modules
    that are not shipped in every distribution. Rather than let the lazy import
    raise an unhandled ``ModuleNotFoundError`` (a 500 that reads as a bug), probe
    for the module first and surface an honest ``501 Not Implemented``.
    """
    if importlib.util.find_spec(module) is None:
        raise HTTPException(
            status_code=501,
            detail=f"{feature} is not available in this build.",
        )


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------

def _require_registry(request: Request) -> Any:
    reg = getattr(request.app.state, "skill_registry", None)
    if reg is None:
        raise HTTPException(status_code=503, detail="SkillRegistry not available")
    return reg


def _security_cfg(request: Request) -> Any:
    """Fetches the SecurityConfig from app state. ``None`` when the config is
    missing (e.g. in tests with a mock app) — admin checks then fall back to
    "no hash set = built-in edits locked"."""
    cfg = getattr(request.app.state, "config", None)
    if cfg is None:
        return None
    return getattr(cfg, "security", None)


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------

def _is_builtin(name: str) -> bool:
    return name in BUILTIN_SKILL_NAMES


def _skill_to_summary(s: Skill) -> dict[str, Any]:
    """Lean representation for ``GET /api/skills`` — without the body."""
    fm = s.frontmatter
    # resources as a plain dict with lists (instead of tuples) for JSON serialization
    resources = {k: list(v) for k, v in s.resources.items()}
    resource_count = sum(len(v) for v in resources.values())
    return {
        "name": s.name,
        "state": s.state.value,
        "is_builtin": _is_builtin(s.name),
        "error": s.error,
        "description": fm.description if fm else "",
        "category": fm.category if fm else "unknown",
        "version": fm.version if fm else "",
        "triggers": [t.model_dump() for t in fm.triggers] if fm else [],
        "tags": list(fm.tags) if fm else [],
        "resources": resources,
        "resource_count": resource_count,
    }


def _skill_to_detail(s: Skill) -> dict[str, Any]:
    """Full detail incl. body + frontmatter dump."""
    out = _skill_to_summary(s)
    out["body"] = s.body
    out["body_hash"] = s.body_hash
    out["frontmatter"] = s.frontmatter.model_dump() if s.frontmatter else None
    try:
        rel = s.path.relative_to(user_skills_dir())
        out["path"] = str(rel).replace("\\", "/")
    except ValueError:
        # Skill does not live under user_skills_dir() (e.g. a test fixture)
        out["path"] = str(s.path)
    return out


def _sort_by_order(skills: list[Skill], order: list[str]) -> list[Skill]:
    """Apply the user's custom list order.

    Skills named in ``order`` come first, in that order; any skill not in the
    order (e.g. freshly created) is appended after them, sorted by name. Names in
    ``order`` that no longer resolve to a skill are simply ignored.
    """
    index = {name: i for i, name in enumerate(order)}
    ordered = sorted(
        (s for s in skills if s.name in index), key=lambda s: index[s.name]
    )
    rest = sorted(
        (s for s in skills if s.name not in index), key=lambda s: s.name.lower()
    )
    return ordered + rest


def _resolve_resource_path(skill: Skill, kind: str, filename: str) -> Path:
    """Resolves a resource path and makes sure it does not escape the
    skill root (path-traversal protection).

    Raises HTTPException on an unknown kind, a missing folder, or a path escape.
    """
    if kind not in RESOURCE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown resource kind '{kind}' (expected: {list(RESOURCE_KINDS)})",
        )
    kind_root = (skill.root / kind).resolve()
    if not kind_root.is_dir():
        raise HTTPException(
            status_code=404, detail=f"Folder '{kind}/' does not exist"
        )
    target = (kind_root / filename).resolve()
    try:
        target.relative_to(kind_root)
    except ValueError:
        # Symlink or `..` construct that points outside the kind root
        raise HTTPException(
            status_code=400, detail="Path traversal outside the resource folder"
        )
    if not target.is_file():
        raise HTTPException(
            status_code=404, detail=f"File '{kind}/{filename}' not found"
        )
    return target


# ----------------------------------------------------------------------
# Admin password check
# ----------------------------------------------------------------------

def _check_admin_pass(provided: str | None, security_cfg: Any) -> bool:
    """Checks an admin password against ``security.admin_password_hash``.

    - No hash set (empty string) -> always False (built-in edits locked).
    - No password provided -> False.
    - Otherwise: compare SHA-256, constant-time via ``hmac.compare_digest``.
    """
    if security_cfg is None:
        return False
    expected = getattr(security_cfg, "admin_password_hash", "")
    if not expected or not provided:
        return False
    computed = hashlib.sha256(provided.encode("utf-8")).hexdigest()
    return hmac.compare_digest(computed, expected)


# ----------------------------------------------------------------------
# Request-Bodies
# ----------------------------------------------------------------------

class SkillUpdateBody(BaseModel):
    """Body for ``PUT /api/skills/{name}``.

    ``content`` is the complete SKILL.md (frontmatter + Markdown). The
    server re-parses the file in place so the registry picks up the state
    change via hot-reload.
    """
    content: str
    admin_password: str | None = Field(default=None)


class SkillCreateBody(BaseModel):
    """Body for ``POST /api/skills`` (a new user skill from the desktop app).

    The fields map 1:1 onto the form in ``SkillCreateDialog`` — optional
    fields are mapped to defaults by the authoring service (``risk_policy``
    defaults to ``{default_tier: "ask"}``, ``body`` to a minimal Markdown
    scaffold).
    """
    name: str = Field(min_length=3, max_length=64)
    description: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    risk_policy: dict[str, Any] | None = None
    body: str = ""
    homepage_url: str | None = None
    source_url: str | None = None
    docs_url: str | None = None
    author: str = ""


class SkillCreatorDraftBody(BaseModel):
    """Body for ``POST /api/skills/creator/draft``.

    ``intent`` is the actual user description. The remaining fields are
    optional UI hints so the creator doesn't have to guess everything.
    """
    intent: str = Field(min_length=3, max_length=4000)
    name_hint: str = Field(default="", max_length=100)
    category: str = Field(default="general", max_length=80)
    trigger_hint: str = Field(default="", max_length=500)
    extra_context: str = Field(default="", max_length=4000)


class SkillCreatorRefineBody(SkillCreatorDraftBody):
    """Revision of an existing AI draft with user feedback."""
    draft: dict[str, Any] = Field(default_factory=dict)
    feedback: str = Field(default="", max_length=4000)


class SkillCreatorValidateBody(BaseModel):
    draft: dict[str, Any] = Field(default_factory=dict)
    skill_md: str | None = None


class SkillCreatorCommitBody(BaseModel):
    draft: dict[str, Any] = Field(default_factory=dict)


class SkillImportBody(BaseModel):
    input: str = Field(min_length=5, max_length=4000)


class SkillImportLocalBody(BaseModel):
    """Body for ``POST /api/skills/import-local`` — a folder on this machine.

    ``path`` points at a skill folder (or its ``SKILL.md`` directly). The
    folder is copied into the user skills directory including bundle
    resources, which is exactly the documented manual install path — just
    automated.
    """

    path: str = Field(min_length=1, max_length=4096)


class SkillOrderBody(BaseModel):
    """Body for ``PUT /api/skills/order`` — the user-defined list order.

    ``order`` is a list of skill names in display order. It affects ONLY the
    list view — triggering + brain injection ignore the order.
    """
    order: list[str] = Field(default_factory=list)


class SkillBulkDeleteBody(BaseModel):
    """Body for ``POST /api/skills/bulk-delete`` — delete several user skills.

    ``names`` is the list of skill names the user selected. Each is deleted
    independently; the response reports which ones went through and which were
    refused (built-ins, unknown names, IO errors).
    """
    names: list[str] = Field(default_factory=list)


class SkillQueryBody(BaseModel):
    """Body for ``POST /api/skills/query`` — local skill search with BM25 + LLM."""
    q: str = Field(default="", max_length=500)
    category: str | None = None
    state: str | None = None              # "active" | "validated" | "draft" | "disabled"
    risk: str | None = None               # max_risk
    is_builtin: bool | None = None
    tags: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

@router.get("")
async def list_skills(request: Request) -> dict[str, Any]:
    from jarvis.skills import prefs

    reg = _require_registry(request)
    skills: list[Skill] = _sort_by_order(reg.list(), prefs.load_order())
    return {
        "skills": [_skill_to_summary(s) for s in skills],
        "total": len(skills),
    }


@router.post("")
async def create_skill(body: SkillCreateBody, request: Request) -> dict[str, Any]:
    """Creates a new user skill and returns the full detail representation.

    Collisions (name == built-in or name == an existing skill) are rejected
    with 409. A slug violation or invalid frontmatter → 400.
    """
    from jarvis.skills.authoring import (
        SkillAuthoringError,
        SkillAuthoringService,
        SkillCreateRequest,
    )

    reg = _require_registry(request)
    bus = getattr(request.app.state, "bus", None)

    service = SkillAuthoringService(registry=reg, bus=bus)
    req = SkillCreateRequest(
        name=body.name,
        description=body.description,
        category=body.category,
        tags=tuple(body.tags),
        triggers=tuple(body.triggers),
        risk_policy=body.risk_policy,
        body=body.body,
        homepage_url=body.homepage_url,
        source_url=body.source_url,
        docs_url=body.docs_url,
        author=body.author,
    )
    try:
        created = await service.create(req)
    except SkillAuthoringError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    return _skill_to_detail(created)


@router.post("/creator/draft")
async def create_skill_draft(
    body: SkillCreatorDraftBody,
    request: Request,
) -> dict[str, Any]:
    """Generates an AI draft without writing any files."""
    _require_optional_module("jarvis.skills.creator_service", "Skill Creator")
    from jarvis.skills.creator_service import SkillCreatorInput, SkillCreatorService

    reg = _require_registry(request)
    brain = getattr(request.app.state, "brain", None)
    bus = getattr(request.app.state, "bus", None)
    config = getattr(request.app.state, "config", None)
    service = SkillCreatorService(brain=brain, registry=reg, bus=bus, config=config)
    result = await service.draft(
        SkillCreatorInput(
            intent=body.intent,
            name_hint=body.name_hint,
            category=body.category,
            trigger_hint=body.trigger_hint,
            extra_context=body.extra_context,
        )
    )
    return {
        "draft": result.draft,
        "skill_md": result.skill_md,
        "validation": result.validation,
        "brain_used": result.brain_used,
    }


@router.post("/creator/refine")
async def refine_skill_draft(
    body: SkillCreatorRefineBody,
    request: Request,
) -> dict[str, Any]:
    """Revises an AI draft based on feedback / follow-up questions."""
    _require_optional_module("jarvis.skills.creator_service", "Skill Creator")
    from jarvis.skills.creator_service import SkillCreatorInput, SkillCreatorService

    reg = _require_registry(request)
    brain = getattr(request.app.state, "brain", None)
    bus = getattr(request.app.state, "bus", None)
    config = getattr(request.app.state, "config", None)
    service = SkillCreatorService(brain=brain, registry=reg, bus=bus, config=config)
    result = await service.refine(
        SkillCreatorInput(
            intent=body.intent,
            name_hint=body.name_hint,
            category=body.category,
            trigger_hint=body.trigger_hint,
            extra_context=body.extra_context,
            existing_draft=body.draft,
            feedback=body.feedback,
        )
    )
    return {
        "draft": result.draft,
        "skill_md": result.skill_md,
        "validation": result.validation,
        "brain_used": result.brain_used,
    }


@router.post("/creator/validate")
async def validate_skill_draft(
    body: SkillCreatorValidateBody,
    request: Request,
) -> dict[str, Any]:
    """Validates a draft or SKILL.md text without persisting it."""
    _require_optional_module("jarvis.skills.creator_service", "Skill Creator")
    from jarvis.skills.creator_service import render_skill_md, validate_skill_md

    content = body.skill_md if body.skill_md is not None else render_skill_md(body.draft)
    validation, frontmatter = validate_skill_md(content)
    return {
        "skill_md": content,
        "validation": validation,
        "frontmatter": frontmatter,
    }


@router.post("/creator/commit")
async def commit_skill_draft(
    body: SkillCreatorCommitBody,
    request: Request,
) -> dict[str, Any]:
    """Persists the confirmed AI draft as a user skill."""
    _require_optional_module("jarvis.skills.creator_service", "Skill Creator")
    from jarvis.skills.authoring import SkillAuthoringError
    from jarvis.skills.creator_service import SkillCreatorService

    reg = _require_registry(request)
    brain = getattr(request.app.state, "brain", None)
    bus = getattr(request.app.state, "bus", None)
    config = getattr(request.app.state, "config", None)
    service = SkillCreatorService(brain=brain, registry=reg, bus=bus, config=config)
    try:
        created = await service.commit(body.draft)
    except SkillAuthoringError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _skill_to_detail(created)


_URL_RE = re.compile(r"https?://[^\s\"'<>]+")

#: Charset an imported skill's frontmatter ``name`` must satisfy before it is
#: used as an install folder name. ``SkillFrontmatter.name`` itself only
#: requires non-empty, so without this gate a crafted ``name: ../../evil``
#: (or an absolute path — ``Path(base) / "C:/..."`` discards the base) turns
#: the import routes into an arbitrary-file-write primitive. Mirrors the slug
#: rule in ``jarvis/skills/authoring/schema.py``.
_IMPORT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _safe_import_target(name: str) -> Path:
    """The install folder for an imported skill — guaranteed inside the root.

    Raises ``HTTPException(400)`` on a name that is not a plain slug or that
    resolves outside the user skills directory (path-traversal fail-closed).
    """
    if not _IMPORT_NAME_RE.match(name or ""):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Skill name {name!r} is not a valid slug "
                "(letters, digits, '-', '_'; max 64 chars)."
            ),
        )
    base = user_skills_dir().resolve()
    target = (base / name).resolve()
    if target.parent != base:
        raise HTTPException(
            status_code=400,
            detail=f"Skill name {name!r} resolves outside the skills directory.",
        )
    return target


def _extract_import_url(value: str) -> str:
    match = _URL_RE.search(value.strip())
    if not match:
        raise HTTPException(
            status_code=400,
            detail="No http(s) link found. Paste a SKILL.md link or a CLI command containing a link.",
        )
    url = match.group(0).rstrip(").,;")
    github_blob = re.match(
        r"https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)$",
        url,
    )
    if github_blob:
        owner, repo, branch, path = github_blob.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    return url


@router.post("/import")
async def import_skill(body: SkillImportBody, request: Request) -> dict[str, Any]:
    """Imports a skill from a link or a pasted CLI command.

    The endpoint deliberately does not accept an arbitrary shell command. Only
    the first http(s) link is extracted from the text and loaded as SKILL.md.
    """
    import tempfile

    import httpx

    reg = _require_registry(request)
    raw_url = _extract_import_url(body.input)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0),
    ) as client:
        try:
            resp = await client.get(raw_url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Download failed: {exc}",
            ) from exc

    content = resp.text
    if "---" not in content[:200]:
        raise HTTPException(
            status_code=400,
            detail="This link does not look like a SKILL.md with YAML frontmatter.",
        )

    with tempfile.TemporaryDirectory(prefix="jarvis-skill-import-") as tmp:
        tmp_path = Path(tmp) / "skill" / "SKILL.md"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(content, encoding="utf-8")
        parsed = parse_skill(tmp_path)

    if parsed.frontmatter is None:
        raise HTTPException(
            status_code=400,
            detail=f"SKILL.md could not be read: {parsed.error}",
        )

    name = parsed.name
    if name in BUILTIN_SKILL_NAMES:
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' is a built-in skill name and cannot be imported.",
        )
    try:
        reg.get(name)
    except KeyError:
        pass
    else:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{name}' already exists.",
        )

    # AP-15: an imported skill is third-party content the user has not reviewed
    # yet. `parse_skill` treats a missing `state:` key as VALIDATED, so writing
    # the downloaded file verbatim would put a remote skill straight into the
    # active pool. Stamp DRAFT on the way in — promotion stays an explicit,
    # human act via POST /api/skills/{name}/enable.
    from jarvis.skills.registry import _rewrite_state_in_frontmatter

    content = _rewrite_state_in_frontmatter(content, SkillLifecycleState.DRAFT.value)

    target_dir = _safe_import_target(name)
    target_file = target_dir / "SKILL.md"
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = target_file.with_suffix(".md.tmp")
    try:
        tmp_file.write_text(content, encoding="utf-8")
        tmp_file.replace(target_file)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not write skill: {exc}",
        ) from exc

    installed = parse_skill(target_file)
    reg._skills[name] = installed  # type: ignore[attr-defined]
    return _skill_to_detail(installed)


def _import_skill_folder(path_str: str, reg: Any) -> tuple[str, list[str]]:
    """Sync body of the local import: validate, lint, copy. Raises HTTPException.

    Runs in a worker thread (``asyncio.to_thread``) — every step here is
    blocking filesystem work.
    """
    import shutil

    src = Path(path_str).expanduser()
    try:
        src = src.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Unreadable path: {exc}") from exc
    folder = src.parent if src.name == "SKILL.md" else src
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"No SKILL.md found at '{folder}'.",
        )
    try:
        base = user_skills_dir().resolve()
        if folder == base or folder.is_relative_to(base):
            raise HTTPException(
                status_code=400,
                detail="This folder is already inside the Jarvis skills directory.",
            )
    except OSError:  # base dir unresolvable — skip this convenience check, not fatal
        pass

    parsed = parse_skill(skill_md)
    if parsed.frontmatter is None:
        raise HTTPException(
            status_code=400,
            detail=f"SKILL.md could not be read: {parsed.error}",
        )

    name = parsed.name
    if name in BUILTIN_SKILL_NAMES:
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' is a built-in skill name and cannot be imported.",
        )
    try:
        reg.get(name)
    except KeyError:  # not installed yet — exactly what a fresh import needs
        pass
    else:
        raise HTTPException(status_code=409, detail=f"Skill '{name}' already exists.")

    content = skill_md.read_text(encoding="utf-8-sig")
    lint_findings: list[str] = []
    try:
        from jarvis.skills.authoring.draft_writer import safe_lint_skill_body

        lint_findings = list(safe_lint_skill_body(parsed.body))
    except Exception:  # noqa: BLE001 — a missing lint module must not block import
        lint_findings = []
    if lint_findings:
        from jarvis.skills.registry import _rewrite_state_in_frontmatter

        content = _rewrite_state_in_frontmatter(
            content, SkillLifecycleState.DRAFT.value
        )

    target_dir = _safe_import_target(name)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "SKILL.md").write_text(content, encoding="utf-8")
        for kind in RESOURCE_KINDS:
            src_kind = folder / kind
            if src_kind.is_dir():
                shutil.copytree(src_kind, target_dir / kind, dirs_exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not copy skill: {exc}"
        ) from exc
    return name, lint_findings


@router.post("/import-local")
async def import_skill_from_path(
    body: SkillImportLocalBody, request: Request
) -> dict[str, Any]:
    """Imports a skill folder from a local path into the user skills dir.

    This is the bridge for skills the user authored OUTSIDE the Jarvis skill
    root — most commonly a coding agent's skill folder (Claude Code / Codex
    style ``.claude/skills/<name>/``), which Jarvis never scans. Live
    forensic 2026-08-12: every skill the maintainer "built" lived there, so
    the brain literally could not know them.

    Trust model, deliberately different from ``POST /import`` (URL): a local
    path names a file already on this machine that the user chose explicitly
    — the exact act the product docs bless as the manual install ("copy the
    folder"). So the parsed state is honored (missing ``state:`` loads as
    VALIDATED, the loader's normal rule) — EXCEPT when the safety lint that
    guards draft promotion finds disallowed calls in the body; then the copy
    is stamped DRAFT and the findings are returned, so review stays a human
    act exactly where it has teeth. Bundle folders (references/, scripts/,
    assets/, agents/) are copied along.
    """
    reg = _require_registry(request)
    name, lint_findings = await asyncio.to_thread(
        _import_skill_folder, body.path, reg
    )

    # A real reload (not a dict insert): bumps the registry generation so the
    # relevance match index rebuilds and paired capabilities sync — otherwise
    # the imported skill is listed but invisible to the paraphrase channel.
    try:
        await reg.reload()
    except Exception:  # noqa: BLE001 — fall back to the minimal insert
        installed = parse_skill(user_skills_dir() / name / "SKILL.md")
        reg._skills[name] = installed  # type: ignore[attr-defined]

    detail = _skill_to_detail(reg.get(name))
    detail["lint_findings"] = lint_findings
    return detail


# NB: registered BEFORE ``/{name}`` so a ``PUT /order`` is not captured by the
# ``PUT /{name}`` path-param route (which would treat "order" as a skill name).
@router.put("/order")
async def reorder_skills(body: SkillOrderBody, request: Request) -> dict[str, Any]:
    """Persist the user's custom skill order (list view only)."""
    from jarvis.skills import prefs

    _require_registry(request)  # 503 if the registry is absent, for consistency
    prefs.set_order(body.order)
    return {"ok": True, "order": prefs.load_order()}


# ----------------------------------------------------------------------
# Match diagnostics — "why did (or didn't) my skill fire?"
# ----------------------------------------------------------------------


class MatchTestRequest(BaseModel):
    """Body for ``POST /api/skills/match-test``.

    POST rather than GET on purpose: an utterance is free text with umlauts,
    slashes and newlines, and it has no business in a URL or an access log.
    """

    utterance: str = Field(..., min_length=1, max_length=2000)
    lang: str = "auto"
    include_disabled: bool = False


@router.post("/match-test", openapi_extra={"x-jarvis-readonly": True})
async def match_test(request: Request, body: MatchTestRequest) -> dict[str, Any]:
    """Dry-run the skill matcher against an utterance and explain the verdict.

    This is the affordance whose absence made the whole problem invisible: the
    Skills view could list, enable, edit, reorder and link-check skills, but
    there was no way to ask "would this sentence reach my skill?" short of
    talking to the assistant and hoping.

    Calls the SAME functions the brain calls — ``evaluate_match`` for the
    decision, ``evaluate_guards`` for the vetoes, ``autofire_policy`` for
    capture rights. A panel that re-implemented any of that would eventually
    disagree with the brain, and a debugger that lies is worse than none.

    Executes NOTHING. No ``render_instructions`` (that evaluates Jinja — a dry
    run which evaluates templates is not dry), no ``SkillInvoked``, no bus
    publish. It appends to the decision ring flagged ``dry_run`` so the panel
    shows it while the durable trail stays honest.
    """
    registry = _require_registry(request)

    from jarvis.skills import match_log
    from jarvis.skills.autofire_policy import classify, may_capture
    from jarvis.skills.guards import GUARD_ORDER, evaluate_guards
    from jarvis.skills.match_eval import BAND_NONE, evaluate_match
    from jarvis.skills.prefs import load_autofire_prefs
    from jarvis.skills.relevance import get_index

    config = getattr(request.app.state, "config", None)
    skills_cfg = getattr(config, "skills", None)
    min_band = str(getattr(skills_cfg, "auto_fire_min_band", "fire"))
    # Fallback mirrors the SkillsConfig default (False since 2026-08-12).
    shadow = bool(getattr(skills_cfg, "relevance_shadow", False))
    enabled = bool(getattr(skills_cfg, "relevance_enabled", True))

    decision = evaluate_match(
        registry,
        body.utterance,
        lang=body.lang,
        limit=8,
        use_relevance=enabled,
        fire_threshold=getattr(skills_cfg, "fire_threshold", None),
        hint_threshold=getattr(skills_cfg, "hint_threshold", None),
    )
    overrides = load_autofire_prefs()

    def _describe(name: str, band: str, evidence: str) -> dict[str, Any]:
        try:
            skill = registry.get(name)
        except Exception:  # noqa: BLE001
            return {
                "skill_name": name,
                "state": "unknown",
                "autofire_class": "",
                "would_fire": False,
                "vetoed_by": "unknown_skill",
            }
        ladder = evaluate_guards(
            skill, user_text=body.utterance, evidence=evidence
        )
        allowed, capture_veto = may_capture(
            skill, band, override=overrides.get(name), min_band=min_band
        )
        veto = ladder.vetoed_by or capture_veto
        state = getattr(skill.state, "value", skill.state)
        return {
            "skill_name": name,
            "state": str(state),
            "autofire_class": classify(skill),
            "auto_fire": overrides.get(name, "auto"),
            "would_fire": bool(allowed and ladder.passed and band != BAND_NONE),
            "vetoed_by": veto or None,
        }

    candidates: list[dict[str, Any]] = []
    for candidate in decision.candidates:
        entry = _describe(candidate.skill_name, candidate.band, candidate.evidence)
        entry.update(
            {
                "score": round(float(candidate.score), 4),
                "band": candidate.band,
                "source": candidate.source,
                "reason": candidate.reason,
                "matched_terms": [
                    term for term, _ in (candidate.signals or ())
                ],
                "signals": {name: value for name, value in (candidate.signals or ())},
            }
        )
        candidates.append(entry)

    # The full ladder for the winner, every guard with its verdict — not just
    # the one that fired. When the answer is "nothing matched", knowing WHICH of
    # the checks ate it is the entire point.
    guards_evaluated: list[dict[str, Any]] = []
    winner = decision.top.skill_name if decision.top is not None else ""
    if winner:
        try:
            ladder = evaluate_guards(
                registry.get(winner),
                user_text=body.utterance,
                evidence=decision.top.evidence if decision.top else "",
            )
            guards_evaluated = [
                {"guard": r.guard, "verdict": r.verdict, "detail": r.detail or None}
                for r in ladder.results
            ]
        except Exception:  # noqa: BLE001
            guards_evaluated = []
    else:
        guards_evaluated = [
            {"guard": name, "verdict": "skipped", "detail": None}
            for name in GUARD_ORDER
        ]

    top_entry = candidates[0] if candidates else None
    would_fire = bool(top_entry and top_entry["would_fire"] and not shadow)

    index = get_index(registry, include_inactive=body.include_disabled)
    match_log.record(
        utterance=body.utterance,
        decision=decision,
        lang=body.lang,
        vetoed_by=(top_entry or {}).get("vetoed_by") or "",
        autofire_class=(top_entry or {}).get("autofire_class") or "",
        fired=False,
        dry_run=True,
    )

    return {
        "utterance": body.utterance,
        "lang": body.lang,
        "elapsed_us": decision.elapsed_us,
        "winner": winner or None,
        "band": decision.band,
        "source": decision.source,
        "margin": round(float(decision.margin), 4),
        "would_fire": would_fire,
        "shadow_mode": shadow,
        "relevance_enabled": enabled,
        "autofire_class": (top_entry or {}).get("autofire_class") or None,
        "vetoed_by": (top_entry or {}).get("vetoed_by"),
        "candidates": candidates,
        "guards_evaluated": guards_evaluated,
        "thresholds": {
            "fire": round(index.fire_threshold, 4),
            "hint": round(index.hint_threshold, 4),
            "min_band": min_band,
        },
        "corpus": {
            "skills_indexed": index.size,
            "distinct_terms": len(index.postings),
        },
    }


@router.get("/lint", openapi_extra={"x-jarvis-readonly": True})
async def lint_skills(request: Request) -> dict[str, Any]:
    """Report which installed skills can never be FOUND.

    Different question from ``validate``: that one asks whether a SKILL.md
    parses and is safe, this one asks whether a skill carries enough distinctive
    vocabulary to be reachable at all. A skill can be perfectly valid, enabled,
    and still invisible to every matching channel — which is how a skill with an
    empty description and a one-heading body sat installed and unnoticed.

    Read-only and advisory. Deliberately not enforced by the loader: a content
    rule applied at load time suppresses the skills nobody anticipated along
    with the bad ones, silently, at boot, with no signal (AP-27). Enforce at the
    write boundary, report here.
    """
    registry = _require_registry(request)

    from jarvis.skills.quality import lint_registry

    try:
        skills = list(registry.list())
    except Exception:  # noqa: BLE001
        skills = []
    reports = [report for report in lint_registry(skills) if report.findings]
    return {
        "total_skills": len(skills),
        "with_findings": len(reports),
        "unreachable": sum(1 for report in reports if report.errors),
        "reports": [report.as_dict() for report in reports],
    }


@router.get("/match-log", openapi_extra={"x-jarvis-readonly": True})
async def match_log_recent(
    request: Request,
    limit: int = 50,
    skill: str = "",
    fired: bool | None = None,
) -> dict[str, Any]:
    """Recent skill-match decisions, newest first.

    Reads the process-local ring — no disk, no query. The ``vetoed_by`` column
    is the feature: it turns "I said something and nothing happened" into "the
    definitional guard suppressed plugin-github", which is the difference
    between a guess and a diagnosis.
    """
    from jarvis.skills import match_log

    entries = match_log.recent(limit=limit, skill=skill, fired=fired)
    return {
        "total": match_log.size(),
        "capacity": match_log.MAX_ENTRIES,
        "entries": [
            {
                "utterance_preview": entry.utterance_preview,
                "utterance_hash": entry.utterance_hash,
                "lang": entry.lang,
                "source": entry.source,
                "band": entry.band,
                "winner": entry.winner or None,
                "autofire_class": entry.autofire_class or None,
                "vetoed_by": entry.vetoed_by or None,
                "fired": entry.fired,
                "shadow": entry.shadow,
                "dry_run": entry.dry_run,
                "elapsed_us": entry.elapsed_us,
                "candidates": [
                    {
                        "skill_name": c.skill_name,
                        "score": round(float(c.score), 4),
                        "band": c.band,
                        "reason": c.reason,
                    }
                    for c in entry.candidates
                ],
            }
            for entry in entries
        ],
    }


@router.get("/{name}")
async def get_skill(name: str, request: Request) -> dict[str, Any]:
    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return _skill_to_detail(skill)


def _delete_user_skill(reg: Any, name: str) -> None:
    """Delete one user skill folder and prune its prefs.

    Raises ``HTTPException`` on every refusal (unknown name → 404, built-in →
    409, path escape / invalid target → 400, IO error → 500) so the single-skill
    ``DELETE`` route and the bulk endpoint share the exact same safety checks.
    Built-ins are refused because they would be re-copied on the next start.
    Only a directory strictly INSIDE the registry root is ever removed.
    """
    import shutil

    from jarvis.skills import prefs

    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    if _is_builtin(name):
        raise HTTPException(
            status_code=409,
            detail="Built-in skill can't be deleted (it is re-copied on next start).",
        )

    root = reg.root.resolve()
    target = skill.root.resolve()
    if target == root:
        raise HTTPException(status_code=400, detail="Invalid delete target.")
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Skill lives outside the user skill folder — delete refused.",
        )

    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not delete skill: {exc}"
        ) from exc

    reg._skills.pop(name, None)  # type: ignore[attr-defined]
    prefs.remove_skill(name)


# NB: registered BEFORE ``/{name}`` so a ``POST /bulk-delete`` is never captured
# by a path-param route that would treat "bulk-delete" as a skill name.
@router.post("/bulk-delete")
async def bulk_delete_skills(
    body: SkillBulkDeleteBody, request: Request
) -> dict[str, Any]:
    """Delete several user skills in one batch.

    Each name is deleted independently: built-ins, unknown names, and IO errors
    are recorded in ``failed`` while every deletable skill still goes through, so
    one bad entry never blocks the rest of the selection. Repeated names are
    de-duped so a doubled selection is not reported as a phantom failure.
    """
    reg = _require_registry(request)
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    seen: set[str] = set()
    for name in body.names:
        if name in seen:
            continue
        seen.add(name)
        try:
            _delete_user_skill(reg, name)
        except HTTPException as exc:
            failed.append({"name": name, "detail": str(exc.detail)})
        else:
            deleted.append(name)
    return {"deleted": deleted, "failed": failed}


@router.delete("/{name}")
async def delete_skill(name: str, request: Request) -> dict[str, Any]:
    """Delete a user skill (folder) and prune its prefs.

    Built-ins are refused (409) — they would be re-copied on the next start.
    Safety lives in ``_delete_user_skill`` (path-escape guard + built-in guard).
    """
    reg = _require_registry(request)
    _delete_user_skill(reg, name)
    return {"ok": True, "removed": True, "name": name}


@router.put("/{name}")
async def update_skill(
    name: str,
    body: SkillUpdateBody,
    request: Request,
) -> dict[str, Any]:
    """Rewrites the SKILL.md. For built-in skills the admin password is checked."""
    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    if _is_builtin(name):
        if not _check_admin_pass(body.admin_password, _security_cfg(request)):
            raise HTTPException(
                status_code=403,
                detail="A built-in skill can only be edited with a valid admin password.",
            )

    # Write atomically: temp file + rename, so the watcher never reads a
    # half-written intermediate state.
    target: Path = skill.path
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(body.content, encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not write skill: {exc}"
        ) from exc

    # Re-parse immediately + replace in the registry, so the response shows the
    # new state (the watchdog hot-reload does the same afterward, but async).
    updated = parse_skill(target)
    reg._skills[name] = updated  # type: ignore[attr-defined]
    return _skill_to_detail(updated)


@router.post("/{name}/enable")
async def enable_skill(name: str, request: Request) -> dict[str, Any]:
    return _flip_state(request, name, SkillLifecycleState.ACTIVE)


@router.post("/{name}/disable")
async def disable_skill(name: str, request: Request) -> dict[str, Any]:
    return _flip_state(request, name, SkillLifecycleState.DISABLED)


@router.post("/reload")
async def reload_registry(request: Request) -> dict[str, Any]:
    reg = _require_registry(request)
    await reg.reload()
    return {"ok": True, "total": len(reg.list())}


@router.get("/{name}/link-health")
async def get_skill_link_health(name: str, request: Request) -> dict[str, Any]:
    """Checks a skill's URLs (homepage/source/docs).

    Stale-while-revalidate: if a cache entry exists (even if stale), it is
    returned immediately — a refresh runs in the background at the same time.
    This ensures the UI never waits on HEAD requests.
    """
    _require_optional_module("jarvis.skills.link_health", "Skill link-health check")
    from jarvis.skills.link_health import LinkHealthChecker

    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    if skill.frontmatter is None:
        return {"fields": {}, "skill": name}

    fm = skill.frontmatter
    fields = {
        "homepage_url": fm.homepage_url,
        "source_url": fm.source_url,
        "docs_url": fm.docs_url,
    }

    # Cache the checker per app state, so the SQLite connection is reused
    checker = getattr(request.app.state, "_link_health_checker", None)
    if checker is None:
        checker = LinkHealthChecker()
        request.app.state._link_health_checker = checker

    out: dict[str, dict[str, Any] | None] = {}
    stale_urls: list[str] = []
    for field_name, url in fields.items():
        if not url:
            out[field_name] = None
            continue
        cached = checker.read_cached(url)
        if cached is None:
            # Cache miss — synchronous check (one-off, fast)
            status = await checker.check_url(url)
            out[field_name] = status.to_dict()
        else:
            out[field_name] = cached.to_dict()
            if not cached.fresh:
                stale_urls.append(url)

    # Refresh stale entries in the background — the current response contains
    # the old value with fresh=False; the next call will see the new one.
    if stale_urls:
        asyncio.create_task(checker.check_all(stale_urls, force=True))

    return {"skill": name, "fields": out}


@router.get("/{name}/resources/{kind}/{filename:path}")
async def get_skill_resource(
    name: str, kind: str, filename: str, request: Request
) -> PlainTextResponse:
    """Returns the content of a bundled resource file (text-only, UTF-8).

    Binary files (icons, audio) are currently not supported — the UI can show
    them in the list but not render them. Displaying images would be a later
    extension via a separate media endpoint.
    """
    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    target = _resolve_resource_path(skill, kind, filename)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=415,
            detail=f"File '{kind}/{filename}' is not UTF-8 text (binary?)",
        )
    return PlainTextResponse(content=text, media_type="text/plain; charset=utf-8")


def _flip_state(
    request: Request, name: str, new_state: SkillLifecycleState
) -> dict[str, Any]:
    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    # DRAFT stays DRAFT — a broken skill can't be activated.
    if skill.state == SkillLifecycleState.DRAFT:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{name}' is in DRAFT state (error: {skill.error}) — "
                   "fix it first.",
        )

    updated = replace(skill, state=new_state)
    reg._skills[name] = updated  # type: ignore[attr-defined]

    # Persist the choice to the sidecar so it survives a reload/restart — the
    # in-memory flip above is wiped by every hot-reload (that was the old bug).
    from jarvis.skills import prefs

    prefs.set_state(name, new_state == SkillLifecycleState.ACTIVE)
    return _skill_to_summary(updated)


# ----------------------------------------------------------------------
# Skill finder (catalog search + install)
# ----------------------------------------------------------------------

class SkillSearchBody(BaseModel):
    """Body for ``POST /api/skills/catalog/search``.

    The fields map 1:1 onto the dropdown menu in the SkillFinder dialog.
    All fields except ``query`` are optional — without a filter it matches
    against the full catalog.
    """
    query: str = Field(default="", max_length=500)
    trust: str = Field(default="any")  # "any" | "official" | "verified" | "community" | "experimental"
    min_stars: int | None = Field(default=None, ge=0)
    category: str | None = None
    language: str | None = None
    max_risk: str | None = None  # "safe" | "monitor" | "ask"
    limit: int = Field(default=10, ge=1, le=30)


class SkillInstallBody(BaseModel):
    """Body for ``POST /api/skills/catalog/install``.

    The client sends back the full candidate (not just the name), so the
    server doesn't have to search the catalog again and the user's intent
    stays stable even if the catalog is updated between the search and the
    install call.
    """
    name: str
    raw_url: str | None = None
    source_url: str = ""
    title: str = ""


@router.post("/query")
async def query_local_skills(
    body: SkillQueryBody, request: Request
) -> dict[str, Any]:
    """Local skill search: BM25 + optional LLM re-ranking.

    With an empty query the endpoint acts as a pure filter router for sidebar
    filters (category/state/risk/built-in toggle/tags). With a query, FTS5-BM25
    runs first against the in-memory skill index, then (given enough tokens and
    an available brain) an LLM rerank of the top 15.
    """
    from jarvis.skills.local_search import (
        LocalSearchFilters,
        LocalSkillSearch,
    )

    reg = _require_registry(request)
    brain = getattr(request.app.state, "brain", None)

    # Cache per app state: LocalSkillSearch keeps the FTS5 index in memory.
    # We attach the instance to app.state so it's reused across requests
    # (otherwise we'd have to rebuild the index on every request).
    searcher = getattr(request.app.state, "_local_skill_search", None)
    if searcher is None or searcher._registry is not reg:
        searcher = LocalSkillSearch(registry=reg, brain=brain)
        request.app.state._local_skill_search = searcher
    else:
        # The brain can change between requests (e.g. after a provider switch)
        searcher._brain = brain

    filters = LocalSearchFilters(
        q=body.q.strip(),
        category=body.category,
        state=body.state,
        risk=body.risk,
        is_builtin=body.is_builtin,
        tags=tuple(body.tags),
        limit=body.limit,
    )
    try:
        hits, brain_used = await searcher.query(filters)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Query failed: {exc}") from exc

    # Return the full summary for every hit, so the UI can render the same
    # objects as in the normal list (no separate sub-component for search
    # results).
    results: list[dict[str, Any]] = []
    for hit in hits:
        try:
            sk = reg.get(hit.name)
        except KeyError:
            continue
        summary = _skill_to_summary(sk)
        summary["score"] = round(hit.score, 3)
        summary["reason"] = hit.reason
        results.append(summary)

    return {
        "skills": results,
        "total": len(results),
        "brain_used": brain_used,
        "query": body.q,
    }


@router.post("/catalog/search")
async def search_catalog(
    body: SkillSearchBody, request: Request
) -> dict[str, Any]:
    """Semantically ranked search over the skill catalog.

    If ``app.state.brain`` is set, the finder uses the brain for ranking.
    Without a brain it falls back to heuristic token matching — so search
    still works in headless mode without credentials.
    """
    brain = getattr(request.app.state, "brain", None)
    finder = SkillFinder(brain=brain)

    # Warm the community index cache (TTL-gated, short timeouts) so
    # marketplace skills appear in the pool even when the Plugins view never
    # ran a fetch this session. Offline: get_index degrades to cache/empty and
    # the finder reads only the cache — search itself never blocks on this
    # beyond the bounded fetch.
    try:
        from jarvis.marketplace.community_source import get_index

        await get_index()
    except Exception:  # noqa: BLE001 - community feed is optional for search
        import logging

        logging.getLogger(__name__).warning(
            "community index warm-up failed", exc_info=True
        )

    # Type conversion: Pydantic doesn't allow a Literal union directly as a query param
    trust_val: Any = body.trust if body.trust in ("any", "official", "verified", "community", "experimental") else "any"

    filters = SearchFilters(
        query=body.query.strip(),
        trust=trust_val,
        min_stars=body.min_stars,
        category=body.category,
        language=body.language,
        max_risk=body.max_risk,
        limit=body.limit,
    )

    try:
        candidates = await finder.search(filters)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}") from exc

    return {
        "query": body.query,
        "count": len(candidates),
        "candidates": [c.to_dict() for c in candidates],
        "brain_used": brain is not None,
    }


@router.post("/catalog/install")
async def install_from_catalog(
    body: SkillInstallBody, request: Request
) -> dict[str, Any]:
    """Installs a skill from the catalog.

    Steps:
    1. Fetch the file via ``httpx`` from ``raw_url`` (or abort if None).
    2. Save it to ``<user_skills>/<name>/SKILL.md``.
    3. Re-parse the registry + hot-swap it in, so the UI sees the new
       skill immediately.
    """
    reg = _require_registry(request)

    # Collision check: does a skill with the same name already exist?
    try:
        existing = reg.get(body.name)
    except KeyError:
        existing = None
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{body.name}' already exists. Delete it before reinstalling.",
        )

    # Minimal SkillCandidate for install() — we only need raw_url + name
    from jarvis.skills.finder import SkillCandidate

    candidate = SkillCandidate(
        name=body.name,
        title=body.title or body.name,
        description="",
        source="catalog",
        source_url=body.source_url,
        raw_url=body.raw_url,
        trust="community",
        stars=None,
        categories=(),
        languages=(),
        risk="monitor",
        tags=(),
    )

    finder = SkillFinder()
    try:
        target_path = await finder.install(candidate)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Installation failed: {exc}"
        ) from exc

    # Registry refresh — the new skill should appear in the sidebar immediately
    try:
        await reg.reload()
    except Exception as exc:  # noqa: BLE001
        # A reload failure is not fatal — the watchdog will catch it async anyway
        return {
            "ok": True,
            "name": body.name,
            "path": str(target_path),
            "reload_warning": str(exc),
        }

    installed = reg.get(body.name)
    return {
        "ok": True,
        "name": body.name,
        "path": str(target_path),
        "skill": _skill_to_summary(installed),
    }


@router.get("/catalog/meta")
async def catalog_meta(request: Request) -> dict[str, Any]:
    """Meta info for the frontend: which categories, languages, and trust levels
    exist in the current catalog. Fills the dropdowns in the SkillFinder dialog
    dynamically, so they don't drift out of sync with the JSON.
    """
    from jarvis.skills.catalog import load_catalog

    entries = load_catalog()
    categories = sorted({c for e in entries for c in e.get("categories", [])})
    languages = sorted({l for e in entries for l in e.get("languages", [])})
    sources = sorted({e.get("source", "") for e in entries if e.get("source")})
    return {
        "total": len(entries),
        "categories": categories,
        "languages": languages,
        "sources": sources,
        "trust_levels": ["official", "verified", "community", "experimental"],
        "risk_levels": ["safe", "monitor", "ask"],
    }


__all__ = ["router"]
