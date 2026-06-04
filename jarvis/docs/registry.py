"""Doc-Registry: in-memory store + hot-reload + FTS5 index.

Follows the same pattern as ``jarvis/skills/registry.py:SkillRegistry`` —
identical debounce setup, identical thread/asyncio locks, identical watchdog
optionality. Differences:

- Multiple ``roots`` (skills live under one root; docs are scattered across
  the whole repo).
- FTS5 index in addition to the dict lookup (skills do not need full-text
  search).
- Index lookup key is ``slug`` (instead of ``name``); the synthetic slugs
  from ``loader._synth_frontmatter`` guarantee uniqueness per path.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .loader import discover_docs
from .schema import Doc, DocDiataxis, DocIndexReloaded, DocStatus
from .search import DocSearch, SearchResult

# watchdog is optional — same pattern as SkillRegistry
try:
    from watchdog.events import FileSystemEventHandler  # type: ignore
    from watchdog.observers import Observer  # type: ignore
    _HAVE_WATCHDOG = True
except Exception:  # pragma: no cover
    FileSystemEventHandler = object  # type: ignore
    Observer = None  # type: ignore
    _HAVE_WATCHDOG = False

log = logging.getLogger(__name__)


class DocRegistry:
    """Indexes all Markdown files under the configured roots.

    Thread-safe: ``asyncio.Lock`` for reload concurrency,
    ``threading.Lock`` for the dict mutex (watchdog callbacks fire from a
    different thread).
    """

    def __init__(
        self,
        roots: list[Path],
        index_db: Path,
        bus: Any | None = None,
        debounce_ms: int = 500,
    ) -> None:
        self.roots = [Path(r) for r in roots]
        self.bus = bus
        self._debounce_ms = debounce_ms
        self._docs: dict[str, Doc] = {}
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._observers: list[Any] = []
        self._pending_reload: float | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.search = DocSearch(index_db)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, slug: str) -> Doc | None:
        return self._docs.get(slug)

    def list(self) -> list[Doc]:
        return list(self._docs.values())

    def filter(
        self,
        diataxis: DocDiataxis | None = None,
        status: DocStatus | None = None,
        phase: str | None = None,
        tags: list[str] | None = None,
    ) -> list[Doc]:
        """Server-side filter — hand-rolled instead of SQL because the list
        is small (~50-100 entries) and is already sorted in memory."""
        out: list[Doc] = []
        tags_set = set(tags or [])
        for d in self._docs.values():
            fm = d.frontmatter
            if diataxis is not None and fm.diataxis != diataxis:
                continue
            if status is not None and fm.status != status:
                continue
            if phase is not None and fm.phase != phase:
                continue
            if tags_set and not tags_set.issubset(set(fm.tags)):
                continue
            out.append(d)
        return out

    def grouped_by_diataxis(self) -> dict[DocDiataxis, list[Doc]]:
        """Returns docs grouped by quadrant — template for the UI sidebar
        (group headers). Order: Tutorial -> How-To -> Concept -> Reference
        -> Troubleshooting -> ADR -> Unclassified."""
        groups: dict[DocDiataxis, list[Doc]] = defaultdict(list)
        for d in self._docs.values():
            groups[d.frontmatter.diataxis].append(d)
        for k in groups:
            groups[k].sort(key=lambda x: x.frontmatter.title.lower())
        return dict(groups)

    def search_query(
        self,
        q: str,
        diataxis: DocDiataxis | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        return self.search.query(
            q,
            diataxis=diataxis.value if diataxis else None,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def reload_sync(self) -> None:
        """Synchronous reload — used for bootstrap and tests."""
        docs = discover_docs(self.roots)
        with self._thread_lock:
            self._docs = {d.frontmatter.slug: d for d in docs}
        self.search.replace_all(docs)
        self._emit_reloaded()

    async def reload(self) -> None:
        """Async reload with lock — prevents concurrent re-indexings."""
        async with self._async_lock:
            loop = asyncio.get_event_loop()
            docs = await loop.run_in_executor(None, discover_docs, self.roots)
            with self._thread_lock:
                self._docs = {d.frontmatter.slug: d for d in docs}
            await loop.run_in_executor(None, self.search.replace_all, docs)
        self._emit_reloaded()

    def _emit_reloaded(self) -> None:
        if self.bus is None:
            return
        by_diataxis: dict[str, int] = defaultdict(int)
        errors = 0
        for d in self._docs.values():
            by_diataxis[d.frontmatter.diataxis.value] += 1
            if d.error is not None:
                errors += 1
        evt = DocIndexReloaded(
            total=len(self._docs),
            by_diataxis=dict(by_diataxis),
            errors=errors,
        )
        try:
            loop = self._loop or asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.bus.publish(evt), loop)
        except RuntimeError:  # pragma: no cover
            pass

    # ------------------------------------------------------------------
    # Hot-Reload (watchdog)
    # ------------------------------------------------------------------

    def start_watcher(self, loop: asyncio.AbstractEventLoop | None = None) -> bool:
        """Starts one filesystem watcher per root. Returns True when at least
        one watcher was started successfully."""
        if not _HAVE_WATCHDOG:
            log.info("watchdog not installed — no doc hot-reload")
            return False

        self._loop = loop or asyncio.get_event_loop()

        registry_self = self

        class _Handler(FileSystemEventHandler):  # type: ignore[misc]
            def on_any_event(self, event: Any) -> None:  # noqa: D401
                if getattr(event, "is_directory", False):
                    return
                src = str(getattr(event, "src_path", ""))
                if not (src.endswith(".md") or src.endswith(".markdown")):
                    return
                registry_self._schedule_reload()

        started_any = False
        for root in self.roots:
            if not root.exists():
                continue
            try:
                observer = Observer()  # type: ignore[operator]
                observer.schedule(_Handler(), str(root), recursive=True)
                observer.daemon = True
                observer.start()
                self._observers.append(observer)
                started_any = True
            except Exception as exc:  # noqa: BLE001
                log.warning("Doc-Watcher for %s failed: %s", root, exc)
        return started_any

    def stop_watcher(self) -> None:
        for obs in self._observers:
            try:
                obs.stop()
                obs.join(timeout=2.0)
            except Exception as exc:  # pragma: no cover  # noqa: BLE001
                log.debug("doc-watcher stop failed: %s", exc)
        self._observers.clear()

    def _schedule_reload(self) -> None:
        deadline = time.monotonic() + self._debounce_ms / 1000.0
        with self._thread_lock:
            self._pending_reload = deadline
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._debounced_reload(), self._loop)
        except RuntimeError:  # pragma: no cover
            pass

    async def _debounced_reload(self) -> None:
        await asyncio.sleep(self._debounce_ms / 1000.0)
        with self._thread_lock:
            deadline = self._pending_reload
            self._pending_reload = None
        if deadline is None:
            return
        if time.monotonic() + 0.001 < deadline:
            # A newer reload was requested in the meantime — skip this one
            return
        try:
            await self.reload()
        except Exception as exc:  # noqa: BLE001
            log.exception("doc reload failed: %s", exc)

    def close(self) -> None:
        self.stop_watcher()
        self.search.close()
