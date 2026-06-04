"""REST-API fuer die Doc-Sektion der Desktop-UI.

Endpoints:
- ``GET /api/docs``                Liste mit Frontmatter-Metadaten (ohne Body).
- ``GET /api/docs/grouped``        Gruppiert nach Diataxis-Quadrant — direkt
                                   verwendbar fuer die Sidebar-Tree-View.
- ``GET /api/docs/search``         FTS5-Volltextsuche mit BM25-Rank + Snippet.
- ``GET /api/docs/asset/{path}``   Bilder/Static-Files relativ zum Doc-Pfad.
- ``GET /api/docs/{slug}``         Voller Body + Frontmatter.
- ``POST /api/docs/reload``        Forciert Re-Indexing (Dev-Helper).

Der Router erwartet eine ``DocRegistry`` auf ``app.state.doc_registry`` —
``WebServer._setup_doc_registry()`` setzt sie beim Startup.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

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
        raise HTTPException(status_code=503, detail="DocRegistry nicht verfuegbar")
    return reg


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------

def _frontmatter_dump(doc: Doc) -> dict[str, Any]:
    """Pydantic ``model_dump`` mit ISO-Date-Strings (statt datetime)."""
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
        "tags": list(fm.tags),
        "related": list(fm.related),
        "deprecates": fm.deprecates,
        "deprecated_by": fm.deprecated_by,
        "next_review_due": fm.next_review_due.isoformat() if fm.next_review_due else None,
        "version_min": fm.version_min,
    }


def _doc_to_summary(doc: Doc) -> dict[str, Any]:
    """Schlanke Repraesentation fuer ``GET /api/docs`` — ohne Body, ohne
    Headings (TOC laedt das Frontend aus dem rohen Markdown beim Render).
    """
    return {
        **_frontmatter_dump(doc),
        "path": doc.path.as_posix(),
        "body_hash": doc.body_hash,
        "error": doc.error,
        "heading_count": len(doc.headings),
    }


def _doc_to_detail(doc: Doc) -> dict[str, Any]:
    """Vollausstattung fuer ``GET /api/docs/{slug}``."""
    out = _doc_to_summary(doc)
    out["body"] = doc.body
    out["headings"] = [
        {"level": lv, "text": txt, "slug": sl}
        for lv, txt, sl in doc.headings
    ]
    return out


# ----------------------------------------------------------------------
# List + Filter
# ----------------------------------------------------------------------

@router.get("")
def list_docs(
    request: Request,
    diataxis: DocDiataxis | None = None,
    status: DocStatus | None = None,
    phase: str | None = None,
    tags: list[str] = Query(default_factory=list),  # noqa: B008
) -> list[dict[str, Any]]:
    reg = _require_registry(request)
    docs = reg.filter(
        diataxis=diataxis, status=status, phase=phase, tags=tags or None,
    )
    docs.sort(key=lambda d: (d.frontmatter.diataxis.value, d.frontmatter.title.lower()))
    return [_doc_to_summary(d) for d in docs]


@router.get("/grouped")
def grouped_docs(request: Request) -> dict[str, list[dict[str, Any]]]:
    """Gruppiert nach Diataxis — Sidebar-Tree-Vorlage.

    Reihenfolge: tutorial -> howto -> explanation -> reference ->
    troubleshooting -> adr -> unclassified.
    """
    reg = _require_registry(request)
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
    return {
        key.value: [_doc_to_summary(d) for d in raw.get(key, [])]
        for key in order
        if key in raw and raw[key]
    }


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------

@router.get("/search")
def search_docs(
    request: Request,
    q: str,
    diataxis: DocDiataxis | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit muss zwischen 1 und 100 liegen")
    reg = _require_registry(request)
    results = reg.search_query(q, diataxis=diataxis, limit=limit)
    return [
        {
            "slug": r.slug,
            "title": r.title,
            "diataxis": r.diataxis,
            "phase": r.phase,
            "snippet": r.snippet,
            "score": r.score,
        }
        for r in results
    ]


# ----------------------------------------------------------------------
# Asset (Bilder relativ zum Doc-Pfad)
# ----------------------------------------------------------------------

@router.get("/asset/{slug}/{asset_path:path}")
def get_asset(request: Request, slug: str, asset_path: str) -> FileResponse:
    """Liefert eine Sibling-Datei (Bild, Diagramm) relativ zum Doc-Pfad.

    Path-Traversal-Schutz: das resolved Asset muss unter dem ``parent``
    des Doc-Files liegen. Sonst 400.
    """
    reg = _require_registry(request)
    doc = reg.get(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Doc '{slug}' nicht gefunden")

    base = doc.path.parent.resolve()
    target = (base / asset_path).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Pfad-Traversal nicht erlaubt",
        ) from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Asset nicht vorhanden")
    return FileResponse(target)


# ----------------------------------------------------------------------
# Detail (muss ans Ende — sonst frisst ``/{slug}`` die anderen Routen)
# ----------------------------------------------------------------------

@router.get("/{slug}")
def get_doc(request: Request, slug: str) -> dict[str, Any]:
    reg = _require_registry(request)
    doc = reg.get(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Doc '{slug}' nicht gefunden")
    return _doc_to_detail(doc)


# ----------------------------------------------------------------------
# Edit-this-page — oeffnet die .md im Windows-Standard-Editor
# ----------------------------------------------------------------------

@router.post("/{slug}/open")
def open_doc_in_editor(request: Request, slug: str) -> dict[str, Any]:
    """Oeffnet die Markdown-Datei im OS-Standard-Editor.

    Auf Windows: ``os.startfile`` startet den File-Type-Handler (typisch
    Notepad, VSCode oder ein Markdown-Editor). Wir launchen NICHT als
    elevated, das ist read+write fuer den User-Owner-Pfad — dem User
    ist das Doc-Repo ohnehin schon write-zugaenglich.

    Vorsicht: auf manchen Systemen ist Notepad mit BOM-Save-Behavior
    konfiguriert; Re-Save kann das Frontmatter zerschiessen. Der
    ``jarvis-doc-author``-Skill bleibt der bevorzugte Authoring-Pfad.
    """
    import os
    import subprocess

    reg = _require_registry(request)
    doc = reg.get(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Doc '{slug}' nicht gefunden")

    target = doc.path.resolve()
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht mehr vorhanden")

    try:
        if hasattr(os, "startfile"):
            # Windows-spezifisch — File-Type-Association entscheidet, welcher
            # Editor aufgeht. Non-blocking.
            os.startfile(str(target))  # noqa: S606
        else:  # pragma: no cover (nur Windows-relevant in dieser App)
            subprocess.Popen(["xdg-open", str(target)])
        return {"path": str(target), "opened": True}
    except OSError as exc:
        log.warning("open_doc_in_editor fehlgeschlagen fuer %s: %s", slug, exc)
        raise HTTPException(
            status_code=500, detail=f"Editor-Start fehlgeschlagen: {exc}",
        ) from exc


# ----------------------------------------------------------------------
# Reload (Dev-Helper)
# ----------------------------------------------------------------------

@router.post("/reload")
def reload_docs(request: Request) -> dict[str, Any]:
    reg = _require_registry(request)
    reg.reload_sync()
    return {"total": len(reg.list())}
