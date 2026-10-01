"""Files dropped or pasted onto a terminal pane.

A native terminal takes a dragged file and writes its PATH into the prompt.
A browser cannot: for security it hands a web page the file's BYTES and never
its location. That is the whole reason dropping a screenshot onto an agent pane
did nothing — the pane had no drop handler, and a handler alone would not have
been enough, because what the coding agent needs is a path it can open.

So a drop takes one of two routes, and the caller picks by what it actually has:

1. **The path came along.** Dragging out of Explorer or Finder usually puts the
   real path in the drag payload (``text/uri-list``). Nothing is copied — the
   path is typed straight into the agent. This is the common case for "look at
   this file in my project".
2. **Only bytes.** A screenshot pasted from the clipboard, an image dragged off
   a web page, a file from a sandboxed source. Those are written into the
   workspace here, and the path of the written copy is what the agent gets.

Why the copies land INSIDE the workspace rather than in a temp directory: a
coding agent's file access is scoped, and how it treats an absolute path from
somewhere else varies by agent, by version, and by the user's permission
settings. Betting on that would make the feature work on the maintainer's
machine and prompt-or-fail on someone else's. A path inside the folder the user
opened needs no permission from anyone.

There is no size cap. A screen recording is exactly the kind of file someone
drops on an agent, and refusing it is worse than any cost of storing it. What
keeps a big drop safe instead is how it is handled: a copy streams to disk in
chunks (never the whole file in memory), and a drop that would leave the disk
nearly full is refused with a message saying exactly that.

The drop directory hides itself from git by carrying its own ``.gitignore`` with
``*`` in it — so a dropped screenshot never shows up in the user's `git status`
and we never touch their repository configuration to achieve that.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeAlias

from loguru import logger

#: Where copies of pasted/dropped bytes land, relative to the workspace root.
DROP_DIRNAME = ".jarvis/drops"

#: Free space a drop must leave behind. Filling the disk to the last byte breaks
#: far more than the drop (the agent, the app's own database, the OS), so a drop
#: that would cross this line is refused instead.
DISK_RESERVE_BYTES = 512 * 1024 * 1024

#: Largest file whose bytes are read back for an analysis (image description,
#: text/PDF extraction). A bigger file is still stored and referenced - the
#: agent opens it itself - it just carries no description.
MAX_ANALYSIS_BYTES = 25 * 1024 * 1024

#: Chunk size for streaming a copy to disk.
_COPY_CHUNK = 1024 * 1024

#: How many files one drop may carry.
MAX_FILES = 20

#: Dropped copies older than this are swept when a new drop arrives. Long enough
#: that yesterday's screenshot is still there if the agent is still working on
#: it, short enough that the folder does not grow without bound.
KEEP_SECONDS = 7 * 24 * 3600

# A dropped name is attacker-adjacent input (it can come from a web page), and it
# also has to survive Windows. Keep letters, digits and the safe punctuation;
# everything else becomes an underscore.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Windows refuses these as file names regardless of extension.
_RESERVED = frozenset(
    {
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)


@dataclass(frozen=True, slots=True)
class StoredDrop:
    """One file written into the workspace's drop directory."""

    relative_path: str
    """Repo-relative, forward-slashed — what gets typed into the agent."""

    absolute_path: str
    name: str
    size: int


class DropError(RuntimeError):
    """A drop the module refuses, with a user-facing English message."""


#: What one dropped file can be: bytes already in memory, a path on disk to
#: copy from, or an open binary file (an upload the web server spooled to disk).
DropSource: TypeAlias = bytes | Path | BinaryIO


def size_of(source: DropSource) -> int:
    """Byte size of ``source`` without reading it. -1 if it cannot be told."""
    if isinstance(source, (bytes, bytearray)):
        return len(source)
    if isinstance(source, Path):
        try:
            return source.stat().st_size
        except OSError:
            # Unreadable file: -1 is the documented 'size unknown' answer.
            return -1
    try:
        here = source.tell()
        end = source.seek(0, os.SEEK_END)
        source.seek(here)
        return int(end)
    except (OSError, ValueError, AttributeError):
        # Unseekable stream: -1 is the documented 'size unknown' answer.
        return -1


