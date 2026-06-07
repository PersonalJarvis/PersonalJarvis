"""REST-API fuer das Skill-System (Desktop-UI).

Endpoints:
- ``GET  /api/skills``                → Liste (ohne Body, schlank fuer Sidebar).
- ``GET  /api/skills/{name}``         → Voller Skill inkl. Markdown-Body.
- ``PUT  /api/skills/{name}``         → Body (und optional Frontmatter) updaten.
  Bei Builtin-Skills: ``admin_password`` im Request-Body noetig.
- ``POST /api/skills/{name}/enable``  → State -> ACTIVE.
- ``POST /api/skills/{name}/disable`` → State -> DISABLED.
- ``POST /api/skills/reload``         → Registry.reload() forcen.

Der Router erwartet eine ``SkillRegistry`` auf ``app.state.skill_registry`` — die
wird vom ``WebServer`` beim Startup gesetzt (nach ``ensure_user_skills_dir()``).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from fastapi.responses import PlainTextResponse

from jarvis.core.paths import user_skills_dir
from jarvis.skills.builtin import BUILTIN_SKILL_NAMES
from jarvis.skills.finder import SearchFilters, SkillFinder
from jarvis.skills.loader import parse_skill
from jarvis.skills.schema import RESOURCE_KINDS, Skill, SkillLifecycleState

router = APIRouter(prefix="/api/skills", tags=["skills"])


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------

def _require_registry(request: Request) -> Any:
    reg = getattr(request.app.state, "skill_registry", None)
    if reg is None:
        raise HTTPException(status_code=503, detail="SkillRegistry nicht verfuegbar")
    return reg


def _security_cfg(request: Request) -> Any:
    """Holt die SecurityConfig aus dem app-state. ``None`` wenn Cfg fehlt
    (z.B. in Tests mit Mock-App) — Admin-Checks fallen dann zurueck auf
    "kein Hash gesetzt = Builtin-Edits gesperrt"."""
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
    """Schlanke Repraesentation fuer ``GET /api/skills`` — ohne Body."""
    fm = s.frontmatter
    # resources als plain dict mit Listen (statt Tuples) fuer JSON-Serialisation
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
    """Volles Detail inkl. Body + Frontmatter-Dump."""
    out = _skill_to_summary(s)
    out["body"] = s.body
    out["body_hash"] = s.body_hash
    out["frontmatter"] = s.frontmatter.model_dump() if s.frontmatter else None
    try:
        rel = s.path.relative_to(user_skills_dir())
        out["path"] = str(rel).replace("\\", "/")
    except ValueError:
        # Skill liegt nicht unter user_skills_dir() (z.B. Test-Fixture)
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
    """Loest einen Resource-Pfad und stellt sicher, dass er nicht aus dem
    Skill-Root ausbricht (Path-Traversal-Schutz).

    Wirft HTTPException bei unbekanntem Kind, fehlendem Ordner, oder Path-Escape.
    """
    if kind not in RESOURCE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannter Resource-Kind '{kind}' (erwartet: {list(RESOURCE_KINDS)})",
        )
    kind_root = (skill.root / kind).resolve()
    if not kind_root.is_dir():
        raise HTTPException(
            status_code=404, detail=f"Ordner '{kind}/' existiert nicht"
        )
    target = (kind_root / filename).resolve()
    try:
        target.relative_to(kind_root)
    except ValueError:
        # Symlink oder `..`-Konstrukt, das ausserhalb des Kind-Roots zeigt
        raise HTTPException(
            status_code=400, detail="Pfad-Traversal ausserhalb des Resource-Ordners"
        )
    if not target.is_file():
        raise HTTPException(
            status_code=404, detail=f"Datei '{kind}/{filename}' nicht gefunden"
        )
    return target


# ----------------------------------------------------------------------
# Admin-Pass-Check
# ----------------------------------------------------------------------

def _check_admin_pass(provided: str | None, security_cfg: Any) -> bool:
    """Prueft ein Admin-Password gegen ``security.admin_password_hash``.

    - Kein Hash gesetzt (leerer String) -> immer False (Builtin-Edits gesperrt).
    - Kein Password provided -> False.
    - Sonst: SHA-256 vergleichen, constant-time via ``hmac.compare_digest``.
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
    """Body fuer ``PUT /api/skills/{name}``.

    ``content`` ist die vollstaendige SKILL.md (Frontmatter + Markdown). Der
    Server re-parsed das File in-place, damit die Registry die State-Change
    via Hot-Reload aufnimmt.
    """
    content: str
    admin_password: str | None = Field(default=None)


