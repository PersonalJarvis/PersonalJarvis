"""FastAPI routes that expose the on-disk Obsidian vault as JSON (Phase B3, Agent A).

The read endpoints power the Desktop App's "Wiki" sidebar tab:

* ``GET /api/wiki/tree``            — folder structure with file metadata.
* ``GET /api/wiki/page/{slug}``     — one page (frontmatter + body + wikilinks).
* ``GET /api/wiki/graph``           — nodes + edges built from outbound wikilinks.
* ``GET /api/wiki/backlinks/{slug}``— reverse-link list with body snippets.
* ``GET /api/wiki/search``          — keyword search via ``VaultSearch`` (B5).

Later additions expose telemetry, health, index repair, and one explicit write
surface: ``POST /api/wiki/ingest``. The write route delegates to the same
curator service as the native ``wiki-ingest`` brain tool.

Read endpoints follow the existing house style: HTTP 200 with the envelope
``{"ok": True, ...}`` on success and ``{"ok": False, "error": "..."}`` on
logical errors. HTTP 404 stays reserved for unknown routes; HTTP 500 only
fires on unhandled exceptions. The write endpoint uses non-2xx responses when
nothing was stored so CLI and agent callers cannot mistake a no-op for success.

The module reuses the tolerant Markdown parser (B1,
``jarvis/memory/wiki/page.py``), ``VaultSearch`` (B5,
``jarvis/memory/wiki/search.py``), and the guarded Wiki ingest service. It
never writes Wiki files directly.

The vault root is read from ``app.state.config.wiki_integration.vault_root``.
Tests can override the path by setting that attribute directly before the
TestClient context, or by passing a config object whose ``wiki_integration``
section already points at a temporary directory.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.memory.wiki.ingest_service import (
    MAX_INGEST_CHARS,
    MAX_SOURCE_CHARS,
    MIN_INGEST_CHARS,
    ingest_wiki_text,
)
from jarvis.memory.wiki.integration import (
    get_running_capture_runtime,
    get_running_curator,
)
from jarvis.memory.wiki.page import parse_markdown
from jarvis.memory.wiki.protocols import WikiPage
from jarvis.memory.wiki.search import VaultSearch
from jarvis.memory.wiki.telemetry import telemetry as _telemetry
from jarvis.memory.wiki.vault_root import resolve_vault_root
from jarvis.memory.wiki.wikilink import (
    _canonicalise as _canonicalise_link,  # type: ignore[attr-defined]
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wiki", tags=["wiki"])

# Canonical directories remain first in the tree for response compatibility.
_PAGE_DIRS: tuple[tuple[str, str], ...] = (
    ("entities", "entity"),
    ("concepts", "concept"),
    ("projects", "project"),
    ("sessions", "session"),
)

# Frozen history and binary-only storage are not live Wiki pages. Hidden
# directories (for example .obsidian and .trash) are pruned separately.
_EXCLUDED_PAGE_DIRS: frozenset[str] = frozenset(
    {"_archive", "attachments", "90-attachments"}
)
# Template scaffolding stays browsable in the tree but never enters the
# graph: its bodies are unrendered placeholders (``{{title}}``, ``[[…]]``),
# so every "link" would materialise as a phantom node.
_TEMPLATE_DIRS: frozenset[str] = frozenset(
    {"templates", "_templates", "99-templates"}
)
_ROOT_FOLDER_NAME = "root"

# Snippet window (chars) for backlink context extraction around the wikilink.
_BACKLINK_SNIPPET_RADIUS = 80
_BACKLINK_SNIPPET_MAX = 200


class WikiIngestRequest(BaseModel):
    """One explicit, self-contained fact or summary to store."""

    text: str = Field(min_length=MIN_INGEST_CHARS, max_length=MAX_INGEST_CHARS)
    source: str = Field(
        default="api:wiki-ingest",
        min_length=1,
        max_length=MAX_SOURCE_CHARS,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:._/-]*$",
    )


class WikiIngestResponse(BaseModel):
    """Honest result returned only after at least one page was written."""

    ok: bool = True
    source: str
    applied: int
    skipped_due_to_recent_edit: int
    failed_validation: int
    blocked_sensitive_content: int
    pages_touched: list[str]


class WikiBackfillRequest(BaseModel):
    """Bounded, evidence-only Realtime session review request."""

    days: int = Field(default=2, ge=1, le=30)
    max_sessions: int = Field(default=20, ge=1, le=100)
    dry_run: bool = True


# ----------------------------------------------------------------------
# Vault-root resolution
# ----------------------------------------------------------------------


def _resolve_vault_root(request: Request) -> Path | None:
    """Return the configured vault root, or ``None`` when the app has no config.

    Resolves through the canonical
    :func:`jarvis.memory.wiki.vault_root.resolve_vault_root` (spec A7) — a
    relative root anchors to the repo root, never the process CWD.

    The route handlers tolerate a missing root by returning an empty-but-valid
    response (per §3.1 edge cases). They do not raise.
    """
    config = getattr(request.app.state, "config", None)
    if config is None:
        return None
    wiki_cfg = getattr(config, "wiki_integration", None)
    if wiki_cfg is None:
        return None
    raw = getattr(wiki_cfg, "vault_root", None)
    if raw is None:
        return None
    return resolve_vault_root(raw).path


# ----------------------------------------------------------------------
# Title / kind helpers
# ----------------------------------------------------------------------


def _title_of(page: WikiPage) -> str:
    """Best-effort human title: H1 in the body, else slug humanised."""
    for line in page.body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return page.slug.replace("-", " ").replace("_", " ").title()


def _display_title(page: WikiPage) -> str:
    """Frontmatter title, else H1, else humanised slug — never a placeholder.

    Template pages carry unrendered Obsidian placeholders (``{{title}}``,
    ``{{date:dddd, MMMM Do YYYY}}``) as their H1; showing those verbatim in
    the tree or graph reads as junk, so any candidate containing ``{{``
    falls through to the next source.
    """
    for candidate in (str(page.frontmatter.get("title") or ""), _title_of(page)):
        candidate = candidate.strip()
        if candidate and "{{" not in candidate:
            return candidate
    return _human_title_from_slug(page.slug)


def _kind_of(page: WikiPage) -> str:
    """Schema kind (entity/concept/project/session/meta) of a page."""
    return page.page_type or "meta"


def _human_title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


# ----------------------------------------------------------------------
# Visible-vault projection (off-loop)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _VisiblePage:
    """One live Markdown page plus filesystem metadata for route responses."""

    page: WikiPage
    relative_path: Path
    mtime: float
    size: int


def _visible_path_sort_key(relative_path: Path) -> tuple[int, str]:
    """Keep canonical page directories first, then root and custom folders."""
    parts = relative_path.parts
    top = parts[0] if len(parts) > 1 else ""
    standard_order = {name: index for index, (name, _) in enumerate(_PAGE_DIRS)}
    if top in standard_order:
        rank = standard_order[top]
    elif not top:
        rank = len(_PAGE_DIRS)
    else:
        rank = len(_PAGE_DIRS) + 1
    return rank, relative_path.as_posix().casefold()


def _scan_visible_pages_sync(vault_root: Path) -> list[_VisiblePage]:
    """Parse every user-visible Markdown page below ``vault_root``.

    Hidden trees, frozen archive history, and attachment stores are pruned
    before files are opened. Symlinked files that resolve outside the vault
    are also ignored so a crafted vault cannot expose unrelated host files.
    """
    root = vault_root.resolve()
    paths: list[tuple[Path, Path]] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            (
                name
                for name in dirnames
                if not name.startswith(".")
                and name.casefold() not in _EXCLUDED_PAGE_DIRS
            ),
            key=str.casefold,
        )
        current_path = Path(current)
        for filename in sorted(filenames, key=str.casefold):
            if filename.startswith(".") or Path(filename).suffix.casefold() != ".md":
                continue
            path = current_path / filename
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
                relative = path.relative_to(root)
            except (OSError, ValueError):
                continue
            paths.append((path, relative))

    paths.sort(key=lambda item: _visible_path_sort_key(item[1]))
    visible: list[_VisiblePage] = []
    for path, relative in paths:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
        except OSError as exc:
            log.warning("wiki_route_walk_failed: %s - %s", path, exc)
            continue
        visible.append(
            _VisiblePage(
                page=parse_markdown(raw, path),
                relative_path=relative,
                mtime=stat.st_mtime,
                size=stat.st_size,
            )
        )
    return visible


async def _scan_visible_pages(vault_root: Path) -> list[_VisiblePage]:
    """Build a fresh, non-blocking projection of the active Obsidian vault."""
    return await asyncio.to_thread(_scan_visible_pages_sync, vault_root)


def _folder_name(relative_path: Path) -> str:
    parent = relative_path.parent
    return _ROOT_FOLDER_NAME if parent == Path(".") else parent.as_posix()


def _folder_kind(name: str, pages: list[_VisiblePage]) -> str:
    standard_kinds = dict(_PAGE_DIRS)
    if name in standard_kinds:
        return standard_kinds[name]
    kinds = {item.page.page_type for item in pages if item.page.page_type}
    return next(iter(kinds)) if len(kinds) == 1 else "meta"


def _tree_from_visible_pages(
    visible: list[_VisiblePage],
) -> tuple[list[dict[str, Any]], float | None]:
    """Return compatible flat folder buckets plus the root log timestamp."""
    grouped: dict[str, list[_VisiblePage]] = {name: [] for name, _ in _PAGE_DIRS}
    log_mtime: float | None = None
    for item in visible:
        name = _folder_name(item.relative_path)
        grouped.setdefault(name, []).append(item)
        if item.relative_path.as_posix() == "log.md":
            log_mtime = item.mtime

    standard_names = [name for name, _ in _PAGE_DIRS]
    extra_names = sorted(
        (name for name in grouped if name not in standard_names),
        key=lambda name: (name != _ROOT_FOLDER_NAME, name.casefold()),
    )
    folders: list[dict[str, Any]] = []
    for name in [*standard_names, *extra_names]:
        pages = grouped[name]
        files = [
            {
                "slug": item.page.slug,
                "title": _display_title(item.page),
                "mtime": item.mtime,
                "size": item.size,
            }
            for item in pages
        ]
        folders.append(
            {
                "name": name,
                "kind": _folder_kind(name, pages),
                "count": len(files),
                "files": files,
            }
        )
    return folders, log_mtime


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("/tree")
async def get_tree(request: Request) -> dict[str, Any]:
    """Return the folder structure and per-file metadata.

    Empty-but-valid response when the vault is missing, empty, or
    misconfigured. The UI renders an empty-state placeholder in that case.
    """
    vault_root = _resolve_vault_root(request)
    vault_str = str(vault_root) if vault_root is not None else ""

    if vault_root is None or not vault_root.is_dir():
        empty_folders = [
            {"name": dirname, "kind": kind, "count": 0, "files": []}
            for dirname, kind in _PAGE_DIRS
        ]
        return {
            "ok": True,
            "vault_root": vault_str,
            "folders": empty_folders,
            "stats": {
                "total_pages": 0,
                "total_links": 0,
                "last_curator_run": None,
            },
        }

    try:
        visible = await _scan_visible_pages(vault_root)
        folders, log_mtime = _tree_from_visible_pages(visible)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_route_failed route=/tree error=%s", exc)
        return {"ok": False, "error": "tree walk failed"}

    total_links = sum(len(item.page.wikilinks) for item in visible)

    last_curator_run = _format_mtime(log_mtime)

    return {
        "ok": True,
        "vault_root": vault_str,
        "folders": folders,
        "stats": {
            "total_pages": len(visible),
            "total_links": total_links,
            "last_curator_run": last_curator_run,
        },
    }


@router.get("/page/{slug}")
async def get_page(slug: str, request: Request) -> dict[str, Any]:
    """Return one page by slug, including frontmatter, body, and wikilinks."""
    if not _is_safe_slug(slug):
        # A page slug is a single kebab-case segment. Reject anything that
        # could escape the vault in the _find_page disk-probe fallback — on
        # Windows a backslash is a valid single URL segment, so ``..\\..\\x``
        # would otherwise read an arbitrary .md outside the vault.
        return {"ok": False, "error": "invalid slug"}

    vault_root = _resolve_vault_root(request)
    if vault_root is None or not vault_root.is_dir():
        return {"ok": False, "error": "vault not configured"}

    try:
        page = await _find_page(vault_root, slug)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_route_failed route=/page/%s error=%s", slug, exc)
        return {"ok": False, "error": "page lookup failed"}

    if page is None:
        return {"ok": False, "error": "page not found"}

    page_path = Path(page.path)
    try:
        stat = await asyncio.to_thread(page_path.stat)
        mtime = stat.st_mtime
        size_bytes = stat.st_size
    except OSError:
        mtime = 0.0
        size_bytes = len(page.body.encode("utf-8"))

    rel_path = _relative_to_vault(page_path, vault_root)
    title = _display_title(page)
    body_md = page.body
    words = len(body_md.split())

    return {
        "ok": True,
        "slug": page.slug,
        "kind": _kind_of(page),
        "title": title,
        "path": rel_path,
        "frontmatter": dict(page.frontmatter),
        "frontmatter_valid": bool(page.is_schema_valid),
        "body_md": body_md,
        "wikilinks": [_link_slug(link) for link in page.wikilinks],
        "stats": {
            "words": words,
            "bytes": size_bytes,
            "mtime": mtime,
        },
    }


@router.get("/graph")
async def get_graph(request: Request) -> dict[str, Any]:
    """Return all pages as nodes and outbound wikilinks as edges.

    Edges whose target page does not exist are returned in ``broken[]``
    instead of ``edges[]`` so the UI can render them differently.
    """
    vault_root = _resolve_vault_root(request)
    if vault_root is None or not vault_root.is_dir():
        return {"ok": True, "nodes": [], "edges": [], "broken": []}

    try:
        visible = await _scan_visible_pages(vault_root)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_route_failed route=/graph error=%s", exc)
        return {"ok": False, "error": "graph build failed"}

    pages = [
        item.page
        for item in visible
        if not (
            len(item.relative_path.parts) > 1
            and item.relative_path.parts[0].casefold() in _TEMPLATE_DIRS
        )
    ]

    nodes: list[dict[str, Any]] = []
    known_slugs: set[str] = set()
    for page in pages:
        slug = page.slug
        if slug in known_slugs:
            continue
        known_slugs.add(slug)
        nodes.append(
            {
                "id": slug,
                "kind": _kind_of(page),
                "title": _display_title(page),
            }
        )

    edges: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    for page in pages:
        body = page.body
        for raw_link in page.wikilinks:
            target_slug = _link_slug(raw_link)
            if not target_slug:
                continue
            context = _link_context(body, raw_link, target_slug)
            if target_slug in known_slugs:
                edges.append(
                    {
                        "source": page.slug,
                        "target": target_slug,
                        "context": context,
                    }
                )
            else:
                broken.append(
                    {
                        "source": page.slug,
                        "target": target_slug,
                        "context": context,
                    }
                )

    return {
        "ok": True,
        "nodes": nodes,
        "edges": edges,
        "broken": broken,
    }


@router.get("/backlinks/{slug}")
async def get_backlinks(slug: str, request: Request) -> dict[str, Any]:
    """Return all pages whose body contains a wikilink to ``slug``."""
    vault_root = _resolve_vault_root(request)
    if vault_root is None or not vault_root.is_dir():
        return {"ok": True, "slug": slug, "backlinks": []}

    try:
        visible = await _scan_visible_pages(vault_root)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_route_failed route=/backlinks/%s error=%s", slug, exc)
        return {"ok": False, "error": "backlinks lookup failed"}

    sources = [
        item.page
        for item in visible
        if any(_link_slug(link) == slug for link in item.page.wikilinks)
    ]
    out: list[dict[str, Any]] = []
    for src in sources:
        snippet = _link_context(src.body, slug, slug)
        out.append(
            {
                "slug": src.slug,
                "title": _display_title(src),
                "snippet": snippet,
            }
        )

    return {"ok": True, "slug": slug, "backlinks": out}


@router.get("/search")
async def get_search(
    request: Request,
    q: str = Query(default=""),
    k: int = Query(default=5, ge=1, le=50),
) -> dict[str, Any]:
    """Keyword search over the vault via ``VaultSearch`` (B5).

    Empty queries return ``{"ok": False, "error": "empty query"}`` —
    the UI uses that signal to suppress its result list while the user
    is still typing the first character.
    """
    if not q or not q.strip():
        return {"ok": False, "error": "empty query"}

    sanitised = _sanitise_query(q)
    if not sanitised:
        return {"ok": False, "error": "empty query"}

    vault_root = _resolve_vault_root(request)
    if vault_root is None or not vault_root.is_dir():
        return {"ok": True, "query": sanitised, "hits": []}

    try:
        searcher = VaultSearch(vault_root)
        hits = await asyncio.to_thread(searcher.search, sanitised, k=k)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_route_failed route=/search q=%r error=%s", q, exc)
        return {"ok": False, "error": "search failed"}

    rendered: list[dict[str, Any]] = []
    for hit in hits:
        path = Path(hit.path)
        rel_path = _relative_to_vault(path, vault_root)
        rendered.append(
            {
                "slug": path.stem,
                "title": hit.title,
                "path": rel_path,
                "snippet": hit.snippet,
                "score": hit.score,
            }
        )

    return {"ok": True, "query": sanitised, "hits": rendered}


@router.post(
    "/ingest",
    response_model=WikiIngestResponse,
    openapi_extra={"x-jarvis-risk-tier": "monitor"},
)
async def ingest_wiki(payload: WikiIngestRequest) -> WikiIngestResponse:
    """Store one fact or summary through the guarded live Wiki curator."""
    outcome = await ingest_wiki_text(
        curator=get_running_curator(),
        text=payload.text,
        source=payload.source,
    )
    if not outcome.success:
        status_code = {
            "not-bootstrapped": 503,
            "curator-failed": 503,
            "recent-edit-conflict": 409,
        }.get(outcome.error_code or "", 422)
        raise HTTPException(
            status_code=status_code,
            detail={
                "code": outcome.error_code or "wiki-ingest-failed",
                "message": outcome.error or "nothing was stored",
                "source": outcome.source,
                "skipped_due_to_recent_edit": len(
                    outcome.skipped_due_to_recent_edit
                ),
                "failed_validation": len(outcome.failed_validation),
                "blocked_sensitive_content": len(outcome.blocked_pii),
            },
        )

    return WikiIngestResponse(
        source=outcome.source,
        applied=len(outcome.applied),
        skipped_due_to_recent_edit=len(outcome.skipped_due_to_recent_edit),
        failed_validation=len(outcome.failed_validation),
        blocked_sensitive_content=len(outcome.blocked_pii),
        pages_touched=outcome.page_names,
    )


@router.post(
    "/backfill",
    openapi_extra={
        "x-jarvis-dangerous": True,
        "x-jarvis-risk-tier": "ask",
        # The route performs bounded Stage-1 and Stage-2 provider calls inline.
        # Auto-generated CLI clients consume this instead of their 30s default.
        "x-jarvis-timeout-seconds": 21_600,
    },
)
async def backfill_wiki(
    request: Request,
    payload: WikiBackfillRequest,
) -> dict[str, Any]:
    """Review recent persisted Realtime sessions through the live Wiki pipeline."""
    from jarvis.memory.wiki.backfill import backfill_realtime_sessions

    runtime = get_running_capture_runtime()
    store = getattr(request.app.state, "session_store", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="wiki capture runtime unavailable")
    if store is None:
        raise HTTPException(status_code=503, detail="voice session store unavailable")
    if not payload.dry_run and runtime.scheduler is None:
        raise HTTPException(status_code=503, detail="wiki Stage-2 consolidator unavailable")

    result = await backfill_realtime_sessions(
        store=store,
        extractor=runtime.extractor,
        days=payload.days,
        max_sessions=payload.max_sessions,
        dry_run=payload.dry_run,
    )
    response: dict[str, Any] = {
        "ok": True,
        **result.as_dict(),
        "consolidation_runs": 0,
        "consolidation_labels": [],
        "consolidation_skip_reason": "",
        "journal_backlog": runtime.journal.backlog_count(),
    }
    if payload.dry_run:
        return response

    review_keys = tuple(getattr(result, "review_keys", ()))
    attempted_keys = tuple(getattr(result, "attempted_review_keys", ()))
    attempted_key_set = set(attempted_keys)
    preexisting_keys = tuple(key for key in review_keys if key not in attempted_key_set)
    stage2 = runtime.journal.capture_decision_summary(review_keys)
    preexisting_before = runtime.journal.capture_decision_summary(preexisting_keys)
    consolidation_runs = 0
    labels: list[str] = []
    skip_reason = ""
    from jarvis.memory.wiki.scheduler import TriggerSource

    # Same-target candidates are deliberately serialized so each judge sees
    # the preceding landed fact. Bound the work by the selected candidate
    # count rather than a fixed 120-pass ceiling that could strand a large but
    # valid backfill.
    max_passes = min(2_000, max(1, int(stage2.get("candidate_rows", 0)) + 10))
    for _ in range(max_passes):
        if int(stage2.get("pending", 0)) <= 0:
            break
        scheduler_result = await runtime.scheduler.trigger(
            TriggerSource.JOURNAL,
            review_keys=review_keys,
        )
        label = str(getattr(scheduler_result, "curator_output_label", "") or "")
        triggered = bool(getattr(scheduler_result, "triggered", False))
        if triggered:
            consolidation_runs += 1
            labels.append(label)
        else:
            skip_reason = str(getattr(scheduler_result, "skip_reason", "unknown"))
            break
        stage2 = runtime.journal.capture_decision_summary(review_keys)
        if label in {"judge-unavailable", "judge-truncated"} or label.startswith(
            "journal-transient:"
        ):
            skip_reason = label
            break

    final_backlog = runtime.journal.backlog_count()
    stage2 = runtime.journal.capture_decision_summary(review_keys)
    remaining = int(stage2.get("pending", 0))
    attempted_final = runtime.journal.capture_decision_summary(attempted_keys)
    preexisting_final = runtime.journal.capture_decision_summary(preexisting_keys)
    write_decisions = ("add", "update", "invalidate")
    accepted_writes = sum(
        int(attempted_final.get(decision, 0))
        + max(
            0,
            int(preexisting_final.get(decision, 0))
            - int(preexisting_before.get(decision, 0)),
        )
        for decision in write_decisions
    )
    response.update(
        {
            "consolidation_runs": consolidation_runs,
            "consolidation_labels": labels,
            "consolidation_skip_reason": skip_reason,
            "journal_backlog": final_backlog,
            "stage2": stage2,
            "accepted_writes": accepted_writes,
        }
    )
    extraction_failed = int(getattr(result, "sessions_failed", 0))
    extraction_in_progress = int(getattr(result, "sessions_in_progress", 0))
    rejected = int(stage2.get("rejected", 0))
    skipped = int(stage2.get("skipped", 0))
    if (
        remaining > 0
        or skip_reason
        or extraction_failed
        or extraction_in_progress
        or rejected
        or skipped
    ):
        response["ok"] = False
        if extraction_failed:
            code = "wiki-backfill-extraction-failed"
        elif extraction_in_progress and not (remaining > 0 or skip_reason):
            code = "wiki-backfill-already-running"
        elif rejected:
            code = "wiki-backfill-stage2-rejected"
        elif skipped:
            code = "wiki-backfill-stage2-skipped"
        else:
            code = "wiki-backfill-stage2-incomplete"
        status_code = 422 if code == "wiki-backfill-stage2-rejected" else 503
        if code == "wiki-backfill-already-running":
            status_code = 409
        raise HTTPException(
            status_code=status_code,
            detail={"code": code, **response},
        )
    return response


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _is_safe_slug(slug: str) -> bool:
    """True when ``slug`` is a single safe vault segment.

    A page slug is one kebab-case path component. Reject empty input, path
    separators (``/`` and Windows ``\\``), parent refs (``..``), drive
    letters (``:``), home expansion (``~``), and NUL — any of which could
    escape the vault when joined as ``vault_root / dir / f"{slug}.md"``.
    """
    if not slug or len(slug) > 200:
        return False
    return not (
        "/" in slug
        or "\\" in slug
        or ".." in slug
        or ":" in slug
        or slug.startswith("~")
        or "\x00" in slug
    )


async def _find_page(vault_root: Path, slug: str) -> WikiPage | None:
    """Locate a visible page by slug using the stable projection order."""
    for item in await _scan_visible_pages(vault_root):
        if item.page.slug == slug:
            return item.page
    return None


def _relative_to_vault(path: Path, vault_root: Path) -> str:
    """POSIX-style path relative to the vault root, or the bare name as fallback."""
    try:
        rel = path.resolve().relative_to(vault_root.resolve())
    except (ValueError, OSError):
        return path.name
    return rel.as_posix()


def _link_slug(link: str) -> str:
    """Reduce a path-qualified Obsidian link to its page slug."""
    target = _canonicalise_link(link).replace("\\", "/").rsplit("/", 1)[-1]
    return target[:-3] if target.casefold().endswith(".md") else target


def _link_context(body: str, link_target: str, fallback_slug: str) -> str:
    """Return a short context snippet around the first occurrence of a link.

    The lookup tries the raw ``[[target]]`` form first, then the bare slug.
    Returns the empty string when nothing matches.
    """
    if not body:
        return ""
    needles = (
        f"[[{link_target}]]",
        f"[[{link_target}|",
        f"[[{fallback_slug}]]",
        f"[[{fallback_slug}|",
        link_target,
        fallback_slug,
    )
    idx = -1
    for needle in needles:
        if not needle:
            continue
        found = body.find(needle)
        if found >= 0:
            idx = found
            break
    if idx < 0:
        return ""
    start = max(0, idx - _BACKLINK_SNIPPET_RADIUS)
    end = min(len(body), idx + _BACKLINK_SNIPPET_RADIUS)
    raw = body[start:end].replace("\n", " ").strip()
    if len(raw) > _BACKLINK_SNIPPET_MAX:
        raw = raw[: _BACKLINK_SNIPPET_MAX].rsplit(" ", 1)[0] + "…"
    if start > 0:
        raw = "…" + raw
    if end < len(body):
        raw = raw + "…"
    return raw


def _sanitise_query(q: str) -> str:
    """Strip FTS5 syntax characters from a user-typed query.

    ``VaultSearch`` (B5) is a file-walking searcher that does not actually
    feed FTS5, but we keep the sanitisation in case a future backend swaps
    in an FTS5-backed implementation behind the same interface. The rule
    mirrors ``jarvis/memory/recall.py::_sanitize_fts5_query``: drop double
    quotes and trim. Plus we additionally strip the FTS5 prefix/boolean
    operators so a query like ``pizza AND "voice"`` becomes ``pizza AND voice``.
    """
    cleaned = q.replace('"', " ")
    # Drop characters that are legal in FTS5 syntax but not as content tokens.
    for ch in ("(", ")", "*", "^"):
        cleaned = cleaned.replace(ch, " ")
    cleaned = " ".join(cleaned.split())
    return cleaned


def _format_mtime(mtime: float | None) -> str | None:
    """Format a POSIX mtime as ISO-8601 (UTC) or return ``None`` when missing."""
    if mtime is None:
        return None
    from datetime import datetime

    try:
        return (
            datetime.fromtimestamp(mtime, tz=UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OSError, OverflowError, ValueError):
        return None


# ----------------------------------------------------------------------
# Telemetry (B8.7)
# ----------------------------------------------------------------------


@router.get("/telemetry")
async def get_telemetry(_request: Request) -> dict[str, Any]:
    """Return the in-memory counter snapshot.

    Lightweight observability endpoint -- inspect "is the memory pipeline
    alive?" without grepping logs. Counters reset on every Jarvis
    restart (this is observability, not metrics).
    """
    return {
        "ok": True,
        "counters": _telemetry.snapshot(),
    }


def _visible_markdown_count(vault_root: Path) -> int:
    """Count indexable Markdown pages, excluding hidden directories."""
    return sum(
        1
        for path in vault_root.rglob("*.md")
        if not any(
            part.startswith(".")
            for part in path.relative_to(vault_root).parts[:-1]
        )
    )


@router.post("/reindex")
async def reindex_wiki(
    request: Request,
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    """Rebuild the derived wiki search index from the active vault."""
    import sqlite3

    from jarvis.memory.wiki.db_path import resolve_wiki_db_path
    from jarvis.memory.wiki.fts_index import rebuild_index

    vault_root = _resolve_vault_root(request)
    if vault_root is None or not vault_root.is_dir():
        return {"ok": False, "error": "vault unavailable"}

    config = getattr(request.app.state, "config", None)
    data_dir = getattr(getattr(config, "memory", None), "data_dir", "./data")
    db_path = resolve_wiki_db_path(data_dir)
    vault_pages = _visible_markdown_count(vault_root)

    def _run() -> tuple[int, int]:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        try:
            try:
                before = int(conn.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0])
            except sqlite3.OperationalError:
                before = 0
            if dry_run:
                return before, before
            return before, rebuild_index(vault_root, conn)
        finally:
            conn.close()

    before, indexed = await asyncio.to_thread(_run)
    return {
        "ok": True,
        "dry_run": dry_run,
        "vault_root": str(vault_root),
        "vault_pages": vault_pages,
        "indexed_before": before,
        "indexed_pages": indexed,
    }


@router.get("/health")
async def wiki_health(request: Request) -> dict[str, Any]:
    """Wiki subsystem health for the Wiki tab status panel (spec A5).

    The wiki subsystem is fire-and-forget by design (AP-9): failures never
    interrupt a voice turn, but they must not vanish either. This surfaces
    the process-wide :mod:`jarvis.memory.wiki.health` singleton so bootstrap
    failures, write failures, chain exhaustion, and journal pressure become
    visible instead of only ever appearing in the logs.
    """
    import sqlite3

    from jarvis.memory.wiki.db_path import resolve_wiki_db_path
    from jarvis.memory.wiki.health import health as _health
    from jarvis.memory.wiki.health import inspect_index_health

    snapshot = _health.snapshot()
    vault_root = _resolve_vault_root(request)
    config = getattr(request.app.state, "config", None)
    data_dir = getattr(getattr(config, "memory", None), "data_dir", "./data")
    db_path = resolve_wiki_db_path(data_dir)

    def _persistent_state() -> dict[str, Any]:
        capture_funnel = {
            "window_hours": 24,
            "total": 0,
            "started": 0,
            "filtered": 0,
            "empty": 0,
            "candidates": 0,
            "failed": 0,
            "facts": 0,
            "sessions_swept": 0,
            "stage2_pending": 0,
            "stage2_add": 0,
            "stage2_update": 0,
            "stage2_noop": 0,
            "stage2_invalidate": 0,
            "stage2_rejected": 0,
            "stage2_skipped": 0,
            "writes": 0,
        }
        state: dict[str, Any] = {
            "journal_backlog": 0,
            "last_write": None,
            "capture_funnel": capture_funnel,
            "capture_error": None,
        }
        if not db_path.exists():
            return state
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        try:
            try:
                state["journal_backlog"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM wiki_candidate_journal "
                        "WHERE status = 'pending'"
                    ).fetchone()[0]
                )
                row = conn.execute(
                    "SELECT consolidated_ms, target_path, source_label "
                    "FROM wiki_candidate_journal "
                    "WHERE status = 'consolidated' AND target_path IS NOT NULL "
                    "ORDER BY consolidated_ms DESC LIMIT 1"
                ).fetchone()
                if row and row[0]:
                    state["last_write"] = {
                        "ts": float(row[0]) / 1000.0,
                        "ok": True,
                        "pages": [str(row[1])],
                        "error": None,
                        "source": str(row[2]),
                    }
                since_ms = int(time.time() * 1000) - 24 * 60 * 60 * 1000
                audit = conn.execute(
                    """
                    SELECT COUNT(*),
                      SUM(CASE WHEN status = 'started' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN status = 'filtered' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN status = 'empty' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN status = 'candidates' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END),
                      COALESCE(SUM(candidate_count), 0),
                      COUNT(DISTINCT CASE
                        WHEN REPLACE(source_kind, '_', '-') = 'session-sweep'
                             AND session_id != '' THEN session_id
                        ELSE NULL END)
                    FROM wiki_extraction_audit WHERE updated_ms >= ?
                    """,
                    (since_ms,),
                ).fetchone()
                if audit is not None:
                    keys = (
                        "total",
                        "started",
                        "filtered",
                        "empty",
                        "candidates",
                        "failed",
                        "facts",
                        "sessions_swept",
                    )
                    state["capture_funnel"] = {
                        **capture_funnel,
                        "window_hours": 24,
                        **{
                            key: int(value or 0)
                            for key, value in zip(keys, audit, strict=True)
                        },
                    }
                decisions = conn.execute(
                    """
                    SELECT
                      SUM(CASE WHEN j.status = 'pending' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN j.status = 'consolidated' AND j.decision = 'add'
                               THEN 1 ELSE 0 END),
                      SUM(CASE WHEN j.status = 'consolidated' AND j.decision = 'update'
                               THEN 1 ELSE 0 END),
                      SUM(CASE WHEN j.status = 'consolidated' AND j.decision = 'noop'
                               THEN 1 ELSE 0 END),
                      SUM(CASE WHEN j.status = 'consolidated' AND j.decision = 'invalidate'
                               THEN 1 ELSE 0 END),
                      SUM(CASE WHEN j.status = 'rejected' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN j.status = 'skipped' THEN 1 ELSE 0 END)
                    FROM wiki_candidate_journal AS j
                    JOIN wiki_candidate_capture AS c ON c.candidate_id = j.id
                    WHERE j.created_ms >= ?
                    """,
                    (since_ms,),
                ).fetchone()
                if decisions is not None:
                    decision_keys = (
                        "stage2_pending",
                        "stage2_add",
                        "stage2_update",
                        "stage2_noop",
                        "stage2_invalidate",
                        "stage2_rejected",
                        "stage2_skipped",
                    )
                    for key, value in zip(decision_keys, decisions, strict=True):
                        state["capture_funnel"][key] = int(value or 0)
                    state["capture_funnel"]["writes"] = sum(
                        state["capture_funnel"][key]
                        for key in ("stage2_add", "stage2_update", "stage2_invalidate")
                    )
            except sqlite3.OperationalError:
                state["capture_error"] = "capture_store_unavailable"
        finally:
            conn.close()
        return state

    persistent, index_health = await asyncio.gather(
        asyncio.to_thread(_persistent_state),
        asyncio.to_thread(inspect_index_health, vault_root, db_path),
    )
    if snapshot["last_write"] is None and persistent["last_write"] is not None:
        snapshot["last_write"] = persistent["last_write"]
    snapshot["journal_backlog"] = persistent["journal_backlog"]
    snapshot["capture_funnel"] = persistent["capture_funnel"]
    snapshot["capture_error"] = persistent["capture_error"]
    snapshot.update(index_health)

    last_index = snapshot.get("last_index")
    if (
        isinstance(last_index, dict)
        and last_index.get("ok") is False
        and snapshot.get("index_state") != "ok"
        and float(last_index.get("ts") or 0.0)
        >= float(snapshot.get("last_index_at") or 0.0)
    ):
        reasons = list(snapshot.get("index_state_reasons") or [])
        if "index_update_failed" not in reasons:
            reasons.insert(0, "index_update_failed")
        snapshot["index_state"] = "stale"
        snapshot["index_state_reason"] = "index_update_failed"
        snapshot["index_state_reasons"] = reasons
    return {"ok": True, "health": snapshot}


__all__ = ["router"]
