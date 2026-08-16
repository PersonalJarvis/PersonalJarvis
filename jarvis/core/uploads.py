"""Staging for uploads that arrive as a folder or an archive.

The desktop UI lets the owner drop a whole folder (or a ``.zip`` / ``.tar.gz``)
onto a dropzone. What reaches the server is a flat list of ``(path, bytes)``
pairs whose paths come straight from the browser — which means they are
untrusted: a crafted archive can carry ``../../.ssh/authorized_keys`` just as
easily as ``SKILL.md``.

This module turns that list into a real directory on disk, and it is the ONLY
place that decides what a legal upload path is. Everything it does is
fail-closed:

- a path that escapes the destination (``..``, a leading ``/``, a drive
  letter) rejects the whole upload rather than being silently dropped, because
  a traversal entry is an attack, not a typo,
- OS clutter (``.git/``, ``__MACOSX/``, ``.DS_Store``, ``Thumbs.db``) is
  removed and REPORTED, so the owner sees why the file count shrank,
- a shared top-level folder is stripped, so dropping ``my-skill/`` and
  dropping its contents give the same result,
- size and count ceilings are checked against the archive's own headers
  BEFORE anything is written, so a zip bomb never reaches the disk.

The caller decides what the staged folder means (a skill, a plugin, a
wallpaper); this module only guarantees that the folder is a faithful,
harmless copy of what was uploaded.
"""
from __future__ import annotations

import io
import logging
import tarfile
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Ceilings mirroring the ones the public skill catalogs use. They are checked
#: per file AND in total, because fifty small files can hurt as much as one
#: large one.
MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 50 * 1024 * 1024
#: A folder with more entries than this is a mistaken drop (a home directory,
#: a ``node_modules``), not a skill or a plugin.
MAX_UPLOAD_FILE_COUNT = 2000

#: Suffixes that make a single uploaded file an archive to expand rather than
#: a file to store. Ordered longest-first so ``.tar.gz`` wins over ``.gz``.
ARCHIVE_SUFFIXES: tuple[str, ...] = (".tar.gz", ".zip", ".tgz")

_METADATA_DIRS = frozenset({".git", "__macosx", ".svn", ".hg"})
_METADATA_FILES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


