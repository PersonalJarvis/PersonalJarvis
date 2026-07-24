"""Local-folder connector: stream text files from a user-chosen directory.

This module also hosts the shared file-walk machinery reused by the
Obsidian-vault and normal-wiki connectors (both are "a folder of text
files with extra rules"). Connectors yield :class:`RawItem` only — no
store, no LLM, no embedding (design doc 02, hard rule 1).

Cursor / checkpoint contract (documented honestly):

- ``backfill(ctx, checkpoint)`` walks the tree in deterministic order
  (sorted by POSIX relative path). ``checkpoint`` is the ``external_id``
  of the last item the runtime persisted; files sorting at or before it
  are skipped so an interrupted backfill resumes instead of restarting.
- ``incremental(ctx, cursor)`` uses a cursor that is the highest
  ``st_mtime_ns`` seen so far, as a string. Every yielded item carries
  ``metadata["mtime_ns"]`` so the runtime can advance the cursor to the
  maximum of the yielded values. Only files with a modification time
  strictly greater than the cursor are re-yielded. An unparsable cursor
  logs an honest warning and re-yields everything — safe, because item
  writes are idempotent upserts on ``(source_id, external_id)``.
- Deletion detection: a file walk only sees files that still exist, so
  this connector cannot emit tombstone items from the walk itself.
  ``capabilities.deletes = True`` means deletions are detectable by the
  RUNTIME during a full backfill (reconcile pass, design doc 02): the
  runtime compares its stored ``external_id`` set for the source against
  the ids yielded by the walk and tombstones the difference. Incremental
  runs never detect deletions.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from jarvis.ultrawiki.types import (
    AuthKind,
    ConnectorCapabilities,
    ConnectorContext,
    IncrementalMode,
    RawItem,
)

log = logging.getLogger(__name__)

#: Matches an H1 markdown heading ("# Title") at the start of a line.
_H1_RE = re.compile(r"^# +(.+?)\s*$", re.MULTILINE)


def first_h1_heading(body: str) -> str:
    """Return the first ``# ...`` heading of a markdown body, or ``""``."""
    match = _H1_RE.search(body)
    return match.group(1).strip() if match else ""


