"""Which coding agent changed which file, read from the agents' own records.

Git says WHAT changed in a workspace; it cannot say WHO — every pane writes
through the same working tree. The coding CLIs, though, write down every tool
call they make, with its arguments (:mod:`.agent_transcript`). A file-writing
call names the file it wrote, so the Changes tab can put the agent next to
each changed file without guessing from the screen.

Deliberately narrow:

* **Only writing tools count.** A call whose tool name says it writes (edit,
  write, patch, replace, create, notebook) and that did not fail. A file an
  agent changed through a shell command (``sed -i``, a formatter, a build) has
  no author here — an honest blank beats a wrong name.
* **Only panes still in the workspace.** Their records are what can be read;
  a closed pane's edits keep their diff, just without a name.
* **Capability, not product.** Every CLI with a readable record goes through
  the same event vocabulary; no branch on which CLI wrote it (AP-21).
* **Cached.** The Changes tab polls every few seconds; a record is re-read at
  most every :data:`CACHE_TTL_S` per pane, off the event loop by the caller.

Cross-platform: paths are compared as resolved :class:`~pathlib.Path` objects
and answered as POSIX paths relative to the workspace root, the same shape
:mod:`.git_changes` uses.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from loguru import logger

from . import agent_transcript

#: How long one pane's list of written files is reused before its record is read again.
CACHE_TTL_S = 20.0

#: A tool whose name says it writes a file. Read-only tools (Read, Grep, Glob,
#: view) never match; ``shell``/``Bash`` never match either — see the module doc.
_WRITING_TOOL = re.compile(r"edit|write|patch|replace|create|notebook", re.IGNORECASE)

#: The arguments a writing tool names its file in, across the CLIs.
_PATH_KEYS = (
    "file_path",
    "path",
    "target_file",
    "filePath",
    "absolute_path",
    "AbsolutePath",
    "notebook_path",
    "TargetFile",
)

#: A file header inside an apply_patch body (Codex and the CLIs that copied it).
_PATCH_FILE = re.compile(
    r"^\*\*\* (?:Update|Add|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", re.MULTILINE
)


@dataclass(frozen=True, slots=True)
class PaneRecord:
    """What is needed to read one pane's record."""

    pane: str
    history_id: str
    agent: str
    display_name: str
    session_id: str
    home: Path | None
    #: Where the pane's agent runs, which relative paths in its calls resolve against.
    folder: str


@dataclass(frozen=True, slots=True)
class ChangeAuthor:
    pane: str
    history_id: str
    agent: str
    display_name: str
    #: When this pane last wrote the file (epoch milliseconds), 0 when unknown.
    last_edit_ms: int


def _patch_paths(value: Any) -> list[str]:
    """Every file an apply_patch body names, found in any string argument."""
    found: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if "*** " in item:
                found.extend(a or b for a, b in _PATCH_FILE.findall(item))
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list | tuple):
            stack.extend(item)
    return found


def written_paths(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    """``{path as written in the call: last write ms}`` for successful writing calls."""
    calls: dict[str, tuple[list[str], int]] = {}
    failed: set[str] = set()
    for event in events:
        kind = event.get("kind")
        payload = event.get("payload") or {}
        if kind == "tool_result" and payload.get("is_error"):
            failed.add(str(payload.get("call_id")))
            continue
        if kind != "tool_call":
            continue
        name = str(payload.get("name") or "")
        if not _WRITING_TOOL.search(name):
            continue
        args = payload.get("input")
        paths: list[str] = []
        if isinstance(args, dict):
            for key in _PATH_KEYS:
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    paths.append(value.strip())
                    break
        paths.extend(_patch_paths(args))
        if paths:
            calls[str(payload.get("call_id"))] = (paths, int(event.get("ts_ms") or 0))
    out: dict[str, int] = {}
    for call_id, (paths, ts) in calls.items():
        if call_id in failed:
            continue
        for path in paths:
            out[path] = max(out.get(path, 0), ts)
    return out


def _relative(raw: str, base: Path, root: Path) -> str | None:
    """``raw`` as a POSIX path relative to ``root``, or None when outside it."""
    candidate = Path(raw.strip().strip('"'))
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        relative = candidate.resolve(strict=False).relative_to(root)
    except (ValueError, OSError):
        # A path outside the workspace is not ours to attribute; None says so.
        return None
    text = PurePosixPath(*relative.parts).as_posix()
    return None if text in ("", ".") else text


Reader = Callable[[PaneRecord], list[dict[str, Any]] | None]


def _read_events(record: PaneRecord) -> list[dict[str, Any]] | None:
    return agent_transcript.read_events(record.agent, record.session_id, home=record.home)


_cache: dict[tuple[str, str], tuple[float, dict[str, int]]] = {}


def _pane_writes(record: PaneRecord, reader: Reader, now: float) -> dict[str, int]:
    key = (record.history_id, record.session_id)
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < CACHE_TTL_S:
        return cached[1]
    try:
        events = reader(record) or []
    except Exception as exc:  # a record the CLI rewrote mid-read: no names this round
        logger.info("Change authors: pane {} record unreadable: {}", record.pane, exc)
        events = []
    writes = written_paths(events)
    _cache[key] = (now, writes)
    return writes


def change_authors(
    workspace_folder: str | Path,
    panes: Iterable[PaneRecord],
    *,
    reader: Reader = _read_events,
    now: float | None = None,
) -> dict[str, list[ChangeAuthor]]:
    """``{workspace-relative path: authors, newest edit first}``."""
    root = Path(workspace_folder).expanduser().resolve(strict=False)
    moment = time.monotonic() if now is None else now
    for key in [k for k, (at, _w) in _cache.items() if moment - at > 10 * CACHE_TTL_S]:
        del _cache[key]
    by_path: dict[str, list[ChangeAuthor]] = {}
    for record in panes:
        base = Path(record.folder or root).expanduser()
        # One pane may spell one file two ways (relative, then absolute).
        latest: dict[str, int] = {}
        for raw, ts in _pane_writes(record, reader, moment).items():
            relative = _relative(raw, base, root)
            if relative is not None:
                latest[relative] = max(latest.get(relative, 0), ts)
        for relative, ts in latest.items():
            by_path.setdefault(relative, []).append(
                ChangeAuthor(
                    pane=record.pane,
                    history_id=record.history_id,
                    agent=record.agent,
                    display_name=record.display_name,
                    last_edit_ms=ts,
                )
            )
    for authors in by_path.values():
        authors.sort(key=lambda author: author.last_edit_ms, reverse=True)
    return by_path


def authors_for(
    path: str, is_directory: bool, by_path: dict[str, list[ChangeAuthor]]
) -> list[ChangeAuthor]:
    """The authors of one git entry; an untracked folder gathers everything inside it."""
    if not is_directory:
        return by_path.get(path, [])
    prefix = path.rstrip("/") + "/"
    newest: dict[str, ChangeAuthor] = {}
    for written, authors in by_path.items():
        if not written.startswith(prefix):
            continue
        for author in authors:
            known = newest.get(author.history_id)
            if known is None or author.last_edit_ms > known.last_edit_ms:
                newest[author.history_id] = author
    return sorted(newest.values(), key=lambda author: author.last_edit_ms, reverse=True)
