"""FastAPI routes that expose the on-disk Obsidian vault as JSON (Phase B3, Agent A).

Five read-only endpoints power the Desktop App's new "Wiki" sidebar tab:

* ``GET /api/wiki/tree``            — folder structure with file metadata.
* ``GET /api/wiki/page/{slug}``     — one page (frontmatter + body + wikilinks).
* ``GET /api/wiki/graph``           — nodes + edges built from outbound wikilinks.
* ``GET /api/wiki/backlinks/{slug}``— reverse-link list with body snippets.
* ``GET /api/wiki/search``          — keyword search via ``VaultSearch`` (B5).

Two later additions follow the same style: ``GET /api/wiki/telemetry`` (B8.7,
in-memory counter snapshot) and ``GET /api/wiki/health`` (spec A5, bootstrap /
write / chain-failure / journal-backlog status).

All endpoints follow the existing house style: HTTP 200 with the envelope
``{"ok": True, ...}`` on success and ``{"ok": False, "error": "..."}`` on
logical errors. HTTP 404 stays reserved for unknown routes; HTTP 500 only
fires on unhandled exceptions.

The module reuses ``PageRepository`` (B1, ``jarvis/memory/wiki/page.py``),
``VaultIndex`` (B1, ``jarvis/memory/wiki/vault_index.py``) and
``VaultSearch`` (B5, ``jarvis/memory/wiki/search.py``). It is a pure view
layer — no writes, no brain, no event-bus side effects.

The vault root is read from ``app.state.config.wiki_integration.vault_root``.
Tests can override the path by setting that attribute directly before the
TestClient context, or by passing a config object whose ``wiki_integration``
section already points at a temporary directory.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request

from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.protocols import WikiPage
from jarvis.memory.wiki.search import VaultSearch
from jarvis.memory.wiki.telemetry import telemetry as _telemetry
from jarvis.memory.wiki.vault_index import VaultIndex
from jarvis.memory.wiki.vault_root import resolve_vault_root
from jarvis.memory.wiki.wikilink import _canonicalise as _canonicalise_link  # type: ignore[attr-defined]

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wiki", tags=["wiki"])

# Directories that VaultIndex / page tree walks. Order matters for stable UI.
_PAGE_DIRS: tuple[tuple[str, str], ...] = (
    ("entities", "entity"),
    ("concepts", "concept"),
    ("projects", "project"),
    ("sessions", "session"),
)

# Snippet window (chars) for backlink context extraction around the wikilink.
_BACKLINK_SNIPPET_RADIUS = 80
_BACKLINK_SNIPPET_MAX = 200


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


def _kind_of(page: WikiPage) -> str:
    """Schema kind (entity/concept/project/session/meta) of a page."""
    return page.page_type or "meta"


def _human_title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


# ----------------------------------------------------------------------
# Walking helpers (off-loop)
# ----------------------------------------------------------------------


def _walk_tree_sync(vault_root: Path) -> tuple[list[dict[str, Any]], int, float | None]:
    """Walk the four page directories. Returns (folders, total_pages, log_mtime)."""
    folders: list[dict[str, Any]] = []
    total_pages = 0
    for dirname, kind in _PAGE_DIRS:
        folder = vault_root / dirname
        files: list[dict[str, Any]] = []
        if folder.is_dir():
            for md_path in sorted(folder.glob("*.md")):
                if md_path.name.startswith("."):
                    continue
                try:
                    stat = md_path.stat()
                except OSError as exc:
                    log.warning("wiki_route_walk_failed: %s — %s", md_path, exc)
                    continue
                title = _title_from_disk(md_path)
                files.append(
                    {
                        "slug": md_path.stem,
                        "title": title,
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                    }
                )
        total_pages += len(files)
        folders.append(
            {
                "name": dirname,
                "kind": kind,
                "count": len(files),
                "files": files,
            }
        )

    log_mtime: float | None = None
    log_path = vault_root / "log.md"
    if log_path.is_file():
        try:
            log_mtime = log_path.stat().st_mtime
        except OSError:
            log_mtime = None
    return folders, total_pages, log_mtime


def _title_from_disk(md_path: Path) -> str:
    """Cheap H1 extraction without a full parse. Falls back to humanised stem."""
    try:
        # Read only the first ~2 KB; an H1 always sits at the top.
        head = md_path.read_text(encoding="utf-8", errors="replace")[:2048]
    except OSError:
        return _human_title_from_slug(md_path.stem)
    in_frontmatter = False
    for raw_line in head.splitlines():
        line = raw_line.rstrip()
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        stripped = line.lstrip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return _human_title_from_slug(md_path.stem)


async def _build_index(vault_root: Path) -> VaultIndex:
    """Build a freshly scanned ``VaultIndex`` over ``vault_root``.

    Reading is delegated to ``asyncio.to_thread`` inside the repository, so
    this coroutine can run on the event loop without blocking.
    """
    repo = MarkdownPageRepository()
    index = VaultIndex(repo=repo)
    await index.scan(vault_root)
    return index


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
        folders, total_pages, log_mtime = await asyncio.to_thread(
            _walk_tree_sync, vault_root
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_route_failed route=/tree error=%s", exc)
        return {"ok": False, "error": "tree walk failed"}

    total_links = 0
    try:
        index = await _build_index(vault_root)
        for _, kind in _PAGE_DIRS:
            for page in index.pages_by_type(kind):
                total_links += len(getattr(page, "wikilinks", ()))
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_route_failed route=/tree (link count) error=%s", exc)
        # Don't fail the whole response if only the link count is degraded.
        total_links = 0

    last_curator_run = _format_mtime(log_mtime)

    return {
        "ok": True,
        "vault_root": vault_str,
        "folders": folders,
        "stats": {
            "total_pages": total_pages,
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
    title = page.frontmatter.get("title") or _title_of(page)
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
        "wikilinks": [_canonicalise_link(link).rsplit("/", 1)[-1] for link in page.wikilinks],
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
        index = await _build_index(vault_root)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_route_failed route=/graph error=%s", exc)
        return {"ok": False, "error": "graph build failed"}

    pages: list[WikiPage] = []
    for _, kind in _PAGE_DIRS:
        pages.extend(index.pages_by_type(kind))

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
                "title": page.frontmatter.get("title") or _title_of(page),
            }
        )

    edges: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    for page in pages:
        body = page.body
        for raw_link in page.wikilinks:
            target_slug = _canonicalise_link(raw_link).rsplit("/", 1)[-1]
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
        index = await _build_index(vault_root)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_route_failed route=/backlinks/%s error=%s", slug, exc)
        return {"ok": False, "error": "backlinks lookup failed"}

    sources: list[WikiPage] = index.backlinks_to(slug)
    out: list[dict[str, Any]] = []
    for src in sources:
        snippet = _link_context(src.body, slug, slug)
        out.append(
            {
                "slug": src.slug,
                "title": src.frontmatter.get("title") or _title_of(src),
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
    """Locate a page by slug across the four page directories.

    Falls back from the ``VaultIndex`` (which skips schema-invalid pages)
    to a direct file probe so the UI can still render malformed pages with
    a warning banner.
    """
    index = await _build_index(vault_root)
    page = index.find_by_slug(slug)
    if page is not None:
        return page

    # Fallback: parse a schema-invalid page directly so the UI can show it.
    repo = MarkdownPageRepository()
    for dirname, _ in _PAGE_DIRS:
        candidate = vault_root / dirname / f"{slug}.md"
        if candidate.is_file():
            try:
                return await repo.load(candidate)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "wiki_route_failed route=/page/%s parse error=%s",
                    slug,
                    exc,
                )
                return None
    return None


def _relative_to_vault(path: Path, vault_root: Path) -> str:
    """POSIX-style path relative to the vault root, or the bare name as fallback."""
    try:
        rel = path.resolve().relative_to(vault_root.resolve())
    except (ValueError, OSError):
        return path.name
    return rel.as_posix()


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
    from datetime import datetime, timezone

    try:
        return (
            datetime.fromtimestamp(mtime, tz=timezone.utc)
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

    snapshot = _health.snapshot()
    vault_root = _resolve_vault_root(request)
    config = getattr(request.app.state, "config", None)
    data_dir = getattr(getattr(config, "memory", None), "data_dir", "./data")
    db_path = resolve_wiki_db_path(data_dir)

    def _persistent_state() -> dict[str, Any]:
        state: dict[str, Any] = {
            "journal_backlog": 0,
            "indexed_pages": 0,
            "last_write": None,
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
            except sqlite3.OperationalError:
                pass
            try:
                state["indexed_pages"] = int(
                    conn.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0]
                )
            except sqlite3.OperationalError:
                pass
        finally:
            conn.close()
        return state

    persistent = await asyncio.to_thread(_persistent_state)
    if snapshot["last_write"] is None and persistent["last_write"] is not None:
        snapshot["last_write"] = persistent["last_write"]
    snapshot["journal_backlog"] = persistent["journal_backlog"]
    snapshot["indexed_pages"] = persistent["indexed_pages"]
    snapshot["vault_pages"] = (
        _visible_markdown_count(vault_root)
        if vault_root is not None and vault_root.is_dir()
        else 0
    )
    snapshot["index_state"] = (
        "ok"
        if snapshot["indexed_pages"] == snapshot["vault_pages"]
        else "stale"
    )
    return {"ok": True, "health": snapshot}


__all__ = ["router"]
