"""Local-folder connector: stream text files from a user-chosen directory.

This module also hosts the shared file-walk machinery reused by the
Obsidian-vault and normal-wiki connectors (both are "a folder of text
files with extra rules"). Connectors yield :class:`RawItem` only — no
store, no LLM, no embedding (design doc 02, hard rule 1).

Cursor / checkpoint contract (documented honestly):

- ``backfill(ctx, checkpoint)`` walks the tree in deterministic order,
  sorted by ``external_id`` — the SAME string the checkpoint holds, which
  is what makes "skip everything at or before the checkpoint" correct for
  every subclass (a subclass that derives its ids differently, like the
  normal-wiki connector stripping ``.md``, orders by ITS ids too).
  ``checkpoint`` is the ``external_id`` of the last item the runtime
  persisted; files sorting at or before it are skipped so an interrupted
  backfill resumes instead of restarting.
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

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

from jarvis.ultrawiki.extract import (
    DOCUMENT_EXTENSIONS,
    MEDIA_EXTENSIONS,
    MEDIA_KINDS,
    detect_kind,
    extract_text,
)
from jarvis.ultrawiki.extract import MAX_DOCUMENT_BYTES as _MAX_DOCUMENT_BYTES
from jarvis.ultrawiki.media import MediaRef
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

#: Shell-prompt decoration a pasted path arrives with. ``C:\\Users\\Someone>``
#: is what an interactive prompt PRINTS; the ``>`` is the prompt, not the
#: folder. Same for the POSIX ``$`` / ``#`` and PowerShell's ``PS `` prefix.
_PROMPT_PREFIX_RE = re.compile(r"^(?:PS|pwsh|bash|cmd)\s+", re.IGNORECASE)
_PROMPT_SUFFIX_RE = re.compile(r"\s*[>$#]+\s*$")


class LocalFolderRootError(RuntimeError):
    """The configured folder cannot be walked — it is missing or not a folder.

    Raised instead of yielding nothing, so the run FAILS and the reason
    reaches the source card as ``last_error``. Returning empty here is what
    let a source pasted with a shell prompt still in the path ("...>") report
    a successful import of zero items, with the reason only ever reaching a
    log file nobody reads.
    """


def normalize_root_path(raw: str) -> str:
    """Strip what a human's copy-paste brings along, never a real folder.

    Handles surrounding quotes, stray whitespace, and shell-prompt decoration
    (``PS C:\\Users\\Someone>``, ``/home/someone$``). Touches the filesystem:
    a path that EXISTS is returned untouched, so a directory legitimately
    named ``weird>`` (legal on POSIX) is never cleaned away. Blocking — call
    it from a worker thread on any async path.
    """
    text = str(raw).strip()
    if not text:
        return text
    for quote in ('"', "'"):
        if len(text) >= 2 and text.startswith(quote) and text.endswith(quote):
            text = text[1:-1].strip()
    if _path_exists(text):
        return text
    candidate = _PROMPT_PREFIX_RE.sub("", text).strip()
    candidate = _PROMPT_SUFFIX_RE.sub("", candidate).strip()
    return candidate or text


def _path_exists(text: str) -> bool:
    """``True`` if something is at ``text``; never raises on a bogus path.

    A string carrying shell decoration can be invalid for the platform's path
    API (``ValueError``/``OSError`` on Windows for characters like ``>``),
    which must read as "not a path", not as a crash.
    """
    try:
        return Path(text).expanduser().exists()
    except (OSError, ValueError):
        return False


#: Directory names never walked, on top of every dot-prefixed one. These hold
#: machine output, not knowledge: dependency trees, byte-code caches, and the
#: per-OS application-data folders. Importing a home folder without this list
#: buries the user's own files under orders of magnitude of vendored text.
#: Deliberately per-OS-complete rather than Windows-shaped (charter §3):
#: ``AppData`` is Windows, ``Library`` is macOS, ``.cache`` (dot-prefixed) is
#: Linux, and the dependency names are the same everywhere.
NOISE_DIR_NAMES: frozenset[str] = frozenset(
    {
        "node_modules",
        "__pycache__",
        "site-packages",
        "dist-packages",
        "bower_components",
        "vendor",
        "venv",
        "AppData",
        "Library",
        "__MACOSX",
        "$RECYCLE.BIN",
        "System Volume Information",
    }
)

def _own_data_dirs() -> tuple[Path, ...]:
    """The app's OWN data directories, resolved once per walk. Never raises.

    Why a folder import needs to know this (2026-07-27 forensic). A user
    pointed the folder connector at the directory the app itself lives in and
    got a 236 131-item corpus, of which 218 419 were wake-word debug clips
    from ``data/wake_debug`` — 92 % of everything the knowledge base held was
    the app's own recordings of itself. Each one was queued for a
    transcription and a summary, which is a backlog measured in weeks made
    entirely of noise.

    Matched by resolved PATH rather than by folder name, and that distinction
    is the whole point: ``data`` is far too common a name to blocklist. Real
    people keep research data, exports and project files in folders called
    exactly that, and a name rule would silently swallow them. Only the
    directory this installation actually writes to is skipped.

    Both candidates are returned because they can genuinely differ: the
    project-root ``data/`` and the per-user fallback used when that path is
    read-only (headless installs, site-packages, ``JARVIS_DATA_DIR``).
    """
    found: list[Path] = []
    try:
        from jarvis.core import config as core_config  # noqa: PLC0415 — lazy (AP-26)

        candidates = [getattr(core_config, "DATA_DIR", None)]
        resolver = getattr(core_config, "_resolve_writable_data_dir", None)
        if callable(resolver):
            candidates.append(resolver())
    except Exception:  # noqa: BLE001 — an unresolvable data dir must not stop a walk
        log.debug("%s: could not resolve the app data dir", __name__, exc_info=True)
        return ()
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            found.append(Path(candidate).resolve())
        except (OSError, ValueError):
            continue
    return tuple(dict.fromkeys(found))


def _is_own_data_dir(path: Path, own_data: tuple[Path, ...]) -> bool:
    """Is *path* one of the app's own data directories? Never raises."""
    if not own_data:
        return False
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return False
    return any(resolved == own for own in own_data)


