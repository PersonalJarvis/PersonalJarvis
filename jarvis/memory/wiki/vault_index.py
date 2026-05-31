"""In-memory index of the wiki vault.

The vault on disk holds four kinds of pages (entities, concepts, projects,
sessions). The ``VaultIndex`` walks those four directories on demand,
parses each markdown file through the ``PageRepository`` (Instance A) and
keeps two dictionaries:

* ``_by_slug``     — ``{slug -> WikiPage}`` for direct lookup.
* ``_backlinks``   — ``{slug -> [pages that contain a wikilink to slug]}``.

Cheap stale-detection: every page entry carries the ``st_mtime`` it was
parsed at. When a caller asks for a page (``find_by_slug``,
``pages_by_type``, ``backlinks_to``) the index re-stats the underlying
file. If the mtime has advanced beyond what is cached the page is
re-parsed on the fly. This keeps the index honest when the user edits
in Obsidian without forcing a full ``scan()``.

Write surface for this module: NONE on the page directories themselves.
``log.md`` is owned by ``LogWriter``, ``index.md`` by ``IndexBuilder``.
This class is read-only over the four page directories.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from jarvis.memory.wiki.protocols import PageRepository, WikiPage

log = logging.getLogger(__name__)

# Page-type directories that VaultIndex scans. Order matters for
# ``render_index_md`` and ``_walk_pages``; keep alphabetical-by-type
# semantics consistent everywhere.
PAGE_TYPE_DIRS: tuple[tuple[str, str], ...] = (
    ("entity", "entities"),
    ("concept", "concepts"),
    ("project", "projects"),
    ("session", "sessions"),
)

# Directories the index must never crawl. Archive and attachments may be
# large and never participate in slug/backlink resolution.
SKIP_DIRS: frozenset[str] = frozenset({"_archive", "attachments"})


@dataclass(slots=True)
class _IndexEntry:
    """Cached entry — the parsed page and the mtime it was parsed at."""

    page: Any  # WikiPage; Any keeps the module free of an Instance A hard-dep.
    mtime_ns: int


@dataclass(slots=True)
class VaultIndex:
    """Whole-vault index over slugs and backlinks.

    Parameters
    ----------
    repo:
        Instance A's ``PageRepository`` — used to parse every markdown
        file into a ``WikiPage``. The repository is the single source of
        truth for "what is a valid page".
    """

    repo: Any  # PageRepository — Any avoids an Instance A hard-import.
    _root: Path | None = field(default=None, init=False)
    _by_slug: dict[str, _IndexEntry] = field(default_factory=dict, init=False)
    _slug_to_path: dict[str, Path] = field(default_factory=dict, init=False)
    _backlinks: dict[str, list[str]] = field(default_factory=dict, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    # ------------------------------------------------------------------
    # Public API — async by contract (Protocol VaultIndex in protocols.py)
    # ------------------------------------------------------------------

    async def scan(self, vault_root: Path) -> None:
        """Walk the four page directories and (re)build the index.

        Files that fail to parse or whose ``is_schema_valid`` is False
        are skipped with a warning — they are not added to the index but
        do not crash the scan. Missing directories are tolerated (a
        fresh vault may not have a ``sessions/`` yet).
        """
        async with self._lock:
            self._root = Path(vault_root)
            self._by_slug.clear()
            self._slug_to_path.clear()
            self._backlinks.clear()

            for _, dirname in PAGE_TYPE_DIRS:
                page_dir = self._root / dirname
                if not page_dir.exists():
                    continue
                for md_path in sorted(page_dir.glob("*.md")):
                    if md_path.name.startswith("."):
                        continue
                    await self._load_one(md_path)

    def pages_by_type(self, page_type: str) -> list[Any]:
        """Return all valid pages of one type, alphabetically by slug.

        Refreshes any stale entries before answering. Unknown
        ``page_type`` returns an empty list (no raise) so callers can
        iterate defensively.
        """
        self._refresh_stale_sync()
        result = [
            entry.page
            for entry in self._by_slug.values()
            if getattr(entry.page, "page_type", "") == page_type
        ]
        result.sort(key=lambda p: getattr(p, "slug", ""))
        return result

    def find_by_slug(self, slug: str) -> Any | None:
        """Return the page with ``slug`` or None.

        Re-parses the underlying file if its mtime changed since the
        last load. New files in the vault (added since ``scan``) are not
        picked up here — call ``scan`` again for that.
        """
        self._refresh_stale_sync(only_slug=slug)
        entry = self._by_slug.get(slug)
        return entry.page if entry else None

    def backlinks_to(self, slug: str) -> list[Any]:
        """Return all pages whose body contains a wikilink to ``slug``.

        Returned in stable alphabetical order by source slug. Stale
        pages are refreshed before the answer; the backlink index is
        rebuilt as a side effect when any source page is re-parsed.
        """
        self._refresh_stale_sync()
        sources = self._backlinks.get(slug, [])
        out: list[Any] = []
        for src_slug in sorted(set(sources)):
            entry = self._by_slug.get(src_slug)
            if entry is not None:
                out.append(entry.page)
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load_one(self, path: Path) -> None:
        """Parse ``path`` via the repo and register it in the index.

        Silently skips files the repo flags as ``is_schema_valid=False``
        or whose ``slug`` is missing. Updates the reverse-link table.
        """
        try:
            page = await self.repo.load(path)
        except Exception as exc:  # noqa: BLE001 — broad on purpose
            log.warning("VaultIndex: failed to parse %s: %s", path, exc)
            return
        if not getattr(page, "is_schema_valid", False):
            log.debug("VaultIndex: skipping invalid page %s", path)
            return
        slug = getattr(page, "slug", "")
        if not slug:
            log.debug("VaultIndex: page %s has no slug, skipping", path)
            return
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError as exc:
            log.warning("VaultIndex: stat() failed for %s: %s", path, exc)
            return
        # Drop any prior entry for the same slug before re-indexing so a
        # rename does not leave a ghost in the backlink table.
        self._drop_entry(slug)
        self._by_slug[slug] = _IndexEntry(page=page, mtime_ns=mtime_ns)
        self._slug_to_path[slug] = path
        for target in getattr(page, "wikilinks", ()):
            target_slug = _slug_of_link(target)
            if target_slug:
                self._backlinks.setdefault(target_slug, []).append(slug)

    def _drop_entry(self, slug: str) -> None:
        """Remove a slug from both the slug table and the backlink table."""
        self._by_slug.pop(slug, None)
        self._slug_to_path.pop(slug, None)
        for target, sources in list(self._backlinks.items()):
            cleaned = [s for s in sources if s != slug]
            if cleaned:
                self._backlinks[target] = cleaned
            else:
                self._backlinks.pop(target, None)

    def _refresh_stale_sync(self, *, only_slug: str | None = None) -> None:
        """Cheaply re-parse pages whose on-disk mtime advanced.

        Synchronous because callers (``find_by_slug`` etc.) live in
        synchronous code paths. The repo's ``parse`` is itself sync-fast;
        we use ``asyncio.run_coroutine_threadsafe``-style fallback only
        if a running loop forbids ``asyncio.run``.
        """
        if self._root is None:
            return
        targets: list[str]
        if only_slug is not None:
            targets = [only_slug] if only_slug in self._by_slug else []
        else:
            targets = list(self._by_slug.keys())

        for slug in targets:
            path = self._slug_to_path.get(slug)
            if path is None:
                continue
            try:
                mtime_ns = path.stat().st_mtime_ns
            except FileNotFoundError:
                self._drop_entry(slug)
                continue
            except OSError as exc:  # pragma: no cover
                log.warning("VaultIndex: stat() failed for %s: %s", path, exc)
                continue
            cached = self._by_slug.get(slug)
            if cached is not None and cached.mtime_ns == mtime_ns:
                continue
            # Stale — re-parse via _load_one, which re-reads through the repo
            # and rebuilds the slug + backlink tables in one place. (Calling
            # repo.load first and discarding the result was pure double work.)
            try:
                _run_coro_sync(self._load_one(path))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "VaultIndex: stale-refresh failed for %s: %s", path, exc
                )


def _slug_of_link(link: str) -> str:
    """Canonicalise a wikilink target to its bare slug.

    ``entities/alex`` and ``concepts/awareness-layer`` both reduce to
    the trailing path segment. A plain ``alex`` is returned unchanged.
    An aliased form (``slug|alias``) drops the alias.
    """
    bare = link.split("|", 1)[0].strip()
    if "/" in bare:
        bare = bare.rsplit("/", 1)[1]
    if bare.endswith(".md"):
        bare = bare[:-3]
    return bare


def _run_coro_sync(coro: Any) -> Any:
    """Run an awaitable from synchronous code.

    The vault index exposes synchronous accessors (``find_by_slug`` etc.)
    but the page repository is async. We bridge with ``asyncio.run`` when
    no loop is running, and with a private loop in a dedicated thread
    otherwise — never block the caller's running loop.
    """
    if not asyncio.iscoroutine(coro):
        return coro
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # A loop is running on this thread; offload to a fresh one in a
    # worker thread. This path is rare in tests; production callers run
    # on a thread without a loop.
    import threading

    result: dict[str, Any] = {}

    def _runner() -> None:
        result["value"] = asyncio.run(coro)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    return result.get("value")