class SkillCreateBody(BaseModel):
    """Body fuer ``POST /api/skills`` (neuer User-Skill aus der Desktop-App).

    Die Felder mappen 1:1 auf die Form im ``SkillCreateDialog`` — Optional-
    Felder werden vom Authoring-Service auf Defaults gemappt (``risk_policy``
    defaultet auf ``{default_tier: "ask"}``, ``body`` auf ein minimales
    Markdown-Geruest).
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
    """Body fuer ``POST /api/skills/creator/draft``.

    ``intent`` ist die eigentliche Nutzerbeschreibung. Die restlichen Felder
    sind optionale UI-Hints, damit der Creator nicht alles erraten muss.
    """
    intent: str = Field(min_length=3, max_length=4000)
    name_hint: str = Field(default="", max_length=100)
    category: str = Field(default="general", max_length=80)
    trigger_hint: str = Field(default="", max_length=500)
    extra_context: str = Field(default="", max_length=4000)


class SkillCreatorRefineBody(SkillCreatorDraftBody):
    """Revision eines bestehenden AI-Drafts mit User-Feedback."""
    draft: dict[str, Any] = Field(default_factory=dict)
    feedback: str = Field(default="", max_length=4000)


class SkillCreatorValidateBody(BaseModel):
    draft: dict[str, Any] = Field(default_factory=dict)
    skill_md: str | None = None


class SkillCreatorCommitBody(BaseModel):
    draft: dict[str, Any] = Field(default_factory=dict)


class SkillImportBody(BaseModel):
    input: str = Field(min_length=5, max_length=4000)


class SkillOrderBody(BaseModel):
    """Body fuer ``PUT /api/skills/order`` — die User-definierte Listen-Reihenfolge.

    ``order`` ist eine Liste von Skill-Namen in Anzeige-Reihenfolge. Sie betrifft
    NUR die Listen-Ansicht — Auslösung + Brain-Einblendung ignorieren die
    Reihenfolge.
    """
    order: list[str] = Field(default_factory=list)


class SkillQueryBody(BaseModel):
    """Body fuer ``POST /api/skills/query`` — lokale Skill-Suche mit BM25 + LLM."""
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
    """Legt einen neuen User-Skill an und gibt die vollstaendige Detail-Repr zurueck.

    Kollisionen (Name == Builtin oder Name == existierender Skill) werden mit
    409 abgelehnt. Slug-Verstoss oder ungueltige Frontmatter → 400.
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
    """Erzeugt einen AI-Draft, ohne Dateien zu schreiben."""
    from jarvis.skills.creator_service import SkillCreatorInput, SkillCreatorService

    reg = _require_registry(request)
    brain = getattr(request.app.state, "brain", None)
    bus = getattr(request.app.state, "bus", None)
    service = SkillCreatorService(brain=brain, registry=reg, bus=bus)
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
    """Ueberarbeitet einen AI-Draft anhand von Feedback/Rueckfragen."""
    from jarvis.skills.creator_service import SkillCreatorInput, SkillCreatorService

    reg = _require_registry(request)
    brain = getattr(request.app.state, "brain", None)
    bus = getattr(request.app.state, "bus", None)
    service = SkillCreatorService(brain=brain, registry=reg, bus=bus)
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
    """Validiert einen Draft oder SKILL.md-Text, ohne zu persistieren."""
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
    """Persistiert den bestaetigten AI-Draft als User-Skill."""
    from jarvis.skills.authoring import SkillAuthoringError
    from jarvis.skills.creator_service import SkillCreatorService

    reg = _require_registry(request)
    brain = getattr(request.app.state, "brain", None)
    bus = getattr(request.app.state, "bus", None)
    service = SkillCreatorService(brain=brain, registry=reg, bus=bus)
    try:
        created = await service.commit(body.draft)
    except SkillAuthoringError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _skill_to_detail(created)