class UploadRejected(Exception):
    """An upload that must not be staged, with the reason the owner will read.

    ``status_code`` travels along so a route can hand it to the client
    unchanged instead of re-deriving it from the message.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class StagedUpload:
    """What ``stage_upload`` produced, in the words the UI needs.

    ``files`` are the paths as they now exist under ``root`` — normalized,
    filtered and with the shared folder already stripped. ``ignored`` and
    ``stripped_root`` exist so the UI can explain the difference between what
    was dropped and what was kept, instead of quietly showing fewer files.
    """

    root: Path
    files: tuple[str, ...]
    ignored: tuple[str, ...]
    stripped_root: str | None
    total_bytes: int

    def to_json(self) -> dict[str, object]:
        return {
            "files": list(self.files),
            "ignored": list(self.ignored),
            "stripped_root": self.stripped_root,
            "total_bytes": self.total_bytes,
        }


def is_archive_name(name: str) -> bool:
    """True when a single uploaded file should be expanded, not stored."""
    lowered = name.strip().lower()
    return any(lowered.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def is_local_metadata_path(path: str) -> bool:
    """True for editor/OS clutter that no upload should ever carry."""
    segments = [segment for segment in path.lower().split("/") if segment]
    if not segments:
        return False
    if any(segment in _METADATA_DIRS for segment in segments[:-1]):
        return True
    basename = segments[-1]
    if basename in _METADATA_DIRS:
        return True
    return basename in _METADATA_FILES or basename.startswith("._")


def normalize_upload_path(raw: str) -> str:
    """The POSIX-style relative path of an uploaded entry.

    Returns ``""`` for an entry that carries no usable path (an empty name, a
    bare directory marker) — the caller skips those. Raises
    ``UploadRejected`` for a path that tries to leave the destination, because
    that is never a mistake worth tolerating.
    """
    cleaned = raw.replace("\x00", "").replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("/")
    if not cleaned or cleaned.endswith("/"):
        return ""

    segments = [segment for segment in cleaned.split("/") if segment and segment != "."]
    if not segments:
        return ""
    if any(segment == ".." for segment in segments):
        raise UploadRejected(f"Upload path {raw!r} points outside the folder.")
    # A Windows drive letter survives the slash normalisation above and would
    # make pathlib discard the destination on join.
    if ":" in segments[0]:
        raise UploadRejected(f"Upload path {raw!r} is not a relative path.")
    return "/".join(segments)


def strip_shared_root(paths: Sequence[str]) -> tuple[list[str], str | None]:
    """Removes the one folder every path shares, if there is one.

    Dropping ``my-skill/`` and dropping the three files inside it must not
    produce different results — only this function decides which of the two
    the upload was.
    """
    if not paths:
        return list(paths), None
    split = [path.split("/") for path in paths]
    if any(len(parts) < 2 for parts in split):
        return list(paths), None
    first = split[0][0]
    if any(parts[0] != first for parts in split):
        return list(paths), None
    return ["/".join(parts[1:]) for parts in split], first


def expand_archive(name: str, data: bytes) -> list[tuple[str, bytes]]:
    """Unpacks a ``.zip`` / ``.tar.gz`` / ``.tgz`` into ``(path, bytes)`` pairs.

    The declared sizes in the archive headers are checked first, so a bomb is
    refused before a single byte is decompressed.
    """
    lowered = name.strip().lower()
    if lowered.endswith(".zip"):
        return _expand_zip(name, data)
    if lowered.endswith(".tar.gz") or lowered.endswith(".tgz"):
        return _expand_tar(name, data)
    raise UploadRejected(f"{name!r} is not a supported archive (.zip, .tar.gz, .tgz).")


def _expand_zip(name: str, data: bytes) -> list[tuple[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadRejected(f"{name!r} is not a readable ZIP archive.") from exc

    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        _check_declared_sizes(
            [(info.filename, info.file_size) for info in members], name
        )
        out: list[tuple[str, bytes]] = []
        for info in members:
            try:
                out.append((info.filename, archive.read(info)))
            except (OSError, zipfile.BadZipFile) as exc:
                raise UploadRejected(
                    f"{name!r} could not be unpacked: {exc}"
                ) from exc
    return out


def _expand_tar(name: str, data: bytes) -> list[tuple[str, bytes]]:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise UploadRejected(f"{name!r} is not a readable tar.gz archive.") from exc

    with archive:
        # Only regular files travel. Links, devices and fifos are dropped
        # rather than rejected: an archive of a real project may legitimately
        # carry a symlink, and refusing the whole upload over one would be
        # useless to the owner. What matters is that none of them is written.
        members = [member for member in archive.getmembers() if member.isfile()]
        _check_declared_sizes([(member.name, member.size) for member in members], name)
        out: list[tuple[str, bytes]] = []
        for member in members:
            handle = archive.extractfile(member)
            if handle is None:  # unreadable member — skip, do not fail the upload
                logger.warning("upload: skipping unreadable tar member %r", member.name)
                continue
            out.append((member.name, handle.read()))
    return out


def _check_declared_sizes(entries: Sequence[tuple[str, int]], archive_name: str) -> None:
    """Refuses an archive on its own headers, before anything is decompressed."""
    if len(entries) > MAX_UPLOAD_FILE_COUNT:
        raise UploadRejected(
            f"{archive_name!r} contains {len(entries)} files — "
            f"the limit is {MAX_UPLOAD_FILE_COUNT}."
        )
    total = 0
    for path, size in entries:
        if size > MAX_UPLOAD_FILE_BYTES:
            raise UploadRejected(_file_too_large_message(path))
        total += size
    if total > MAX_UPLOAD_TOTAL_BYTES:
        raise UploadRejected(_total_too_large_message())


def _file_too_large_message(path: str) -> str:
    limit_mb = MAX_UPLOAD_FILE_BYTES // (1024 * 1024)
    return f"'{path}' is larger than {limit_mb} MB."


def _total_too_large_message() -> str:
    limit_mb = MAX_UPLOAD_TOTAL_BYTES // (1024 * 1024)
    return f"The upload is larger than {limit_mb} MB in total."


def stage_upload(
    entries: Iterable[tuple[str, bytes]],
    dest: Path,
    *,
    expand_archives: bool = True,
) -> StagedUpload:
    """Writes an uploaded folder or archive into ``dest`` and reports what happened.

    ``dest`` is created if it does not exist and is expected to be empty and
    owned by the caller (a temporary directory in practice) — nothing here
    guards against writing into a directory that already holds files.
    """
    pairs = list(entries)
    if expand_archives and len(pairs) == 1 and is_archive_name(pairs[0][0]):
        name, data = pairs[0]
        if len(data) > MAX_UPLOAD_TOTAL_BYTES:
            raise UploadRejected(_total_too_large_message())
        pairs = expand_archive(name, data)

    if not pairs:
        raise UploadRejected("The upload contains no files.")

    kept: list[tuple[str, bytes]] = []
    ignored: list[str] = []
    for raw_path, data in pairs:
        path = normalize_upload_path(raw_path)
        if not path:
            continue
        if is_local_metadata_path(path):
            ignored.append(path)
            continue
        kept.append((path, data))

    if not kept:
        raise UploadRejected("The upload contains no usable files.")
    if len(kept) > MAX_UPLOAD_FILE_COUNT:
        raise UploadRejected(
            f"The upload contains {len(kept)} files — "
            f"the limit is {MAX_UPLOAD_FILE_COUNT}."
        )

    total = 0
    for path, data in kept:
        if len(data) > MAX_UPLOAD_FILE_BYTES:
            raise UploadRejected(_file_too_large_message(path))
        total += len(data)
    if total > MAX_UPLOAD_TOTAL_BYTES:
        raise UploadRejected(_total_too_large_message())

    stripped_paths, stripped_root = strip_shared_root([path for path, _ in kept])

    root = dest.resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for path, (_, data) in zip(stripped_paths, kept, strict=True):
        target = (root / path).resolve()
        # Belt and braces: normalize_upload_path already refused traversal, but
        # a write outside the staging root must be impossible even if it ever
        # grows a hole.
        if target != root and root not in target.parents:
            raise UploadRejected(f"Upload path {path!r} points outside the folder.")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(data)
        except OSError as exc:
            raise UploadRejected(
                f"Could not store '{path}': {exc}", status_code=500
            ) from exc
        written.append(path)

    return StagedUpload(
        root=root,
        files=tuple(written),
        # Reported in the same words as the kept files. Clutter is filtered
        # before the shared folder is found, so a path only loses the wrapper
        # when it actually sat inside it — a stray ``__MACOSX/`` NEXT to the
        # skill folder keeps its own name.
        ignored=tuple(dict.fromkeys(_strip_prefix(ignored, stripped_root))),
        stripped_root=stripped_root,
        total_bytes=total,
    )


def _strip_prefix(paths: Sequence[str], prefix: str | None) -> list[str]:
    if not prefix:
        return list(paths)
    marker = f"{prefix}/"
    return [
        path[len(marker):] if path.startswith(marker) else path for path in paths
    ]