def _is_linked_git_worktree(path: Path) -> bool:
    """Whether *path* is a linked Git worktree nested below the chosen root.

    Git marks linked worktrees with a ``.git`` file that points back to the
    primary checkout. They are machine-created copies of content already
    present elsewhere and can multiply a Desktop import by every active task.
    A primary checkout has a ``.git`` directory and is intentionally retained.
    Selecting a worktree itself as the source remains an explicit opt-in,
    because the walker filters children and never rejects its root.
    """
    try:
        return (path / ".git").is_file()
    except OSError:
        # An unreadable marker cannot prove that this is a linked worktree.
        return False


#: Text-shaped suffixes read as UTF-8. Deliberately an ALLOWLIST: a folder
#: full of photos, videos and installers must import as nothing rather than
#: as megabytes of replacement characters. Grouped for review, flattened
#: below — prose and notes, structured text, markup, then source code.
_PROSE_EXTENSIONS = (".md", ".markdown", ".txt", ".text", ".rst", ".org", ".tex")
_DATA_EXTENSIONS = (
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env.example",
)
_MARKUP_EXTENSIONS = (".html", ".htm", ".xhtml", ".xml", ".svg", ".css", ".scss")
_CODE_EXTENSIONS = (
    ".py",
    ".pyi",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cs",
    ".m",
    ".r",
    ".lua",
    ".pl",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".psm1",
    ".bat",
    ".cmd",
    ".sql",
    ".graphql",
    ".proto",
    ".dockerfile",
    ".gradle",
)

#: Formats whose SIZE says nothing about how much text they hold. A 30 MB PDF
#: is mostly layout and images around a few pages of prose, so judging it by
#: the plain-text ceiling would drop every real document.
_LARGE_DOCUMENT_KINDS: frozenset[str] = frozenset(
    {"pdf", "docx", "xlsx", "pptx", "odf", "epub", "rtf"}
)

#: Absolute ceiling for anything read as a whole document, media excluded.
#: Far above the text limit, far below what could exhaust a small VPS. Owned
#: by the extraction service so a folder walk and a cloud adapter cannot drift
#: apart on the same question; re-exported here for the callers that had it.
MAX_DOCUMENT_BYTES = _MAX_DOCUMENT_BYTES