_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _extract_import_url(value: str) -> str:
    match = _URL_RE.search(value.strip())
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Kein http(s)-Link gefunden. Fuege einen SKILL.md-Link oder einen CLI-Befehl mit Link ein.",
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
    """Importiert einen Skill aus einem Link oder einem eingefuegten CLI-Befehl.

    Der Endpoint akzeptiert absichtlich keinen beliebigen Shell-Command. Aus dem
    Text wird nur der erste http(s)-Link extrahiert und als SKILL.md geladen.
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
                detail=f"Download fehlgeschlagen: {exc}",
            ) from exc

    content = resp.text
    if "---" not in content[:200]:
        raise HTTPException(
            status_code=400,
            detail="Der Link sieht nicht wie eine SKILL.md mit YAML-Frontmatter aus.",
        )

    with tempfile.TemporaryDirectory(prefix="jarvis-skill-import-") as tmp:
        tmp_path = Path(tmp) / "skill" / "SKILL.md"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(content, encoding="utf-8")
        parsed = parse_skill(tmp_path)

    if parsed.frontmatter is None:
        raise HTTPException(
            status_code=400,
            detail=f"SKILL.md konnte nicht gelesen werden: {parsed.error}",
        )

    name = parsed.name
    if name in BUILTIN_SKILL_NAMES:
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' ist ein Builtin-Skill-Name und kann nicht importiert werden.",
        )
    try:
        reg.get(name)
    except KeyError:
        pass
    else:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{name}' existiert bereits.",
        )

    target_dir = user_skills_dir() / name
    target_file = target_dir / "SKILL.md"
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = target_file.with_suffix(".md.tmp")
    try:
        tmp_file.write_text(content, encoding="utf-8")
        tmp_file.replace(target_file)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Konnte Skill nicht schreiben: {exc}",
        ) from exc

    installed = parse_skill(target_file)
    reg._skills[name] = installed  # type: ignore[attr-defined]
    return _skill_to_detail(installed)


# NB: registered BEFORE ``/{name}`` so a ``PUT /order`` is not captured by the
# ``PUT /{name}`` path-param route (which would treat "order" as a skill name).
@router.put("/order")
async def reorder_skills(body: SkillOrderBody, request: Request) -> dict[str, Any]:
    """Persist the user's custom skill order (list view only)."""
    from jarvis.skills import prefs

    _require_registry(request)  # 503 if the registry is absent, for consistency
    prefs.set_order(body.order)
    return {"ok": True, "order": prefs.load_order()}


@router.get("/{name}")
async def get_skill(name: str, request: Request) -> dict[str, Any]:
    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' nicht gefunden")
    return _skill_to_detail(skill)


@router.delete("/{name}")
async def delete_skill(name: str, request: Request) -> dict[str, Any]:
    """Loescht einen User-Skill (Ordner) und raeumt seine Prefs auf.

    Builtins werden abgelehnt (409) — sie wuerden beim naechsten Start ohnehin
    neu kopiert. Sicherheit: geloescht wird nur INNERHALB des Registry-Roots
    (kein Path-Escape).
    """
    import shutil

    from jarvis.skills import prefs

    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' nicht gefunden")

    if _is_builtin(name):
        raise HTTPException(
            status_code=409,
            detail="Builtin-Skill kann nicht geloescht werden (wird beim Start neu kopiert).",
        )

    root = reg.root.resolve()
    target = skill.root.resolve()
    if target == root:
        raise HTTPException(status_code=400, detail="Ungueltiges Loeschziel.")
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Skill liegt ausserhalb des User-Skill-Ordners — Loeschen verweigert.",
        )

    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Konnte Skill nicht loeschen: {exc}"
        ) from exc

    reg._skills.pop(name, None)  # type: ignore[attr-defined]
    prefs.remove_skill(name)
    return {"ok": True, "removed": True, "name": name}


