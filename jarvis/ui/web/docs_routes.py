"""REST API for the doc section of the desktop UI.

Endpoints:
- ``GET /api/docs``                List with frontmatter metadata (no body).
- ``GET /api/docs/grouped``        Grouped by Diataxis quadrant — directly
                                   usable for the sidebar tree view.
- ``GET /api/docs/search``         FTS5 full-text search with BM25 rank + snippet.
- ``GET /api/docs/asset/{slug}/{path}`` Images/static files relative to a guide.
- ``GET /api/docs/{slug}``         Full body + frontmatter.
- ``POST /api/docs/reload``        Forces re-indexing (dev helper).

The router expects a ``DocRegistry`` on ``app.state.doc_registry`` —
``WebServer._setup_doc_registry()`` sets it at startup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from jarvis.core.paths import repo_root
from jarvis.docs.registry import DocRegistry
from jarvis.docs.schema import Doc, DocDiataxis, DocStatus

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/docs", tags=["docs"])


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------


def _require_registry(request: Request) -> DocRegistry:
    reg = getattr(request.app.state, "doc_registry", None)
    if reg is None:
        raise HTTPException(status_code=503, detail="DocRegistry not available")
    return reg


async def _ready_registry(request: Request) -> DocRegistry:
    """Return a populated registry, loading it on demand when necessary."""
    reg = _require_registry(request)
    await reg.ensure_loaded()
    return reg


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------


def _frontmatter_dump(doc: Doc) -> dict[str, Any]:
    """Pydantic ``model_dump`` with ISO date strings (instead of datetime)."""
    fm = doc.frontmatter
    return {
        "title": fm.title,
        "slug": fm.slug,
        "diataxis": fm.diataxis.value,
        "status": fm.status.value,
        "owner": fm.owner,
        "last_reviewed": fm.last_reviewed.isoformat() if fm.last_reviewed else None,
        "phase": fm.phase,
        "audience": fm.audience,
        "summary": fm.summary,
        "section": fm.section,
        "section_order": fm.section_order,
        "order": fm.order,
        "tags": list(fm.tags),
        "related": list(fm.related),
        "deprecates": fm.deprecates,
        "deprecated_by": fm.deprecated_by,
        "next_review_due": fm.next_review_due.isoformat() if fm.next_review_due else None,
        "version_min": fm.version_min,
    }


def _doc_to_summary(doc: Doc) -> dict[str, Any]:
    """Lean representation for ``GET /api/docs`` — no body, no
    headings (the frontend loads the TOC from the raw Markdown at render time).
    """
    return {
        **_frontmatter_dump(doc),
        "path": _safe_display_path(doc.path),
        "body_hash": doc.body_hash,
        "error": _safe_doc_error(doc.error),
        "heading_count": len(doc.headings),
    }


def _safe_display_path(path: Path) -> str:
    """Return a useful path without exposing a local user directory."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root().resolve()).as_posix()
    except ValueError:
        return path.name


def _safe_doc_error(error: str | None) -> str | None:
    """Expose an actionable category without leaking a local path or content."""
    if error is None:
        return None
    category = error.partition(":")[0].strip().lower()
    if category in {
        "read failed",
        "frontmatter parse failed",
        "frontmatter schema invalid",
        "hard failure",
    }:
        return category
    return "indexing failed"


def _doc_to_nav_summary(doc: Doc) -> dict[str, Any]:
    """Compact sidebar payload used by the desktop Docs view."""
    fm = doc.frontmatter
    return {
        "title": fm.title,
        "slug": fm.slug,
        "diataxis": fm.diataxis.value,
        "summary": fm.summary,
        "section": fm.section,
        "section_order": fm.section_order,
        "order": fm.order,
        "tags": list(fm.tags),
        "related": list(fm.related),
    }


def _doc_to_detail(doc: Doc) -> dict[str, Any]:
    """Full payload for ``GET /api/docs/{slug}``."""
    out = _doc_to_summary(doc)
    out["body"] = doc.body
    out["headings"] = [{"level": lv, "text": txt, "slug": sl} for lv, txt, sl in doc.headings]
    return out


# ----------------------------------------------------------------------
# List + Filter
# ----------------------------------------------------------------------


