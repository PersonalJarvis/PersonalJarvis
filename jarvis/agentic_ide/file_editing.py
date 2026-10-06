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

import codecs
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
    "copy_entry",
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

#: Byte-order marks that name a UTF-16 file; such a file has NUL bytes by
#: nature, so it is recognised before the binary check.
_UTF16_BOMS = {b"\xff\xfe": "utf-16-le", b"\xfe\xff": "utf-16-be"}
_UTF16_BOM_BY_CODEC = {codec: bom for bom, codec in _UTF16_BOMS.items()}
#: How sure the detector must be before a non-UTF-8 file opens as text.
_MAX_CHAOS = 0.25
#: Single-byte code pages the detector cannot tell apart on short text, by
#: script. When it lands in a family and the bytes are valid in that script's
#: Windows code page — by far the most common legacy encodings — the Windows
#: code page wins; the status bar's "Reopen with encoding" corrects a wrong guess.
_FAMILIES: dict[str, frozenset[str]] = {
    "cp1252": frozenset(
        {
            "cp1250",
            "cp1252",
            "cp1254",
            "cp1257",
            "iso8859-1",
            "iso8859-2",
            "iso8859-9",
            "iso8859-13",
            "iso8859-15",
            "mac-latin2",
            "mac-roman",
        }
    ),
    "cp1251": frozenset({"cp1251", "cp866", "koi8-r", "koi8-u", "iso8859-5", "mac-cyrillic"}),
    "cp1253": frozenset({"cp1253", "iso8859-7", "mac-greek"}),
}


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
    #: The file mixes line endings; a save writes ``eol`` throughout.
    mixed_eol: bool = False


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
    _refuse_git(parts)
    if os.name == "nt" and any(":" in part for part in parts):
        # An NTFS alternate data stream (``a.txt:hidden``) is not a file to edit.
        raise EditError("That is not a valid file name here.")
    return "/".join(parts)


def _refuse_git(parts: list[str] | tuple[str, ...]) -> None:
    """Refuse a path inside git's own database.

    Windows drops trailing dots and spaces from a name, so ``.git.`` opens
    ``.git``; compare the name as the file system will read it.
    """
    if any(part.rstrip(". ").lower() == ".git" for part in parts):
        raise EditError("Files inside .git cannot be edited here.")


def _check_resolved(root: str | os.PathLike[str], target: Path) -> None:
    """Refuse a target that only reaches ``.git`` through a symlink.

    ``hooks -> .git/hooks`` passes the textual check and still stays inside the
    workspace, but a write there is a git hook, i.e. code that runs later.
    """
    real_root = Path(os.path.realpath(os.fspath(root)))
    try:
        _refuse_git(target.relative_to(real_root).parts)
    except ValueError as exc:  # contained_path already guarantees this
        raise EditError("That path leaves the workspace folder.") from exc


def _resolve(root: str | os.PathLike[str], path: str) -> tuple[str, Path]:
    """The normalised relative path and its contained absolute location."""
    relative = _normalise_relative(path)
    try:
        target = contained_path(root, relative)
    except UnsafePathError as exc:
        raise EditError("That path leaves the workspace folder.") from exc
    if target == Path(os.path.realpath(os.fspath(root))):
        raise EditError("Give a file path inside the workspace.")
    _check_resolved(root, target)
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
    _check_resolved(root, folder / name)
    return relative, folder / name