@router.put("/{name}")
async def update_skill(
    name: str,
    body: SkillUpdateBody,
    request: Request,
) -> dict[str, Any]:
    """Schreibt die SKILL.md neu. Bei Builtin-Skills wird der Admin-Pass geprueft."""
    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' nicht gefunden")

    if _is_builtin(name):
        if not _check_admin_pass(body.admin_password, _security_cfg(request)):
            raise HTTPException(
                status_code=403,
                detail="Builtin-Skill darf nur mit gueltigem Admin-Password bearbeitet werden.",
            )

    # Atomar schreiben: temp-file + rename, damit der Watcher keinen halb
    # geschriebenen Zwischenstand liest.
    target: Path = skill.path
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(body.content, encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Konnte Skill nicht schreiben: {exc}"
        ) from exc

    # Sofort re-parsen + in Registry ersetzen, damit der Response den neuen State
    # zeigt (der watchdog-Hotreload macht danach dasselbe, aber async).
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
    """Prueft die URLs (homepage/source/docs) eines Skills.

    Stale-While-Revalidate: ist ein Cache-Eintrag vorhanden (auch wenn stale),
    wird er sofort zurueckgegeben — zugleich laeuft ein Refresh im Hintergrund.
    Das sorgt dafuer, dass die UI nie auf HEAD-Requests wartet.
    """
    from jarvis.skills.link_health import LinkHealthChecker

    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' nicht gefunden")

    if skill.frontmatter is None:
        return {"fields": {}, "skill": name}

    fm = skill.frontmatter
    fields = {
        "homepage_url": fm.homepage_url,
        "source_url": fm.source_url,
        "docs_url": fm.docs_url,
    }

    # Checker pro App-State cachen, damit die SQLite-Connection wiederverwendet wird
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
            # Cache-Miss — synchroner Check (einmalig, schnell)
            status = await checker.check_url(url)
            out[field_name] = status.to_dict()
        else:
            out[field_name] = cached.to_dict()
            if not cached.fresh:
                stale_urls.append(url)

    # Stale-Eintraege im Hintergrund refreshen — der aktuelle Response enthaelt
    # den alten Wert mit fresh=False, der naechste Call sieht den neuen.
    if stale_urls:
        asyncio.create_task(checker.check_all(stale_urls, force=True))

    return {"skill": name, "fields": out}


@router.get("/{name}/resources/{kind}/{filename:path}")
async def get_skill_resource(
    name: str, kind: str, filename: str, request: Request
) -> PlainTextResponse:
    """Liefert den Content eines Bundle-Resource-Files (Text-only, UTF-8).

    Binaer-Files (Icons, Audio) werden aktuell nicht unterstuetzt — die UI
    kann sie aus der Liste zeigen, aber nicht rendern. Anzeige fuer Bilder
    waere ein spaeterer Ausbau via separatem Media-Endpoint.
    """
    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' nicht gefunden")

    target = _resolve_resource_path(skill, kind, filename)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=415,
            detail=f"Datei '{kind}/{filename}' ist nicht UTF-8-Text (binaer?)",
        )
    return PlainTextResponse(content=text, media_type="text/plain; charset=utf-8")


def _flip_state(
    request: Request, name: str, new_state: SkillLifecycleState
) -> dict[str, Any]:
    reg = _require_registry(request)
    try:
        skill = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' nicht gefunden")

    # DRAFT bleibt DRAFT — man kann einen kaputten Skill nicht aktivieren.
    if skill.state == SkillLifecycleState.DRAFT:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{name}' ist im DRAFT-State (Fehler: {skill.error}) — "
                   "erst reparieren.",
        )

    updated = replace(skill, state=new_state)
    reg._skills[name] = updated  # type: ignore[attr-defined]

    # Persist the choice to the sidecar so it survives a reload/restart — the
    # in-memory flip above is wiped by every hot-reload (that was the old bug).
    from jarvis.skills import prefs

    prefs.set_state(name, new_state == SkillLifecycleState.ACTIVE)
    return _skill_to_summary(updated)


# ----------------------------------------------------------------------
# Skill-Finder (Catalog-Search + Install)
# ----------------------------------------------------------------------

class SkillSearchBody(BaseModel):
    """Body fuer ``POST /api/skills/catalog/search``.

    Die Felder mappen 1:1 auf das Dropdown-Menue im SkillFinder-Dialog.
    Alle bis auf ``query`` sind optional — ohne Filter matcht gegen den
    kompletten Katalog.
    """
    query: str = Field(default="", max_length=500)
    trust: str = Field(default="any")  # "any" | "official" | "verified" | "community" | "experimental"
    min_stars: int | None = Field(default=None, ge=0)
    category: str | None = None
    language: str | None = None
    max_risk: str | None = None  # "safe" | "monitor" | "ask"
    limit: int = Field(default=10, ge=1, le=30)