def read_for_analysis(source: DropSource, *, limit: int = MAX_ANALYSIS_BYTES) -> bytes | None:
    """The bytes of ``source`` for an analysis, or ``None`` when it is too big.

    Only called for files an analysis will actually look at, so a large video
    dropped next to a screenshot is never pulled into memory.
    """
    size = size_of(source)
    if size < 0 or size > limit:
        return None
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, Path):
        return source.read_bytes()
    source.seek(0)
    return source.read()


def _write(source: DropSource, target: Path) -> None:
    """Write ``source`` to ``target``, streaming anything not already in memory."""
    if isinstance(source, (bytes, bytearray)):
        target.write_bytes(source)
        return
    if isinstance(source, Path):
        shutil.copyfile(source, target)
        return
    source.seek(0)
    with target.open("wb") as out:
        shutil.copyfileobj(source, out, _COPY_CHUNK)


def _ensure_room(target: Path, needed: int) -> None:
    """Refuse a drop that would leave the disk nearly full."""
    try:
        free = shutil.disk_usage(target).free
    except OSError as exc:
        # Unknown free space is not a reason to refuse: the write itself fails
        # loudly (and cleans up) if the disk really is full.
        logger.debug("Agentic IDE: free-space check skipped: {}", exc)
        return
    if needed + DISK_RESERVE_BYTES > free:
        need_mb = -(-needed // (1024 * 1024))
        free_mb = free // (1024 * 1024)
        raise DropError(
            f"Not enough free disk space for that drop ({need_mb} MB needed, "
            f"{free_mb} MB free)."
        )


def safe_name(raw: str) -> str:
    """A file name safe to write on any of the three platforms.

    Path separators are stripped rather than escaped: a dropped ``name`` may
    contain ``../`` (it is not a trusted value), and the only correct reading of
    that is "this is part of the name", never "walk up a directory".
    """
    base = os.path.basename((raw or "").replace("\\", "/")).strip()
    cleaned = _UNSAFE.sub("_", base).strip("._-")
    if not cleaned:
        cleaned = "file"
    stem, dot, ext = cleaned.rpartition(".")
    if dot and stem.lower() in _RESERVED:
        cleaned = f"{stem}_{dot}{ext}"
    elif not dot and cleaned.lower() in _RESERVED:
        cleaned = f"{cleaned}_"
    # Windows caps a path component at 255; leave room for the timestamp prefix.
    return cleaned[:120]


def drop_dir(workspace: str | Path) -> Path:
    """The workspace's drop directory, created and self-ignoring."""
    target = Path(workspace).expanduser() / DROP_DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    # The directory hides its whole contents from git without the user ever
    # having to edit their own .gitignore. Written once; harmless to rewrite.
    marker = target.parent / ".gitignore"
    if not marker.exists():
        try:
            marker.write_text("*\n", encoding="utf-8")
        except OSError as exc:  # noqa: BLE001 - cosmetic, never fatal
            logger.debug("Agentic IDE: drop dir gitignore not written: {}", exc)
    return target


def sweep(workspace: str | Path, *, keep_seconds: int = KEEP_SECONDS) -> int:
    """Delete drop copies older than ``keep_seconds``. Returns how many went."""
    try:
        target = Path(workspace).expanduser() / DROP_DIRNAME
        if not target.is_dir():
            return 0
        cutoff = time.time() - max(0, keep_seconds)
        removed = 0
        with os.scandir(target) as it:
            for item in it:
                try:
                    if item.is_file(follow_symlinks=False) and item.stat().st_mtime < cutoff:
                        os.unlink(item.path)
                        removed += 1
                except OSError:
                    continue
        return removed
    except OSError:
        return 0


def store(
    workspace: str | Path,
    files: list[tuple[str, DropSource]],
) -> list[StoredDrop]:
    """Write ``(name, source)`` pairs into the workspace drop directory.

    Each source is bytes, a path to copy from, or an open binary file; anything
    not already in memory is streamed. Raises ``DropError`` on an empty drop,
    too many files, or too little free disk space - the caller turns that into
    an HTTP error the user actually reads.
    """
    if not files:
        raise DropError("That drop carried no file.")
    if len(files) > MAX_FILES:
        raise DropError(f"Too many files at once (max {MAX_FILES}).")

    sizes = [size_of(source) for _name, source in files]

    root = Path(workspace).expanduser()
    target = drop_dir(root)
    sweep(root)
    _ensure_room(target, sum(size for size in sizes if size > 0))

    stored: list[StoredDrop] = []
    # One timestamp for the whole drop, so files dropped together sort together.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for index, ((name, source), size) in enumerate(zip(files, sizes, strict=True)):
        if size == 0:
            continue
        base = safe_name(name)
        # A second drop of the same screenshot must not overwrite the first -
        # the agent may still be working on it.
        suffix = "" if len(files) == 1 else f"-{index + 1}"
        candidate = target / f"{stamp}{suffix}-{base}"
        counter = 2
        while candidate.exists():
            candidate = target / f"{stamp}{suffix}-{counter}-{base}"
            counter += 1
        try:
            _write(source, candidate)
            written = candidate.stat().st_size
        except OSError as exc:
            # A half-written video is worse than none: the agent would open it.
            candidate.unlink(missing_ok=True)
            raise DropError(f"Could not save {base}: {exc}") from exc
        if written == 0:
            candidate.unlink(missing_ok=True)
            continue
        stored.append(
            StoredDrop(
                relative_path=candidate.relative_to(root).as_posix(),
                absolute_path=str(candidate),
                name=base,
                size=written,
            )
        )

    if not stored:
        raise DropError("Every dropped file was empty.")
    logger.info(
        "Agentic IDE: stored {} dropped file(s) in {}", len(stored), target
    )
    return stored


def reference(path: str, *, agent: str) -> str:
    """How ``path`` should be written so ``agent`` picks the file up.

    Some CLIs resolve ``@path`` into the file's contents — verified live for the
    ones that declare it, including that the file-picker popup does not eat an
    injected reference. A CLI without that syntax gets a quoted path and reads
    it because the sentence asks it to, which works everywhere and is therefore
    the default a newly registered entry gets.

    Which of the two a CLI wants is registry data, not a name test here: a
    dropped file is one of the places a wrong guess is invisible — the path goes
    in, the agent answers, and nobody notices it never opened the file.

    Quoting matters in both forms: workspace paths contain spaces far more often
    than people expect ("Personal Jarvis").
    """
    from jarvis.workspace import agents as workspace_agents

    posix = path.replace("\\", "/")
    spec = workspace_agents.get_agent(agent)
    if spec is not None and spec.file_reference == "at":
        # An @reference containing a space would end at the space, so a path
        # that needs quoting is passed plainly instead of half-referenced.
        return f"@{posix}" if " " not in posix else f'"{posix}"'
    return f'"{posix}"'


def dereference(reference: str) -> str:
    """The workspace-relative path behind a :func:`reference`, or "".

    The inverse of :func:`reference`, and deliberately nothing more: it undoes
    the two shapes that function writes (``@path`` and a quoted path) so a
    receipt about a drop can point a viewer at the file, without a second
    place that knows how agents like their paths.
    """
    value = (reference or "").strip()
    if value.startswith("@"):
        value = value[1:]
    elif len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value.strip().replace("\\", "/")


def within_workspace(path: str, workspace: str | Path) -> str | None:
    """``path`` as a workspace-relative posix path, or ``None`` if it is outside.

    Used for the fast route: a file dragged out of Explorer already lives
    somewhere, and when that somewhere is inside the open workspace there is
    nothing to copy — the agent can open it where it is.
    """
    try:
        root = Path(workspace).expanduser().resolve()
        candidate = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        return None
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return None


__all__ = [
    "DROP_DIRNAME",
    "KEEP_SECONDS",
    "DISK_RESERVE_BYTES",
    "MAX_ANALYSIS_BYTES",
    "MAX_FILES",
    "DropError",
    "DropSource",
    "StoredDrop",
    "dereference",
    "drop_dir",
    "read_for_analysis",
    "reference",
    "safe_name",
    "size_of",
    "store",
    "sweep",
    "within_workspace",
]