def _detect_eol(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _mixed_eol(text: str) -> bool:
    """True when more than one kind of line ending appears (CRLF, LF, lone CR)."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    return sum(1 for count in (crlf, lf, cr) if count) > 1


def _canonical_codec(name: str) -> str | None:
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


def _decode(data: bytes) -> tuple[str, str] | None:
    """The text and the encoding to save it back in, or None for a binary file.

    UTF-8 (with or without BOM) and BOM-marked UTF-16 are taken as they are.
    Anything else is handed to the charset detector, and only a confident
    answer opens as text: a wrong guess would garble the file on save.
    """
    for bom, codec in _UTF16_BOMS.items():
        if data.startswith(bom):
            try:
                return data[len(bom) :].decode(codec), codec
            except UnicodeDecodeError:
                return None
    if b"\x00" in data[:_BINARY_SNIFF_BYTES]:
        return None
    encoding = "utf-8-sig" if data.startswith(_UTF8_BOM) else "utf-8"
    try:
        return data.decode(encoding), encoding
    except UnicodeDecodeError:
        pass
    # Lazy import: only a non-UTF-8 file pays for the detector.
    from charset_normalizer import from_bytes

    best = from_bytes(data).best()
    if best is None or best.chaos > _MAX_CHAOS:
        return None
    codec = _canonical_codec(best.encoding)
    if codec is None:
        return None
    for preferred, family in _FAMILIES.items():
        if codec in family and codec != preferred:
            try:
                return data.decode(preferred), preferred
            except UnicodeDecodeError:
                break
    try:
        return data.decode(codec), codec
    except UnicodeDecodeError:
        return None


def _decode_as(data: bytes, encoding: str) -> tuple[str, str]:
    """Read the bytes in a chosen encoding, or say why that does not work."""
    codec = _canonical_codec(encoding)
    if codec is None:
        raise EditError(f"The encoding {encoding!r} is not known.")
    body = data
    if codec in _UTF16_BOM_BY_CODEC and data.startswith(_UTF16_BOM_BY_CODEC[codec]):
        body = data[2:]
    try:
        return body.decode(codec), codec
    except UnicodeDecodeError as exc:
        raise EditError(f"This file cannot be read as {codec}.") from exc


def _encode(text: str, encoding: str) -> bytes:
    """Text as bytes in the file's own encoding (UTF-16 keeps its BOM)."""
    codec = _canonical_codec(encoding)
    if codec is None:
        raise EditError(f"The encoding {encoding!r} is not known.")
    try:
        if codec in _UTF16_BOM_BY_CODEC:
            return _UTF16_BOM_BY_CODEC[codec] + text.encode(codec)
        return text.encode(codec)
    except UnicodeEncodeError as exc:
        raise EditError(
            f"Some characters cannot be stored as {codec}. Save the file as UTF-8 instead."
        ) from exc


def read_text_file(
    root: str | os.PathLike[str], path: str, *, encoding: str | None = None
) -> TextFile:
    """Load one workspace file for editing, untouched.

    ``encoding`` forces how the bytes are read ("Reopen with encoding");
    without it the encoding is detected.
    """
    relative, target = _resolve(root, path)
    if not target.is_file():
        raise EditError("That file does not exist.")
    size = target.stat().st_size
    if size > MAX_EDITABLE_BYTES:
        return TextFile(relative, None, "", size, "utf-8", "\n", False, True)
    data = target.read_bytes()
    version = _version_of(data)
    decoded = _decode(data) if encoding is None else _decode_as(data, encoding)
    if decoded is None:
        return TextFile(relative, None, version, size, "utf-8", "\n", True, False)
    text, encoding = decoded
    return TextFile(
        relative, text, version, size, encoding, _detect_eol(text), False, False, _mixed_eol(text)
    )


def file_version(root: str | os.PathLike[str], path: str) -> str | None:
    """The current version of one file, or None when it no longer exists.

    What the editor polls to notice an agent's edit to an open file; a file
    too large to edit reports an empty version, as :func:`read_text_file` does.
    """
    _, target = _resolve(root, path)
    try:
        if target.stat().st_size > MAX_EDITABLE_BYTES:
            return ""
        return _version_of(target.read_bytes())
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None
    except PermissionError:
        # Windows reports reading a folder as "access denied".
        if target.is_dir():
            return None
        raise


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
    data = _encode(text, encoding)
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
        _canonical_codec(encoding) or encoding,
        _detect_eol(text),
        False,
        False,
        _mixed_eol(text),
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
    # entry; that is a rename, not a collision. Only a change of case counts:
    # a hard-linked sibling is also "the same file" and must not be replaced.
    same_entry = (
        target.exists()
        and source_path.parent == target.parent
        and source_path.name.lower() == target.name.lower()
        and os.path.samefile(source_path, target)
    )
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


def _free_name(target: Path) -> Path:
    """``name copy.ext``, ``name copy 2.ext``, … — the first that is free."""
    if not (target.exists() or target.is_symlink()):
        return target
    stem, suffix = target.stem, target.suffix
    if target.is_dir():
        stem, suffix = target.name, ""
    for number in range(1, 1000):
        label = " copy" if number == 1 else f" copy {number}"
        candidate = target.with_name(f"{stem}{label}{suffix}")
        if not (candidate.exists() or candidate.is_symlink()):
            return candidate
    raise EditError("There are too many copies with that name already.")


def copy_entry(
    root: str | os.PathLike[str], source: str, destination: str, *, unique: bool = False
) -> str:
    """Copy a file or folder inside the workspace; returns the new path.

    With ``unique`` an existing destination gets a free "copy" name instead of
    refusing, the way pasting into the same folder works in a file manager.
    Symlinks are copied as links, never followed out of the workspace.
    """
    _, source_path = _resolve_entry(root, source)
    _, target = _resolve_entry(root, destination)
    if not (source_path.is_symlink() or source_path.exists()):
        raise EditError("That file or folder no longer exists.")
    if not target.parent.is_dir():
        raise EditError("The destination folder does not exist.")
    if target.exists() or target.is_symlink():
        if not unique:
            raise EditError("Something with that name already exists.")
        target = _free_name(target)
    is_folder = source_path.is_dir() and not source_path.is_symlink()
    if is_folder:
        source_key = os.path.normcase(str(source_path))
        if os.path.normcase(os.path.realpath(target)).startswith(source_key + os.sep):
            raise EditError("A folder cannot be copied into itself.")
        shutil.copytree(source_path, target, symlinks=True)
    else:
        shutil.copy2(source_path, target, follow_symlinks=False)
    real_root = Path(os.path.realpath(os.fspath(root)))
    return target.relative_to(real_root).as_posix()


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
        logger.debug("Quick Open: no git listing for {}, walking the folder", base.name)
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