class SkillInstallBody(BaseModel):
    """Body fuer ``POST /api/skills/catalog/install``.

    Der Client schickt den vollstaendigen Kandidaten zurueck (und nicht nur
    den Namen), damit der Server nicht nochmal den Katalog durchsuchen muss
    und der User-Intent stabil bleibt, selbst wenn der Katalog zwischen Such-
    und Install-Call aktualisiert wird.
    """
    name: str
    raw_url: str | None = None
    source_url: str = ""
    title: str = ""


@router.post("/query")
async def query_local_skills(
    body: SkillQueryBody, request: Request
) -> dict[str, Any]:
    """Lokale Skill-Suche: BM25 + optionales LLM-Re-Ranking.

    Mit leerer Query laeuft der Endpoint als reiner Filter-Router fuer Sidebar-
    Filter (Kategorie/State/Risk/Builtin-Toggle/Tags). Mit Query wird zunaechst
    FTS5-BM25 gegen den in-memory Skill-Index gefahren, dann (bei ausreichend
    vielen Tokens + verfuegbarem Brain) ein LLM-Rerank der Top-15.
    """
    from jarvis.skills.local_search import (
        LocalSearchFilters,
        LocalSkillSearch,
    )

    reg = _require_registry(request)
    brain = getattr(request.app.state, "brain", None)

    # Cache pro App-State: LocalSkillSearch haelt den FTS5-Index in-memory.
    # Wir haengen die Instanz an app.state, damit sie zwischen Requests wieder-
    # verwendet wird (sonst muessten wir den Index pro Request neu bauen).
    searcher = getattr(request.app.state, "_local_skill_search", None)
    if searcher is None or searcher._registry is not reg:
        searcher = LocalSkillSearch(registry=reg, brain=brain)
        request.app.state._local_skill_search = searcher
    else:
        # Brain kann sich zwischen Requests aendern (z.B. nach Provider-Switch)
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
        raise HTTPException(status_code=500, detail=f"Query fehlgeschlagen: {exc}") from exc

    # Fuer jeden Hit das volle Summary zurueckgeben, damit die UI dieselben
    # Objekte wie in der normalen Liste rendern kann (keine separate
    # Sub-Komponente fuer Search-Ergebnisse).
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
    """Semantisch-gerankte Suche im Skill-Katalog.

    Wenn ``app.state.brain`` gesetzt ist, nutzt der Finder das Brain fuer
    Ranking. Ohne Brain fallback auf heuristisches Token-Matching — die Suche
    funktioniert also auch im Headless-Mode ohne Credentials.
    """
    brain = getattr(request.app.state, "brain", None)
    finder = SkillFinder(brain=brain)

    # Typ-Konversion: Pydantic erlaubt nicht Literal-Union direkt als Query-Param
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
        raise HTTPException(status_code=500, detail=f"Suche fehlgeschlagen: {exc}") from exc

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
    """Installiert einen Skill aus dem Katalog.

    Schritte:
    1. Datei per ``httpx`` aus ``raw_url`` holen (oder abbrechen wenn None).
    2. In ``<user_skills>/<name>/SKILL.md`` ablegen.
    3. Registry re-parsen + hot-swap einfuegen, damit die UI sofort den
       neuen Skill sieht.
    """
    reg = _require_registry(request)

    # Kollisions-Check: existierender Skill mit gleichem Namen?
    try:
        existing = reg.get(body.name)
    except KeyError:
        existing = None
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{body.name}' existiert bereits. Loesche ihn, bevor du neu installierst.",
        )

    # Minimaler SkillCandidate fuer install() — wir brauchen raw_url + name
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
            status_code=500, detail=f"Installation fehlgeschlagen: {exc}"
        ) from exc

    # Registry refresh — neuer Skill soll sofort in der Sidebar erscheinen
    try:
        await reg.reload()
    except Exception as exc:  # noqa: BLE001
        # Reload-Fail ist nicht fatal — der watchdog faengt's asynchron eh ein
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
    """Meta-Info fuer das Frontend: welche Kategorien, Sprachen, Trust-Levels
    existieren im aktuellen Katalog. Fuellt die Dropdowns im SkillFinder-Dialog
    dynamisch, damit sie nicht mit der JSON auseinanderlaufen.
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