@router.get("")
async def list_docs(
    request: Request,
    diataxis: DocDiataxis | None = None,
    status: DocStatus | None = None,
    phase: str | None = None,
    tags: list[str] = Query(default_factory=list),  # noqa: B008
) -> list[dict[str, Any]]:
    reg = await _ready_registry(request)
    docs = reg.filter(
        diataxis=diataxis,
        status=status,
        phase=phase,
        tags=tags or None,
    )
    docs.sort(key=lambda d: (d.frontmatter.diataxis.value, d.frontmatter.title.lower()))
    return [_doc_to_summary(d) for d in docs]


@router.get("/grouped")
async def grouped_docs(
    request: Request,
    compact: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Grouped by Diataxis — sidebar tree template.

    Order: tutorial -> howto -> explanation -> reference ->
    troubleshooting -> adr -> unclassified.
    """
    reg = await _ready_registry(request)
    raw = reg.grouped_by_diataxis()
    order = [
        DocDiataxis.TUTORIAL,
        DocDiataxis.HOWTO,
        DocDiataxis.EXPLANATION,
        DocDiataxis.REFERENCE,
        DocDiataxis.TROUBLESHOOTING,
        DocDiataxis.ADR,
        DocDiataxis.UNCLASSIFIED,
    ]
    serializer = _doc_to_nav_summary if compact else _doc_to_summary
    return {
        key.value: [serializer(d) for d in raw.get(key, [])]
        for key in order
        if key in raw and raw[key]
    }


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------


@router.get("/search")
async def search_docs(
    request: Request,
    q: str,
    diataxis: DocDiataxis | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    reg = await _ready_registry(request)
    results = reg.search_query(q, diataxis=diataxis, limit=limit)
    payload: list[dict[str, Any]] = []
    for result in results:
        doc = reg.get(result.slug)
        payload.append(
            {
                "slug": result.slug,
                "title": result.title,
                "diataxis": result.diataxis,
                "phase": result.phase,
                "summary": doc.frontmatter.summary if doc else "",
                "section": doc.frontmatter.section if doc else "Other",
                "snippet": result.snippet,
                "score": result.score,
            }
        )
    return payload


# ----------------------------------------------------------------------
# Asset (images relative to the doc path)
# ----------------------------------------------------------------------


@router.get("/asset/{slug}/{asset_path:path}")
async def get_asset(request: Request, slug: str, asset_path: str) -> FileResponse:
    """Returns a sibling file (image, diagram) relative to the doc path.

    Path-traversal protection: the resolved asset must live under the
    ``parent`` of the doc file. Otherwise 400.
    """
    reg = await _ready_registry(request)
    doc = reg.get(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Doc '{slug}' not found")

    base = doc.path.parent.resolve()
    target = (base / asset_path).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="path traversal not allowed",
        ) from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="asset does not exist")
    return FileResponse(target)


# ----------------------------------------------------------------------
# Detail (must go last — otherwise ``/{slug}`` swallows the other routes)
# ----------------------------------------------------------------------


@router.get("/{slug}")
async def get_doc(request: Request, slug: str) -> dict[str, Any]:
    reg = await _ready_registry(request)
    doc = reg.get(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Doc '{slug}' not found")
    return _doc_to_detail(doc)


# ----------------------------------------------------------------------
# Edit-this-page — opens the .md in the Windows default editor
# ----------------------------------------------------------------------


@router.post("/{slug}/open")
async def open_doc_in_editor(request: Request, slug: str) -> dict[str, Any]:
    """Opens the Markdown file in the OS default editor.

    Cross-platform via ``jarvis.platform.open_path.open_file`` (Windows
    ``os.startfile``, macOS ``open``, Linux ``xdg-open``). We do NOT launch
    elevated — that path is read+write for the user-owner already; the user
    already has write access to the doc repo anyway.

    Caution: on some systems Notepad is configured with BOM-save
    behavior; re-saving can mangle the frontmatter. The
    ``jarvis-doc-author`` skill remains the preferred authoring path.
    """
    from jarvis.platform import open_path

    reg = await _ready_registry(request)
    doc = reg.get(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Doc '{slug}' not found")

    target = doc.path.resolve()
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file no longer exists")

    opened = open_path.open_file(target)
    if not opened:
        raise HTTPException(status_code=500, detail="editor start failed")
    return {"path": _safe_display_path(target), "opened": True}


# ----------------------------------------------------------------------
# Reload (dev helper)
# ----------------------------------------------------------------------


@router.post("/reload")
async def reload_docs(request: Request) -> dict[str, Any]:
    reg = _require_registry(request)
    await reg.reload()
    return {"total": len(reg.list())}
