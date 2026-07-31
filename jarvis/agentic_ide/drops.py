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

The drop directory hides itself from git by carrying its own ``.gitignore`` with
``*`` in it — so a dropped screenshot never shows up in the user's `git status`
and we never touch their repository configuration to achieve that.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

#: Where copies of pasted/dropped bytes land, relative to the workspace root.
DROP_DIRNAME = ".jarvis/drops"

#: Per-file cap. A screenshot is ~1-5 MB; 25 MB covers a screen recording still
#: or a large PDF without letting an accidental drop of a video eat the disk.
MAX_FILE_BYTES = 25 * 1024 * 1024

#: Total cap for one drop, so dragging a whole folder of images is bounded.
MAX_TOTAL_BYTES = 100 * 1024 * 1024

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
    files: list[tuple[str, bytes]],
) -> list[StoredDrop]:
    """Write ``(name, data)`` pairs into the workspace drop directory.

    Raises ``DropError`` on an empty drop, too many files, or a size overrun —
    the caller turns that into an HTTP error the user actually reads.
    """
    if not files:
        raise DropError("That drop carried no file.")
    if len(files) > MAX_FILES:
        raise DropError(f"Too many files at once (max {MAX_FILES}).")

    total = sum(len(data) for _n, data in files)
    if total > MAX_TOTAL_BYTES:
        raise DropError(
            f"That drop is too large (max {MAX_TOTAL_BYTES // (1024 * 1024)} MB in total)."
        )
    for name, data in files:
        if len(data) > MAX_FILE_BYTES:
            raise DropError(
                f"{name!r} is too large (max {MAX_FILE_BYTES // (1024 * 1024)} MB per file)."
            )

    root = Path(workspace).expanduser()
    target = drop_dir(root)
    sweep(root)

    stored: list[StoredDrop] = []
    # One timestamp for the whole drop, so files dropped together sort together.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for index, (name, data) in enumerate(files):
        if not data:
            continue
        base = safe_name(name)
        # A second drop of the same screenshot must not overwrite the first —
        # the agent may still be working on it.
        suffix = "" if len(files) == 1 else f"-{index + 1}"
        candidate = target / f"{stamp}{suffix}-{base}"
        counter = 2
        while candidate.exists():
            candidate = target / f"{stamp}{suffix}-{counter}-{base}"
            counter += 1
        try:
            candidate.write_bytes(data)
        except OSError as exc:
            raise DropError(f"Could not save {base}: {exc}") from exc
        stored.append(
            StoredDrop(
                relative_path=candidate.relative_to(root).as_posix(),
                absolute_path=str(candidate),
                name=base,
                size=len(data),
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
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "DropError",
    "StoredDrop",
    "drop_dir",
    "reference",
    "safe_name",
    "store",
    "sweep",
    "within_workspace",
]