def iso_utc_from_timestamp(seconds: float) -> str:
    """Render a POSIX timestamp as an ISO-8601 UTC string (second precision)."""
    return datetime.fromtimestamp(int(seconds), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_mtime_cursor(cursor: str | None, *, connector_id: str) -> int:
    """Parse an mtime-ns cursor; unparsable values honestly degrade to 0."""
    if cursor is None or cursor == "":
        return 0
    try:
        return int(cursor)
    except ValueError:
        log.warning(
            "%s: unusable incremental cursor %r; re-yielding everything "
            "(idempotent upserts make this safe)",
            connector_id,
            cursor,
        )
        return 0


class LocalFolderConnector:
    """Stream text files from ``ctx.config['root']`` as raw items.

    Config keys:

    - ``root`` (required): directory to walk.
    - ``extensions`` (optional): list of file extensions to include,
      default ``[".md", ".txt"]``. Entries may omit the leading dot.

    Files larger than :attr:`MAX_FILE_BYTES` are skipped with a log line.
    Bodies are read as UTF-8 with ``errors="replace"`` so a stray binary
    or wrongly-encoded file never aborts a walk.
    """

    id = "local-folder"
    label = "Local Folder"
    auth = AuthKind.LOCAL_PATH
    capabilities = ConnectorCapabilities(
        backfill=True,
        incremental=IncrementalMode.CURSOR,
        deletes=True,
    )

    DEFAULT_EXTENSIONS: tuple[str, ...] = (".md", ".txt")
    #: Directory names skipped in addition to hidden (dot-prefixed) ones.
    SKIP_DIR_NAMES: frozenset[str] = frozenset()
    MAX_FILE_BYTES: int = 2 * 1024 * 1024

    # ------------------------------------------------------------------
    # Protocol surface
    # ------------------------------------------------------------------

    async def backfill(
        self, ctx: ConnectorContext, checkpoint: str | None = None
    ) -> AsyncIterator[RawItem]:
        root = self._resolve_root(ctx)
        if root is None:
            return
        extensions = self._extensions(ctx)
        for path in self._sorted_files(root, extensions):
            external_id = self._external_id_for(root, path)
            if checkpoint and external_id <= checkpoint:
                continue
            item = self._item_for(root, path)
            if item is not None:
                yield item

    async def incremental(
        self, ctx: ConnectorContext, cursor: str | None = None
    ) -> AsyncIterator[RawItem]:
        root = self._resolve_root(ctx)
        if root is None:
            return
        threshold = parse_mtime_cursor(cursor, connector_id=self.id)
        extensions = self._extensions(ctx)
        for path in self._sorted_files(root, extensions):
            mtime_ns = self._mtime_ns(path)
            if mtime_ns is None or mtime_ns <= threshold:
                continue
            item = self._item_for(root, path)
            if item is not None:
                yield item

    # ------------------------------------------------------------------
    # Hooks (overridden by the Obsidian and normal-wiki subclasses)
    # ------------------------------------------------------------------

    def _resolve_root(self, ctx: ConnectorContext) -> Path | None:
        raw = ctx.config.get("root")
        if not raw:
            log.warning(
                "%s: source %s has no 'root' configured; yielding nothing",
                self.id,
                ctx.source_id,
            )
            return None
        root = Path(str(raw)).expanduser()
        if not root.is_dir():
            log.warning(
                "%s: configured root %s does not exist or is not a directory; yielding nothing",
                self.id,
                root,
            )
            return None
        return root

    def _extensions(self, ctx: ConnectorContext) -> tuple[str, ...]:
        raw = ctx.config.get("extensions")
        if not raw:
            return self.DEFAULT_EXTENSIONS
        normalized: list[str] = []
        for entry in raw:
            text = str(entry).strip().lower()
            if not text:
                continue
            normalized.append(text if text.startswith(".") else f".{text}")
        return tuple(normalized) or self.DEFAULT_EXTENSIONS

    def _sorted_files(self, root: Path, extensions: tuple[str, ...]) -> list[Path]:
        """All matching files under ``root``, sorted by POSIX relative path.

        Hidden directories and files (dot-prefixed) plus :attr:`SKIP_DIR_NAMES`
        are excluded. Symlinked directories are not followed.
        """
        matches: list[tuple[str, Path]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d not in self.SKIP_DIR_NAMES
            )
            base = Path(dirpath)
            for name in filenames:
                if name.startswith("."):
                    continue
                path = base / name
                if path.suffix.lower() not in extensions:
                    continue
                matches.append((path.relative_to(root).as_posix(), path))
        matches.sort(key=lambda pair: pair[0])
        return [path for _rel, path in matches]

    def _external_id_for(self, root: Path, path: Path) -> str:
        return path.relative_to(root).as_posix()

    def _title_for(self, path: Path, body: str) -> str:
        return path.stem

    # ------------------------------------------------------------------
    # Item construction (sync helpers keep blocking I/O out of async frames)
    # ------------------------------------------------------------------

    def _mtime_ns(self, path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except OSError as exc:
            log.debug("%s: stat failed for %s: %s", self.id, path, exc)
            return None

    def _item_for(self, root: Path, path: Path) -> RawItem | None:
        try:
            stat = path.stat()
        except OSError as exc:
            log.debug("%s: stat failed for %s: %s", self.id, path, exc)
            return None
        if stat.st_size > self.MAX_FILE_BYTES:
            log.info(
                "%s: skipping %s (%d bytes exceeds the %d-byte limit)",
                self.id,
                path,
                stat.st_size,
                self.MAX_FILE_BYTES,
            )
            return None
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.debug("%s: read failed for %s: %s", self.id, path, exc)
            return None
        return RawItem(
            external_id=self._external_id_for(root, path),
            body=body,
            permalink=path.resolve().as_uri(),
            timestamp_utc=iso_utc_from_timestamp(stat.st_mtime),
            title=self._title_for(path, body),
            metadata={"mtime_ns": stat.st_mtime_ns, "size_bytes": stat.st_size},
        )


__all__ = [
    "LocalFolderConnector",
    "first_h1_heading",
    "iso_utc_from_timestamp",
    "parse_mtime_cursor",
]
