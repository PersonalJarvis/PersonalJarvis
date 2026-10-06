"""Read and write workspace files for the Agentic IDE's code editor.

The explorer's preview path (``documents.extract``) normalises whitespace and is
fine for reading a document, but an editor must round-trip a file byte for byte:
indentation, line endings, a UTF-8 byte-order mark and a trailing newline all
survive a load and a save unchanged. Everything here therefore works on the raw
bytes and only decodes them at the edge.

Safety rules every function follows:

* **Contained.** A caller-supplied relative path is resolved (symlinks
  included) and refused unless it stays inside the workspace folder. Paths that
  do not exist yet (a new file, a rename target) are checked the same way.
* **Never inside ``.git``.** Editing git's own database through the editor
  corrupts a checkout; those paths are refused outright.
* **Optimistic concurrency.** A read returns a ``version`` (a content hash).
  A save names the version it was based on; if a coding agent changed the file
  in the meantime the save is refused with :class:`EditConflict` instead of
  silently overwriting the agent's work.
* **Atomic saves.** New bytes go to a temporary file in the same directory and
  replace the target with ``os.replace``, so a crash mid-save never leaves a
  half-written file. The original file mode is kept.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from loguru import logger

from jarvis.core.path_safety import UnsafePathError, contained_path
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from .folders import _SKIP_DIRS

__all__ = [
    "MAX_EDITABLE_BYTES",
    "EditConflict",
    "EditError",
    "TextFile",
    "TrashUnavailable",
    "create_entry",
    "delete_entry",
    "file_version",
    "list_files",
    "read_text_file",
    "rename_entry",
    "write_text_file",
]

#: Larger files open read-only in the explorer; a browser editor holding tens of
#: megabytes of text stalls the whole window.
MAX_EDITABLE_BYTES = 5 * 1024 * 1024

#: Bytes sniffed for a NUL to tell a binary file from text.
_BINARY_SNIFF_BYTES = 8192

_UTF8_BOM = b"\xef\xbb\xbf"

_ENCODINGS = ("utf-8", "utf-8-sig")


class EditError(ValueError):
    """A request the editor cannot carry out; the message is safe to show."""


class EditConflict(EditError):
    """The file changed on disk since the editor loaded it."""

    def __init__(self, message: str, current_version: str | None) -> None:
        super().__init__(message)
        self.current_version = current_version


@dataclass(frozen=True, slots=True)
class TextFile:
    """One file as the editor sees it."""

    path: str
    text: str | None
    version: str
    size: int
    encoding: str
    eol: str
    binary: bool
    too_large: bool


def _version_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalise_relative(path: str) -> str:
    """A POSIX relative path, or :class:`EditError` for anything else."""
    value = (path or "").strip().replace("\\", "/")
    if not value or any(ord(char) < 32 for char in value):
        raise EditError("Give a file path inside the workspace.")
    pure = PurePosixPath(value)
    if pure.is_absolute() or (len(value) > 1 and value[1] == ":"):
        raise EditError("Use a path relative to the workspace folder.")
    parts = [part for part in pure.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise EditError("That path leaves the workspace folder.")
    if any(part.lower() == ".git" for part in parts):
        raise EditError("Files inside .git cannot be edited here.")
    return "/".join(parts)


def _resolve(root: str | os.PathLike[str], path: str) -> tuple[str, Path]:
    """The normalised relative path and its contained absolute location."""
    relative = _normalise_relative(path)
    try:
        target = contained_path(root, relative)
    except UnsafePathError as exc:
        raise EditError("That path leaves the workspace folder.") from exc
    if target == Path(os.path.realpath(os.fspath(root))):
        raise EditError("Give a file path inside the workspace.")
    return relative, target


def _resolve_entry(root: str | os.PathLike[str], path: str) -> tuple[str, Path]:
    """Like :func:`_resolve`, but the last component is taken literally.

    Rename and delete act on the directory entry itself: a symlink is renamed
    or removed, never the file it points at, and a case-only rename keeps the
    spelling the user typed (resolving would return the on-disk spelling).
    Only the parent folder is resolved and must stay inside the workspace.
    """
    relative = _normalise_relative(path)
    parent, _, name = relative.rpartition("/")
    try:
        folder = contained_path(root, parent or ".")
    except UnsafePathError as exc:
        raise EditError("That path leaves the workspace folder.") from exc
    return relative, folder / name


def _detect_eol(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def read_text_file(root: str | os.PathLike[str], path: str) -> TextFile:
    """Load one workspace file for editing, untouched."""
    relative, target = _resolve(root, path)
    if not target.is_file():
        raise EditError("That file does not exist.")
    size = target.stat().st_size
    if size > MAX_EDITABLE_BYTES:
        return TextFile(relative, None, "", size, "utf-8", "\n", False, True)
    data = target.read_bytes()
    version = _version_of(data)
    if b"\x00" in data[:_BINARY_SNIFF_BYTES]:
        return TextFile(relative, None, version, size, "utf-8", "\n", True, False)
    encoding = "utf-8-sig" if data.startswith(_UTF8_BOM) else "utf-8"
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        return TextFile(relative, None, version, size, encoding, "\n", True, False)
    return TextFile(relative, text, version, size, encoding, _detect_eol(text), False, False)


def file_version(root: str | os.PathLike[str], path: str) -> str | None:
    """The current version of one file, or None when it no longer exists.

    What the editor polls to notice an agent's edit to an open file; a file
    too large to edit reports an empty version, as :func:`read_text_file` does.
    """
    _, target = _resolve(root, path)
    if not target.is_file():
        return None
    if target.stat().st_size > MAX_EDITABLE_BYTES:
        return ""
    return _version_of(target.read_bytes())


def _atomic_write(target: Path, data: bytes) -> None:
    """Replace ``target`` with ``data`` in one step, keeping its file mode."""
    mode: int | None = None
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = None
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".jarvis-save", dir=target.parent
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, target)
    finally:
        # After a successful replace the temp name is gone; otherwise clean up.
        if temp.exists():
            try:
                temp.unlink()
            except OSError as exc:
                logger.warning("Could not remove editor temp file {}: {}", temp, exc)


def write_text_file(
    root: str | os.PathLike[str],
    path: str,
    text: str,
    *,
    expected_version: str | None,
    encoding: str = "utf-8",
    create: bool = False,
) -> TextFile:
    """Save editor text over a workspace file.

    ``expected_version`` is the version the editor loaded. ``None`` means the
    editor believes the file is new: that is only allowed with ``create=True``
    and refused when a file already sits there.
    """
    relative, target = _resolve(root, path)
    if encoding not in _ENCODINGS:
        raise EditError("Only UTF-8 files can be saved from the editor.")
    data = text.encode("utf-8")
    if encoding == "utf-8-sig":
        data = _UTF8_BOM + data
    if len(data) > MAX_EDITABLE_BYTES:
        raise EditError("That file is too large to save from the editor.")
    if target.is_dir():
        raise EditError("A folder already has that name.")
    if target.exists():
        current = _version_of(target.read_bytes())
        if expected_version is None or expected_version != current:
            raise EditConflict("The file changed on disk since it was opened.", current)
    elif not create and expected_version is not None:
        raise EditConflict("The file was deleted since it was opened.", None)
    if not target.parent.is_dir():
        raise EditError("The folder for that file does not exist.")
    _atomic_write(target, data)
    return TextFile(
        relative,
        None,
        _version_of(data),
        len(data),
        encoding,
        _detect_eol(text),
        False,
        False,
    )


def create_entry(root: str | os.PathLike[str], path: str, *, directory: bool) -> str:
    """Create an empty file or a folder (with missing parents)."""
    relative, target = _resolve(root, path)
    if target.exists():
        raise EditError("Something with that name already exists.")
    if directory:
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        # "x" fails instead of truncating if a file appears in between.
        with target.open("xb"):
            pass
    return relative


def rename_entry(root: str | os.PathLike[str], source: str, destination: str) -> str:
    """Rename or move a file or folder inside the workspace."""
    _, source_path = _resolve_entry(root, source)
    relative, target = _resolve_entry(root, destination)
    if not (source_path.is_symlink() or source_path.exists()):
        raise EditError("That file or folder no longer exists.")
    # On a case-insensitive disk "readme.md" -> "README.md" names the same
    # entry; that is a rename, not a collision.
    same_entry = target.exists() and os.path.samefile(source_path, target)
    if (target.exists() or target.is_symlink()) and not same_entry:
        raise EditError("Something with that name already exists.")
    source_key = os.path.normcase(str(source_path))
    if not source_path.is_symlink() and source_path.is_dir():
        if os.path.normcase(os.path.realpath(target)).startswith(source_key + os.sep):
            raise EditError("A folder cannot be moved into itself.")
    if not target.parent.is_dir():
        raise EditError("The destination folder does not exist.")
    os.replace(source_path, target)
    return relative


#: Quick Open lists at most this many paths; a home directory opened as a
#: workspace must not stall the editor.
MAX_LISTED_FILES = 50_000
_LIST_TIMEOUT_S = 15.0


def _git_listed_files(base: Path) -> list[str] | None:
    """Tracked plus untracked-but-not-ignored files, or None outside a checkout."""
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.quotePath=false",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            cwd=str(base),
            capture_output=True,
            timeout=_LIST_TIMEOUT_S,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    paths = result.stdout.decode("utf-8", errors="replace").split("\0")
    # ls-files lists a file deleted in the working tree until it is staged.
    return sorted({path for path in paths if path and (base / path).is_file()})


def _walked_files(base: Path) -> tuple[list[str], bool]:
    found: list[str] = []
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(name for name in dirs if name not in _SKIP_DIRS)
        rel_dir = Path(current).relative_to(base).as_posix()
        for name in sorted(files):
            found.append(name if rel_dir == "." else f"{rel_dir}/{name}")
            if len(found) >= MAX_LISTED_FILES:
                return found, True
    return found, False


def list_files(root: str | os.PathLike[str]) -> tuple[list[str], bool]:
    """Every file path in the workspace for Quick Open, and whether it was cut."""
    base = Path(os.path.realpath(os.fspath(root)))
    listed = _git_listed_files(base)
    if listed is None:
        return _walked_files(base)
    return listed[:MAX_LISTED_FILES], len(listed) > MAX_LISTED_FILES


class TrashUnavailable(EditError):
    """No system trash on this machine; only a permanent delete is possible."""


def _move_to_trash(target: Path) -> bool:
    """Send ``target`` to the system trash; False when this machine has none.

    ``send2trash`` covers the Windows Recycle Bin, the macOS Trash and the
    freedesktop trash on Linux. It is optional: a headless server without it
    (or without a trash folder) gets an honest "permanent delete?" question
    from the editor instead of a silent permanent delete.
    """
    try:
        from send2trash import send2trash  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        send2trash(str(target))
    except OSError as exc:
        logger.info("Editor delete: system trash refused {}: {}", target.name, exc)
        return False
    return True


def delete_entry(root: str | os.PathLike[str], path: str, *, permanent: bool = False) -> bool:
    """Delete a file or a folder with everything in it.

    Goes to the system trash unless ``permanent`` is set; returns whether it
    did. Raises :class:`TrashUnavailable` when there is no trash and the caller
    has not agreed to a permanent delete.
    """
    _, target = _resolve_entry(root, path)
    if not (target.is_symlink() or target.exists()):
        raise EditError("That file or folder no longer exists.")
    if not permanent:
        if _move_to_trash(target):
            return True
        raise TrashUnavailable("This computer has no trash to move it to.")
    if target.is_symlink() or target.is_file():
        target.unlink()
    else:
        shutil.rmtree(target)
    return False