#: Files stat'ed + read per worker-thread hop. Filesystem I/O must never run
#: on the event loop (it also serves voice and chat), but one hop per file
#: would pay the handoff thousands of times over a real vault — a small batch
#: amortizes it while keeping each hop short.
READ_BATCH = 32


def _batched(values: Sequence[Path], size: int) -> list[Sequence[Path]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


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
        refresh_interval_s=300.0,
        reconcile_interval_s=86_400.0,
    )

    DEFAULT_EXTENSIONS: tuple[str, ...] = (
        *_PROSE_EXTENSIONS,
        *_DATA_EXTENSIONS,
        *_MARKUP_EXTENSIONS,
        *_CODE_EXTENSIONS,
        *sorted(DOCUMENT_EXTENSIONS),
        *sorted(MEDIA_EXTENSIONS),
    )
    #: Directory names skipped in addition to hidden (dot-prefixed) ones.
    SKIP_DIR_NAMES: frozenset[str] = NOISE_DIR_NAMES
    MAX_FILE_BYTES: int = 2 * 1024 * 1024

    # ------------------------------------------------------------------
    # Protocol surface
    # ------------------------------------------------------------------

    async def backfill(
        self, ctx: ConnectorContext, checkpoint: str | None = None
    ) -> AsyncIterator[RawItem]:
        root = await asyncio.to_thread(self._resolve_root, ctx)
        if root is None:
            return
        extensions = self._extensions(ctx)
        paths = await asyncio.to_thread(
            self._sorted_files, root, extensions, self._skip_dirs(ctx)
        )
        pending = [
            path
            for path in paths
            if not (checkpoint and self._external_id_for(root, path) <= checkpoint)
        ]
        for batch in _batched(pending, READ_BATCH):
            for item in await asyncio.to_thread(self._items_for_paths, root, batch):
                yield item

    async def incremental(
        self, ctx: ConnectorContext, cursor: str | None = None
    ) -> AsyncIterator[RawItem]:
        root = await asyncio.to_thread(self._resolve_root, ctx)
        if root is None:
            return
        threshold = parse_mtime_cursor(cursor, connector_id=self.id)
        extensions = self._extensions(ctx)
        paths = await asyncio.to_thread(
            self._sorted_files, root, extensions, self._skip_dirs(ctx)
        )
        for batch in _batched(paths, READ_BATCH):
            items = await asyncio.to_thread(
                self._items_for_paths, root, batch, min_mtime_ns=threshold
            )
            for item in items:
                yield item

    # ------------------------------------------------------------------
    # Hooks (overridden by the Obsidian and normal-wiki subclasses)
    # ------------------------------------------------------------------

    def _resolve_root(self, ctx: ConnectorContext) -> Path | None:
        """The folder to walk, or ``None`` when none is configured at all.

        A CONFIGURED path that cannot be walked raises
        :class:`LocalFolderRootError` rather than returning ``None``: the run
        must fail so the reason lands on the card. An ABSENT ``root`` stays a
        warning + empty walk — it cannot come from the UI (the field is
        required) and subclasses resolve their root elsewhere.
        """
        raw = ctx.config.get("root")
        if not raw:
            log.warning(
                "%s: source %s has no 'root' configured; yielding nothing",
                self.id,
                ctx.source_id,
            )
            return None
        cleaned = normalize_root_path(str(raw))
        if cleaned != str(raw).strip():
            log.info(
                "%s: read the configured folder as %r (the pasted value %r "
                "carried shell-prompt decoration)",
                self.id,
                cleaned,
                str(raw),
            )
        try:
            root = Path(cleaned).expanduser()
            is_dir = root.is_dir()
            exists = root.exists()
        except (OSError, ValueError) as exc:
            raise LocalFolderRootError(
                f"{cleaned!r} is not a usable folder path on this system "
                f"({type(exc).__name__}). Pick the folder again."
            ) from exc
        if is_dir:
            return root
        if exists:
            raise LocalFolderRootError(
                f"{root} is a file, not a folder. Point this source at the folder that CONTAINS it."
            )
        raise LocalFolderRootError(
            f"There is no folder at {root}. Check the path: a value copied "
            f"from a terminal often carries the prompt with it."
        )

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

    def _skip_dirs(self, ctx: ConnectorContext) -> frozenset[str]:
        """Folder names not walked: the built-in noise list PLUS the user's.

        Lower-cased for matching, because a list typed on Windows or macOS
        (case-insensitive filesystems) has to keep working when the same
        config is opened on Linux. Additive by design — naming one folder must
        never switch the sensible defaults off.
        """
        names = {name.lower() for name in self.SKIP_DIR_NAMES}
        for entry in ctx.config.get("exclude") or ():
            text = str(entry).strip().lower()
            if text:
                names.add(text)
        return frozenset(names)

    def _sorted_files(
        self,
        root: Path,
        extensions: tuple[str, ...],
        skip_dirs: frozenset[str] | None = None,
    ) -> list[Path]:
        """All matching files under ``root``, sorted by ``external_id``.

        The sort key is deliberately :meth:`_external_id_for`, not the raw
        relative path: the backfill checkpoint stores an ``external_id``, so
        resuming can only be correct while walk order and checkpoint order are
        the same string ordering.

        Hidden directories and files (dot-prefixed) plus ``skip_dirs`` (which
        defaults to :attr:`SKIP_DIR_NAMES`) are excluded. Symlinked
        directories are not followed. Blocking — call it through
        ``asyncio.to_thread``.
        """
        skip = (
            skip_dirs
            if skip_dirs is not None
            else frozenset(name.lower() for name in self.SKIP_DIR_NAMES)
        )
        own_data = _own_data_dirs()
        matches: list[tuple[str, Path]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            base = Path(dirpath)
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not d.startswith(".")
                and d.lower() not in skip
                and not _is_own_data_dir(base / d, own_data)
                and not _is_linked_git_worktree(base / d)
            )
            for name in filenames:
                if name.startswith("."):
                    continue
                path = base / name
                if path.suffix.lower() not in extensions:
                    continue
                matches.append((self._external_id_for(root, path), path))
        matches.sort(key=lambda pair: pair[0])
        return [path for _external_id, path in matches]

    def _external_id_for(self, root: Path, path: Path) -> str:
        return path.relative_to(root).as_posix()

    def _title_for(self, path: Path, body: str) -> str:
        return path.stem

    # ------------------------------------------------------------------
    # Item construction — every helper below BLOCKS on the filesystem and is
    # only ever called through asyncio.to_thread from the generators above.
    # ------------------------------------------------------------------

    def _items_for_paths(
        self,
        root: Path,
        paths: Sequence[Path],
        *,
        min_mtime_ns: int | None = None,
    ) -> list[RawItem]:
        """Stat + read one batch of files in a worker thread.

        With ``min_mtime_ns`` set (the incremental cursor) files that were not
        modified after it are skipped before their body is read at all.
        """
        items: list[RawItem] = []
        for path in paths:
            if min_mtime_ns is not None:
                mtime_ns = self._mtime_ns(path)
                if mtime_ns is None or mtime_ns <= min_mtime_ns:
                    continue
            item = self._item_for(root, path)
            if item is not None:
                items.append(item)
        return items

    def _mtime_ns(self, path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except OSError as exc:
            log.debug("%s: stat failed for %s: %s", self.id, path, exc)
            return None

    def _item_for(self, root: Path, path: Path) -> RawItem | None:
        """One file as an item, whatever kind of file it turns out to be.

        Everything goes through the one shared extractor. Three outcomes:

        * **text came out** — the ordinary case.
        * **it is a photo, recording or video** — stored with what the file
          says about ITSELF (name, folder, capture date, camera, place) so it
          is findable immediately, and marked for the enrichment stage to
          describe later. Deliberately not skipped: a photo library is the
          largest and most personal thing most people own.
        * **text could exist but did not come out** — a scan with no OCR
          layer, a format needing an uninstalled extra. Stored with the honest
          reason, so a later run can reclaim it rather than the file silently
          never having existed.
        """
        try:
            stat = path.stat()
        except OSError as exc:
            log.debug("%s: stat failed for %s: %s", self.id, path, exc)
            return None

        kind = self._detect(path)
        if kind in MEDIA_KINDS:
            return self._media_item(root, path, stat, kind)

        # The size ceiling is a TEXT ceiling and is checked only here: a photo
        # or video is never read whole (only its header is), so judging it by
        # the plain-text limit would drop every real one.
        if kind not in _LARGE_DOCUMENT_KINDS and stat.st_size > self.MAX_FILE_BYTES:
            log.info(
                "%s: skipping %s (%d bytes exceeds the %d-byte limit)",
                self.id,
                path,
                stat.st_size,
                self.MAX_FILE_BYTES,
            )
            return None
        if stat.st_size > MAX_DOCUMENT_BYTES:
            log.info(
                "%s: skipping %s (%d bytes exceeds the %d-byte document limit)",
                self.id,
                path,
                stat.st_size,
                MAX_DOCUMENT_BYTES,
            )
            return None

        result = extract_text(path, filename=path.name)
        if result.ok:
            return self._build_item(root, path, stat, result.text, {})
        if result.content_missing:
            return self._build_item(
                root,
                path,
                stat,
                self._placeholder_body(path, {}),
                {"content_missing": True, "content_missing_reason": result.reason},
            )
        return None

    def _detect(self, path: Path) -> str:
        """The file's real kind, read from its first bytes. ``""`` on any error."""
        try:
            return detect_kind(path, filename=path.name)
        except Exception:  # noqa: BLE001 — an unreadable file is skipped, not raised
            log.debug("%s: could not identify %s", self.id, path, exc_info=True)
            return ""

    def _media_item(
        self, root: Path, path: Path, stat: os.stat_result, kind: str
    ) -> RawItem | None:
        result = extract_text(path, filename=path.name)
        facts = dict(result.meta)
        metadata: dict[str, object] = {
            "media_kind": kind,
            "enrich_pending": True,
            **facts,
            **MediaRef(kind="file", path=str(path.resolve())).as_metadata(),
        }
        # The camera's own date beats the filesystem's: a file's mtime is the
        # day it was COPIED, so an album restored from a backup would otherwise
        # collapse onto one meaningless afternoon.
        captured = str(facts.get("captured_at") or "")
        return self._build_item(
            root,
            path,
            stat,
            self._placeholder_body(path, facts),
            metadata,
            timestamp_utc=captured or iso_utc_from_timestamp(stat.st_mtime),
        )

    def _placeholder_body(self, path: Path, facts: dict[str, object]) -> str:
        """What is known about a file before anything has read its content.

        Every line is a fact the file itself carries — never a guess. That is
        what makes it safe to index: a search for a month, a camera or a folder
        finds the picture, and nothing claims to know what is in it.
        """
        lines = [f"File: {path.name}"]
        parent = path.parent.name
        if parent:
            lines.append(f"Folder: {parent}")
        for label, key in (
            ("Taken", "captured_at"),
            ("Camera", "camera"),
        ):
            value = facts.get(key)
            if value:
                lines.append(f"{label}: {value}")
        latitude, longitude = facts.get("latitude"), facts.get("longitude")
        if latitude is not None and longitude is not None:
            lines.append(f"Location: {latitude}, {longitude}")
        return "\n".join(lines)

    def _build_item(
        self,
        root: Path,
        path: Path,
        stat: os.stat_result,
        body: str,
        extra: dict[str, object],
        *,
        timestamp_utc: str = "",
    ) -> RawItem:
        return RawItem(
            external_id=self._external_id_for(root, path),
            body=body,
            permalink=path.resolve().as_uri(),
            timestamp_utc=timestamp_utc or iso_utc_from_timestamp(stat.st_mtime),
            title=self._title_for(path, body),
            metadata={
                "mtime_ns": stat.st_mtime_ns,
                "size_bytes": stat.st_size,
                **extra,
            },
        )


__all__ = [
    "NOISE_DIR_NAMES",
    "LocalFolderConnector",
    "LocalFolderRootError",
    "first_h1_heading",
    "iso_utc_from_timestamp",
    "normalize_root_path",
    "parse_mtime_cursor",
]
