"""Export-file import: the standards-based universal on-ramp.

A user (or a company) already HAS their data — a Google Takeout archive, a
WhatsApp "Export chat" text file, a mail archive, a CSV dump, a folder of
PDFs. This connector is the door that accepts whatever shape it arrives in:
point it at one file or one folder, and it detects the formats, says what it
found, and streams everything in.

Design rules it inherits from the connector contract (design doc 02):

* **Yields :class:`RawItem` only.** No store, no LLM, no embedding.
* **Nothing blocking runs on the event loop.** The whole detect/parse/walk
  path is one BLOCKING generator, pulled a batch at a time through
  ``asyncio.to_thread`` — so a 10 GB mbox costs one batch of items in memory,
  never the file.
* **Nothing is extracted to disk.** Zip entries are parsed through
  ``zipfile`` streams; a nested zip is buffered in memory under an explicit
  cap rather than written out.
* **Every skip is counted and logged, never raised.** An encrypted PDF, a
  binary blob, a zip past the depth limit — the import continues and the
  reason is on the record.

Resume / checkpoint contract (documented honestly)
--------------------------------------------------

``backfill(ctx, checkpoint)`` walks in deterministic sorted order and resume
is **file-granular, not item-granular**. Item ids carry the natural
identifier of the record (a mail's ``Message-ID``, an event's ``UID``), which
is what makes a re-import idempotent — but those ids do not sort in the order
the items are produced, so "skip everything at or before the checkpoint"
cannot be applied to them the way the local-folder connector applies it to
paths. Instead the checkpoint's ORIGIN FILE is recovered from the id, every
file sorting strictly BEFORE it is skipped, and that file itself is re-read
from its start. The re-read items upsert as ``unchanged`` on
``(source_id, external_id)``, so the only cost is a little repeated work
after an interrupted run.

Deletes are not detectable: an export file is a snapshot, and
``capabilities.deletes`` is therefore ``False``. ``incremental`` is
:data:`~jarvis.ultrawiki.types.IncrementalMode.NONE` — a re-sync is a full,
idempotent re-read of the same drop.

Archive scope: **zip only** (``.zip``, plus a ``PK`` magic sniff). Google
Takeout offers ``.tgz`` as well; tar/gzip support is deliberately NOT here
because a compressed tar has no central directory, so entries can only be
reached by decompressing the whole stream in order — which breaks both the
sorted-order guarantee the checkpoint depends on and the ability to skip an
entry cheaply. Takeout's zip option covers the same data.
"""

from __future__ import annotations

import asyncio
import csv
import email.utils
import hashlib
import io
import json
import logging
import os
import re
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote

from jarvis.ultrawiki.adapters._google import (
    BODY_CAP,
    cap_body,
    strip_html,
)
from jarvis.ultrawiki.connectors.local_folder import iso_utc_from_timestamp
from jarvis.ultrawiki.types import (
    AuthKind,
    ConnectorCapabilities,
    ConnectorContext,
    IncrementalMode,
    RawItem,
)

log = logging.getLogger(__name__)

__all__ = [
    "ARCHIVE_FORMAT",
    "CONTAINER_FORMATS",
    "EXPORT_FORMATS",
    "TAR_FORMAT",
    "ExportImportConnector",
    "ScanReport",
    "chat_name_from_filename",
    "detect_format",
    "scan_export",
    "uploads_dir",
]


# ---------------------------------------------------------------------------
# Formats + limits
# ---------------------------------------------------------------------------

#: Every ITEM-PRODUCING format, in the order a preview report lists them.
#: Five-layer drift discipline (AP-4): the frontend renders one label per id
#: and ``tests/unit/ultrawiki/test_export_import.py`` pins the two together.
EXPORT_FORMATS: tuple[str, ...] = (
    "mbox",
    "eml",
    "ics",
    "vcard",
    "whatsapp",
    "csv",
    "tsv",
    "jsonl",
    "json",
    "pdf",
    "document",
    "html",
    "markdown",
    "text",
    "image",
    "audio",
    "video",
)

#: The container formats. Not in :data:`EXPORT_FORMATS` — they yield no items
#: of their own, only the entries inside them.
ARCHIVE_FORMAT = "zip"

#: Google Takeout offers `.zip` AND `.tgz`, and the second used to be refused
#: outright — so the user who clicked the default download was left holding a
#: file this importer would not open.
TAR_FORMAT = "tar"

CONTAINER_FORMATS: tuple[str, ...] = (ARCHIVE_FORMAT, TAR_FORMAT)

#: Suffixes whose bytes are a ZIP but whose CONTENT is one document. The one
#: place the extension outranks the magic, and it has to: telling a `.docx`
#: from a plain archive means reading the archive's member list, which the
#: sniffer (which sees only the first few KB) cannot do. Getting this wrong is
#: not cosmetic — a Word file would be walked as an archive and imported as a
#: pile of its own internal XML parts.
_DOCUMENT_EXTENSIONS: dict[str, str] = {
    ".docx": "document", ".docm": "document",
    ".xlsx": "document", ".xlsm": "document",
    ".pptx": "document", ".pptm": "document",
    ".odt": "document", ".ods": "document", ".odp": "document", ".odg": "document",
    ".epub": "document",
    ".rtf": "document",
    ".doc": "document", ".xls": "document", ".ppt": "document",
}

#: Formats handed to the shared extractor rather than parsed here.
_SHARED_EXTRACTOR_KINDS: frozenset[str] = frozenset(
    {"docx", "xlsx", "pptx", "odf", "epub", "rtf", "legacy_office"}
)

_EXTENSION_FORMATS: dict[str, str] = {
    ".mbox": "mbox",
    ".mbx": "mbox",
    ".eml": "eml",
    ".emlx": "eml",
    ".ics": "ics",
    ".ical": "ics",
    ".ifb": "ics",
    ".vcf": "vcard",
    ".vcard": "vcard",
    ".csv": "csv",
    ".tsv": "tsv",
    ".tab": "tsv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".json": "json",
    ".txt": "text",
    ".text": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".pdf": "pdf",
    ".zip": ARCHIVE_FORMAT,
}

#: Nested archives are followed this many levels deep (the outer zip is 1).
#: A cycle needs a self-containing archive, which the depth limit alone ends.
MAX_ZIP_DEPTH = 3

#: Total UNCOMPRESSED bytes one run will expand out of archives. The honest
#: defence against a zip bomb: a 4 GB budget imports any real Takeout and
#: stops a 42-byte / 4-petabyte archive after one harmless step.
ZIP_UNCOMPRESSED_BUDGET = 4 * 1024 * 1024 * 1024

#: A nested archive has to be buffered (``zipfile`` needs to seek, and a zip
#: entry stream cannot), so it is bounded far tighter than the outer one,
#: which is read straight off disk.
MAX_BUFFERED_ZIP_BYTES = 256 * 1024 * 1024

#: A PDF also needs random access; the same buffering rule applies inside an
#: archive. On disk it is opened by path and this cap never applies.
MAX_BUFFERED_PDF_BYTES = 128 * 1024 * 1024

#: An Office/OpenDocument/EPUB file is a ZIP the extractor must seek inside,
#: so it is read whole. Generous — a slide deck with images is routinely
#: 50 MB around a few pages of text — but never unbounded.
MAX_BUFFERED_DOCUMENT_BYTES = 128 * 1024 * 1024

#: Bytes read from a media file. EXIF sits in front of the image data, so the
#: header is all that is needed — which is what keeps a folder of 4K video
#: from being pulled through memory to learn a capture date.
MEDIA_HEADER_BYTES = 512 * 1024

#: One tar entry buffered in memory. A compressed tar has no central
#: directory, so entries can only be read in order and cannot be re-opened;
#: buffering is the only way to hand a parser a re-readable stream.
MAX_BUFFERED_TAR_ENTRY_BYTES = 64 * 1024 * 1024

#: Formats parsed from one whole in-memory string (calendar, contacts, JSON,
#: HTML). Bigger than this is refused with an honest line rather than an
#: out-of-memory kill; the streaming formats have no such limit.
MAX_WHOLE_READ_BYTES = 64 * 1024 * 1024

#: One mail is bounded independently of the mbox around it, so a malformed
#: file without separators cannot become one unbounded message.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024

#: Rows/lines per tabular item. Small enough that a chunk is still ONE
#: retrievable thought, large enough that a 200k-row dump is not 200k items.
TABULAR_CHUNK_ROWS = 100

#: One line of a JSONL dump never contributes more than this to a chunk.
MAX_JSONL_LINE_CHARS = 8000

#: Bytes sniffed to detect a format. Enough for every magic number plus a few
#: WhatsApp lines, cheap enough to do for every file in a preview pass.
SNIFF_BYTES = 4096

#: Items handed back per worker-thread hop (mirrors the local-folder walk).
ITEM_BATCH = 32

#: Bytes a PREVIEW pass will read to count records before it starts reporting
#: estimates instead of exact numbers. A preview must feel instant; an exact
#: count of a 40 GB mailbox does not.
SCAN_BYTES_BUDGET = 256 * 1024 * 1024

#: Directory names never walked, on top of every dot-prefixed entry.
SKIP_DIR_NAMES: frozenset[str] = frozenset({"__MACOSX", "node_modules"})


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

#: ``PK`` is handled ahead of this table (an office document is a ZIP), and a
#: gzip stream resolves through its NAME because compression records nothing
#: about what was compressed.
_MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"BEGIN:VCALENDAR", "ics"),
    (b"BEGIN:VCARD", "vcard"),
    (b"\x1f\x8b", "gzip_or_tar"),
)

#: Media formats this importer now carries through to the enrichment stage.
_MEDIA_FORMATS: frozenset[str] = frozenset({"image", "audio", "video"})

#: Double suffixes that mark a compressed stream as a tar archive.
_TARBALL_SUFFIXES: tuple[str, ...] = (
    ".tgz", ".tar.gz", ".taz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar.zst"
)

#: An mbox opens with a ``From <envelope> <date>`` separator line, followed
#: immediately by the first message's headers.
_MBOX_MAGIC = re.compile(rb"^From \S+[^\r\n]*\r?\n[A-Za-z-]+:")

#: Byte-order mark a Windows exporter leaves in front of a text file.
_UTF8_BOM = b"\xef\xbb\xbf"

#: Zero-width / bidi marks and narrow spaces that real WhatsApp exports carry
#: around timestamps; they break every naive line regex.
_INVISIBLE_RE = re.compile(r"[​-‏‪-‮﻿]")
_ODD_SPACE_RE = re.compile(r"[   ]")

#: "[dd.mm.yy, hh:mm:ss] Name: text" — the bracketed family (iOS and several
#: desktop exports), with either separator, either date order, optional
#: seconds and optional AM/PM.
_WA_BRACKET_RE = re.compile(
    r"^\[(?P<a>\d{1,4})[./-](?P<b>\d{1,2})[./-](?P<c>\d{2,4}),?\s+"
    r"(?P<hh>\d{1,2}):(?P<mm>\d{2})(?::(?P<ss>\d{2}))?\s*"
    r"(?P<ampm>[AaPp]\.?[Mm]\.?)?\]\s*"
    r"(?:(?P<author>[^:]{1,80}?):\s)?(?P<text>.*)$"
)

#: "dd/mm/yy, hh:mm - Name: text" — the dash family (Android and the web
#: export), same tolerances.
_WA_DASH_RE = re.compile(
    r"^(?P<a>\d{1,4})[./-](?P<b>\d{1,2})[./-](?P<c>\d{2,4}),?\s+"
    r"(?P<hh>\d{1,2}):(?P<mm>\d{2})(?::(?P<ss>\d{2}))?\s*"
    r"(?P<ampm>[AaPp]\.?[Mm]\.?)?\s+-\s+"
    r"(?:(?P<author>[^:]{1,80}?):\s)?(?P<text>.*)$"
)

# The connector word between "WhatsApp Chat" and the contact name is whatever
# the exporting phone's UI language used. These are matching DATA — the
# literal tokens a filename must contain to be recognised — not prose, and
# they are the reason a German or Spanish phone's export still gets a proper
# chat name instead of the raw filename.
# i18n-allow: non-English filename tokens WhatsApp itself writes (input vocabulary)
_WA_FILENAME_RE = re.compile(
    r"^(?:whatsapp[\s_-]*chat|chat[\s_-]*de[\s_-]*whatsapp"
    r"|conversa[\s_-]*do[\s_-]*whatsapp|discussion[\s_-]*whatsapp"
    r"|chat[\s_-]*whatsapp|whatsapp)"
    r"[\s_-]*(?:with|mit|con|com|avec|de|van|med|z)?[\s_-]*(?P<name>.+)$",
    re.IGNORECASE,
)


def _normalize_line(text: str) -> str:
    """Strip the invisible marks a phone export sprinkles around timestamps."""
    return _ODD_SPACE_RE.sub(" ", _INVISIBLE_RE.sub("", text))


def _looks_like_whatsapp(head: bytes) -> bool:
    """True when the first lines match either WhatsApp message family."""
    try:
        text = head.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — a sniff never raises
        return False
    hits = 0
    for raw in text.splitlines()[:40]:
        line = _normalize_line(raw)
        if _WA_BRACKET_RE.match(line) or _WA_DASH_RE.match(line):
            hits += 1
            if hits >= 2:
                return True
    return False


def detect_format(name: str, head: bytes) -> str:
    """The format id for one file: content magic first, extension second.

    Magic wins because an export's extension lies routinely — a WhatsApp chat
    and a vCard dump are both ``.txt``, a Takeout mail file has no extension
    at all. Returns ``""`` for anything unrecognised; the caller counts it as
    skipped rather than failing the import.
    """
    stripped = head
    if stripped.startswith(_UTF8_BOM):
        stripped = stripped[len(_UTF8_BOM) :]
    stripped = stripped.lstrip(b"\r\n \t")
    suffix = Path(name).suffix.lower()

    # A ZIP is the one case where the name has to win: every modern Office and
    # OpenDocument file IS a ZIP, and walking one as an archive would import a
    # Word document as a heap of its own internal XML.
    if stripped.startswith(b"PK\x03\x04"):
        return _DOCUMENT_EXTENSIONS.get(suffix, ARCHIVE_FORMAT)

    for prefix, fmt in _MAGIC_PREFIXES:
        if stripped[: len(prefix)].upper() == prefix.upper():
            if fmt == "gzip_or_tar":
                return TAR_FORMAT if _looks_like_tarball(name) else ""
            return fmt
    if _MBOX_MAGIC.match(stripped):
        return "mbox"
    ext_format = _EXTENSION_FORMATS.get(suffix, "")
    if ext_format in ("", "text", "markdown") and _looks_like_whatsapp(head):
        return "whatsapp"
    if ext_format:
        return ext_format

    # Everything the shared extractor knows and this module never learned:
    # photos, voice notes, video, Office documents saved without a helpful
    # name, tar archives. One detector, so a format is supported once.
    shared = _shared_kind(name, head)
    if shared in _MEDIA_FORMATS:
        return shared
    if shared in _SHARED_EXTRACTOR_KINDS:
        return "document"
    if shared == "tar":
        return TAR_FORMAT

    if head and _is_probably_text(head):
        # A Takeout mail file has no suffix at all; anything that reads as
        # text is still worth importing as text rather than being dropped.
        return "text"
    return ""


def _shared_kind(name: str, head: bytes) -> str:
    """What :mod:`jarvis.ultrawiki.extract` makes of these leading bytes."""
    try:
        from jarvis.ultrawiki.extract import detect_kind  # noqa: PLC0415 — lazy (AP-26)

        return detect_kind(head, filename=name)
    except Exception:  # noqa: BLE001 — a sniff never fails an import
        log.debug("export import: shared detection failed for %s", name, exc_info=True)
        return ""


def _looks_like_tarball(name: str) -> bool:
    """Whether a compressed stream's NAME says it wraps a tar archive."""
    lowered = Path(name).name.lower()
    return any(lowered.endswith(suffix) for suffix in _TARBALL_SUFFIXES)


def _is_probably_text(head: bytes) -> bool:
    """Cheap binary check: a NUL byte or a high share of control bytes."""
    if b"\x00" in head:
        return False
    sample = head[:1024]
    if not sample:
        return False
    control = sum(1 for byte in sample if byte < 9 or 13 < byte < 32)
    return control / len(sample) < 0.05


def chat_name_from_filename(name: str) -> str:
    """The chat partner's name out of a WhatsApp export filename.

    ``WhatsApp Chat with Ada.txt`` / ``WhatsApp Chat mit Ada.txt`` ->
    ``Ada``. Anything the pattern does not cover falls back to the file's own
    stem, so a renamed export still lands under a readable name instead of an
    empty thread key.
    """
    stem = Path(name).stem.strip()
    match = _WA_FILENAME_RE.match(stem)
    if match:
        found = match.group("name").strip(" -_")
        if found:
            return found
    return stem or name


# ---------------------------------------------------------------------------
# Walk plumbing
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScanReport:
    """What a walk found — the preview payload AND the import's honest log.

    Both passes fill the same structure so a preview can never promise
    something the import then reports differently.
    """

    #: format id -> {"files": int, "items_estimate": int, "exact": bool}
    formats: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: lowercase extension -> number of files skipped as unrecognised
    unknown: dict[str, int] = field(default_factory=dict)
    #: [{"path": ..., "reason": ...}] — read once, refused honestly
    unreadable: list[dict[str, str]] = field(default_factory=list)
    archives: dict[str, Any] = field(
        default_factory=lambda: {
            "files": 0,
            "entries": 0,
            "max_depth": 0,
            "budget_exhausted": False,
        }
    )
    total_bytes: int = 0
    files_seen: int = 0
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def count_file(self, fmt: str, size: int) -> None:
        row = self.formats.setdefault(
            fmt, {"files": 0, "items_estimate": 0, "exact": True}
        )
        row["files"] += 1
        self.files_seen += 1
        self.total_bytes += max(0, int(size))

    def add_items(self, fmt: str, count: int, *, exact: bool = True) -> None:
        row = self.formats.setdefault(
            fmt, {"files": 0, "items_estimate": 0, "exact": True}
        )
        row["items_estimate"] += max(0, int(count))
        if not exact:
            row["exact"] = False

    def count_unknown(self, name: str, size: int) -> None:
        suffix = Path(name).suffix.lower() or "(no extension)"
        self.unknown[suffix] = self.unknown.get(suffix, 0) + 1
        self.files_seen += 1
        self.total_bytes += max(0, int(size))

    def refuse(self, path: str, reason: str) -> None:
        if len(self.unreadable) < 200:
            self.unreadable.append({"path": path, "reason": reason})

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def as_dict(self) -> dict[str, Any]:
        """The API payload — formats in the declared order, never dict order."""
        ordered = {
            fmt: dict(self.formats[fmt])
            for fmt in EXPORT_FORMATS
            if fmt in self.formats
        }
        for fmt, row in self.formats.items():  # a format id added later
            ordered.setdefault(fmt, dict(row))
        return {
            "formats": ordered,
            "unknown": [
                {"extension": ext, "files": count}
                for ext, count in sorted(
                    self.unknown.items(), key=lambda pair: (-pair[1], pair[0])
                )
            ],
            "unreadable": list(self.unreadable),
            "archives": dict(self.archives),
            "total_bytes": self.total_bytes,
            "files_seen": self.files_seen,
            "items_estimate": sum(
                int(row.get("items_estimate", 0)) for row in self.formats.values()
            ),
            "unknown_files": sum(self.unknown.values()),
            "truncated": self.truncated,
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class _Budget:
    """Remaining uncompressed archive bytes for one walk.

    The default is read through a factory, not baked in at class-definition
    time: the cap is a module constant a test (or a future setting) must be
    able to change without the dataclass silently keeping the old value.
    """

    remaining: int = field(default_factory=lambda: ZIP_UNCOMPRESSED_BUDGET)
    exhausted: bool = False

    def take(self, size: int) -> bool:
        if self.exhausted:
            return False
        if size > self.remaining:
            self.exhausted = True
            return False
        self.remaining -= max(0, size)
        return True


@dataclass(frozen=True, slots=True)
class _Entry:
    """One concrete parseable blob — on disk or inside an archive."""

    #: On-disk file this came from. The RESUME unit (see the module docstring).
    origin: str
    #: Full stable key: ``origin`` for a plain file, ``origin!inner/path``
    #: for an archive entry. Prefixes every ``external_id`` it produces.
    key: str
    name: str
    permalink: str
    size: int
    fmt: str
    timestamp_utc: str
    #: Opens a FRESH binary stream each call; the caller always closes it.
    open_binary: Callable[[], IO[bytes]]
    #: Set only for on-disk files — a seekable original that ``pypdf`` and the
    #: whole-read parsers can use without buffering.
    disk_path: Path | None = None
    #: Which container this came out of, if any (``zip`` / ``tar`` / ``""``).
    archive_kind: str = ""
    #: On-disk path of that container, empty when it is itself nested. Only a
    #: ZIP on disk can be reopened entry by entry later, which is what the
    #: enrichment stage needs to describe a photo inside an export.
    archive_path: str = ""
    #: The entry's name inside that container.
    archive_entry: str = ""


def _sorted_disk_files(root: Path) -> list[tuple[str, Path]]:
    """``(key, path)`` for every non-hidden file under ``root``, sorted by key."""
    found: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name not in SKIP_DIR_NAMES
        )
        base = Path(dirpath)
        for name in filenames:
            if name.startswith("."):
                continue
            path = base / name
            found.append((path.relative_to(root).as_posix(), path))
    found.sort(key=lambda pair: pair[0])
    return found


def _read_head(open_binary: Callable[[], IO[bytes]]) -> bytes:
    try:
        with open_binary() as stream:
            return stream.read(SNIFF_BYTES)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise _Unreadable(f"{type(exc).__name__}: {exc}") from exc


class _Unreadable(Exception):
    """One file could not be read. Always caught; never reaches a caller."""


def _zip_entry_timestamp(info: zipfile.ZipInfo) -> str:
    """A zip entry's stored local time as ISO-8601 UTC (zips carry no zone)."""
    try:
        moment = datetime(*info.date_time, tzinfo=UTC)
    except (TypeError, ValueError):
        return ""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _walk_entries(
    root: Path, report: ScanReport, budget: _Budget
) -> Iterator[_Entry]:
    """Every parseable blob under ``root``, in deterministic sorted order.

    A generator on purpose: archives stay open exactly as long as the caller
    is consuming their entries, and nothing is materialised ahead of time.
    """
    if root.is_file():
        pairs = [(root.name, root)]
    else:
        pairs = _sorted_disk_files(root)
    for key, path in pairs:
        yield from _entries_for_disk_file(path, key, report, budget, depth=0)


def _entries_for_disk_file(
    path: Path, key: str, report: ScanReport, budget: _Budget, *, depth: int
) -> Iterator[_Entry]:
    try:
        size = path.stat().st_size
        head = _read_head(lambda: path.open("rb"))
    except (OSError, _Unreadable) as exc:
        report.refuse(key, f"{type(exc).__name__}: {exc}")
        return
    fmt = detect_format(path.name, head)
    if fmt == ARCHIVE_FORMAT:
        report.archives["files"] += 1
        yield from _entries_for_zip(
            lambda: path.open("rb"),
            origin=key,
            prefix="",
            report=report,
            budget=budget,
            depth=depth + 1,
            label=key,
            archive_path=str(path.resolve()),
        )
        return
    if fmt == TAR_FORMAT:
        report.archives["files"] += 1
        yield from _entries_for_tar(
            lambda: path.open("rb"),
            origin=key,
            prefix="",
            report=report,
            budget=budget,
            depth=depth + 1,
            label=key,
        )
        return
    if not fmt:
        report.count_unknown(path.name, size)
        return
    report.count_file(fmt, size)
    yield _Entry(
        origin=key,
        key=key,
        name=path.name,
        permalink=_file_uri(path),
        size=size,
        fmt=fmt,
        timestamp_utc=_disk_timestamp(path),
        open_binary=lambda: path.open("rb"),
        disk_path=path,
    )


def _disk_timestamp(path: Path) -> str:
    try:
        return iso_utc_from_timestamp(path.stat().st_mtime)
    except OSError:
        return ""


def _file_uri(path: Path) -> str:
    try:
        return path.resolve().as_uri()
    except (OSError, ValueError):
        return f"file:///{quote(path.as_posix().lstrip('/'))}"


def _entries_for_zip(
    open_archive: Callable[[], IO[bytes]],
    *,
    origin: str,
    prefix: str,
    report: ScanReport,
    budget: _Budget,
    depth: int,
    label: str,
    archive_path: str = "",
) -> Iterator[_Entry]:
    """Walk one archive's entries as streams — nothing is written to disk."""
    if depth > MAX_ZIP_DEPTH:
        report.note(
            f"'{label}' contains archives nested deeper than {MAX_ZIP_DEPTH} "
            "levels; those were left unread."
        )
        return
    report.archives["max_depth"] = max(report.archives["max_depth"], depth)
    try:
        archive = zipfile.ZipFile(open_archive())
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.refuse(label, f"{type(exc).__name__}: {exc}")
        return
    with archive:
        try:
            infos = sorted(archive.infolist(), key=lambda info: info.filename)
        except (RuntimeError, zipfile.BadZipFile) as exc:
            report.refuse(label, f"{type(exc).__name__}: {exc}")
            return
        base_uri = _zip_base_uri(open_archive, label)
        for info in infos:
            if info.is_dir():
                continue
            inner = info.filename.replace("\\", "/")
            parts = [part for part in inner.split("/") if part]
            if not parts or any(
                part.startswith(".") or part in SKIP_DIR_NAMES for part in parts
            ):
                continue
            if not budget.take(int(info.file_size)):
                report.archives["budget_exhausted"] = True
                report.note(
                    "The archive expands to more than "
                    f"{ZIP_UNCOMPRESSED_BUDGET // (1024**3)} GB; reading "
                    "stopped there so the import cannot be used to fill this "
                    "machine's disk or memory."
                )
                return
            report.archives["entries"] += 1
            full_inner = f"{prefix}{inner}"
            key = f"{origin}!{full_inner}"
            opener = _zip_entry_opener(archive, info, key, report)
            if opener is None:
                continue
            try:
                head = _read_head(opener)
            except _Unreadable as exc:
                report.refuse(key, str(exc))
                continue
            fmt = detect_format(parts[-1], head)
            if fmt in CONTAINER_FORMATS:
                buffered = _buffer_zip_entry(
                    archive, info, key, report, MAX_BUFFERED_ZIP_BYTES, "archive"
                )
                if buffered is None:
                    continue
                report.archives["files"] += 1
                walk = _entries_for_zip if fmt == ARCHIVE_FORMAT else _entries_for_tar
                yield from walk(
                    lambda data=buffered: io.BytesIO(data),
                    origin=origin,
                    prefix=f"{full_inner}!",
                    report=report,
                    budget=budget,
                    depth=depth + 1,
                    label=key,
                )
                continue
            if not fmt:
                report.count_unknown(parts[-1], int(info.file_size))
                continue
            report.count_file(fmt, int(info.file_size))
            yield _Entry(
                origin=origin,
                key=key,
                name=parts[-1],
                permalink=f"{base_uri}#{quote(full_inner)}",
                size=int(info.file_size),
                fmt=fmt,
                timestamp_utc=_zip_entry_timestamp(info),
                open_binary=opener,
                archive_kind=ARCHIVE_FORMAT,
                archive_path=archive_path,
                archive_entry=inner,
            )


def _entries_for_tar(
    open_archive: Callable[[], IO[bytes]],
    *,
    origin: str,
    prefix: str,
    report: ScanReport,
    budget: _Budget,
    depth: int,
    label: str,
) -> Iterator[_Entry]:
    """Walk a tar (plain or compressed) as a forward-only stream.

    The honest differences from the ZIP walk, all forced by the format:

    * **Order is the archive's, not sorted.** A compressed tar has no central
      directory, so the entries can only be read in the order they were
      written. Resume therefore restarts at the archive, which is safe because
      every re-read upserts as ``unchanged`` on ``(source_id, external_id)``.
    * **Each entry is buffered** under :data:`MAX_BUFFERED_TAR_ENTRY_BYTES`.
      A stream member cannot be re-opened, and the parsers need to read a
      member more than once (sniff, then parse).
    * **A member is never written to disk**, and paths are flattened, so a
      hostile ``../`` entry cannot escape anywhere — there is nowhere to
      escape TO.
    """
    import tarfile  # noqa: PLC0415 — lazy (AP-26): only a tar import pays for it

    if depth > MAX_ZIP_DEPTH:
        report.note(
            f"'{label}' contains archives nested deeper than {MAX_ZIP_DEPTH} "
            "levels; those were left unread."
        )
        return
    report.archives["max_depth"] = max(report.archives["max_depth"], depth)
    try:
        # "r|*" is the STREAM mode: no seeking, so it works on a compressed
        # archive and on a member of another archive alike.
        archive = tarfile.open(fileobj=open_archive(), mode="r|*")
    except (OSError, tarfile.TarError, RuntimeError, EOFError) as exc:
        report.refuse(label, f"{type(exc).__name__}: {exc}")
        return
    with archive:
        try:
            yield from _tar_members(
                archive,
                origin=origin,
                prefix=prefix,
                report=report,
                budget=budget,
                depth=depth,
                label=label,
            )
        except (OSError, tarfile.TarError, RuntimeError, EOFError) as exc:
            # A truncated download is the normal way this ends. Everything
            # read up to here is kept.
            report.refuse(label, f"{type(exc).__name__}: {exc}")


def _tar_members(
    archive: Any,
    *,
    origin: str,
    prefix: str,
    report: ScanReport,
    budget: _Budget,
    depth: int,
    label: str,
) -> Iterator[_Entry]:
    for member in archive:
        if not member.isfile():
            continue
        inner = str(member.name).replace("\\", "/")
        parts = [part for part in inner.split("/") if part and part != "."]
        if not parts or any(
            part.startswith(".") or part in SKIP_DIR_NAMES or part == ".."
            for part in parts
        ):
            continue
        if not budget.take(int(member.size)):
            report.archives["budget_exhausted"] = True
            report.note(
                "The archive expands to more than "
                f"{ZIP_UNCOMPRESSED_BUDGET // (1024**3)} GB; reading stopped "
                "there so the import cannot be used to fill this machine's "
                "disk or memory."
            )
            return
        report.archives["entries"] += 1
        full_inner = f"{prefix}{inner}"
        key = f"{origin}!{full_inner}"
        if int(member.size) > MAX_BUFFERED_TAR_ENTRY_BYTES:
            report.refuse(
                key,
                "an entry inside a tar archive has to be held in memory to be "
                f"read, and this one is larger than "
                f"{MAX_BUFFERED_TAR_ENTRY_BYTES // (1024 * 1024)} MB; unpack "
                "the archive and import the folder instead",
            )
            continue
        try:
            handle = archive.extractfile(member)
            payload = b"" if handle is None else handle.read()
        except Exception as exc:  # noqa: BLE001 — one bad member, not the archive
            report.refuse(key, f"{type(exc).__name__}: {exc}")
            continue
        fmt = detect_format(parts[-1], payload[:SNIFF_BYTES])
        if fmt in CONTAINER_FORMATS:
            report.archives["files"] += 1
            walk = _entries_for_zip if fmt == ARCHIVE_FORMAT else _entries_for_tar
            yield from walk(
                lambda data=payload: io.BytesIO(data),
                origin=origin,
                prefix=f"{full_inner}!",
                report=report,
                budget=budget,
                depth=depth + 1,
                label=key,
            )
            continue
        if not fmt:
            report.count_unknown(parts[-1], int(member.size))
            continue
        report.count_file(fmt, int(member.size))
        yield _Entry(
            origin=origin,
            key=key,
            name=parts[-1],
            permalink=f"file:///{quote(label)}#{quote(full_inner)}",
            size=int(member.size),
            fmt=fmt,
            timestamp_utc=_tar_member_timestamp(member),
            open_binary=lambda data=payload: io.BytesIO(data),
            archive_kind=TAR_FORMAT,
            archive_entry=inner,
        )


def _tar_member_timestamp(member: Any) -> str:
    try:
        return datetime.fromtimestamp(int(member.mtime), tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _zip_base_uri(open_archive: Callable[[], IO[bytes]], label: str) -> str:
    """The ``file://`` URI of the archive itself; the label when it has none."""
    try:
        probe = open_archive()
    except OSError:
        return f"file:///{quote(label)}"
    try:
        name = getattr(probe, "name", "")
        if isinstance(name, str) and name:
            return _file_uri(Path(name))
    finally:
        try:
            probe.close()
        except OSError:  # pragma: no cover — closing a BytesIO cannot fail
            pass
    return f"file:///{quote(label)}"


def _zip_entry_opener(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    key: str,
    report: ScanReport,
) -> Callable[[], IO[bytes]] | None:
    """A fresh-stream opener for one entry, or ``None`` when it is encrypted."""
    if info.flag_bits & 0x1:
        report.refuse(key, "the archive entry is password protected")
        return None

    def _open() -> IO[bytes]:
        return archive.open(info, "r")

    return _open


def _buffer_zip_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    key: str,
    report: ScanReport,
    limit: int,
    what: str,
) -> bytes | None:
    """Read one entry into memory under ``limit``; ``None`` when it is bigger.

    Only for the two parsers that genuinely need to seek (a nested archive's
    central directory, a PDF's cross-reference table). Everything else reads
    the entry as a stream.
    """
    if int(info.file_size) > limit:
        report.refuse(
            key,
            f"the {what} inside the archive is larger than "
            f"{limit // (1024 * 1024)} MB, which is more than this import "
            "will hold in memory at once",
        )
        return None
    try:
        with archive.open(info, "r") as stream:
            return stream.read(limit + 1)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.refuse(key, f"{type(exc).__name__}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _hash_id(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", errors="replace"))
        digest.update(b"\x00")
    return digest.hexdigest()[:24]


def _decode_header_value(raw: Any) -> str:
    """One MIME header as readable text; never raises on a malformed value."""
    if raw is None:
        return ""
    try:
        return str(make_header(decode_header(str(raw)))).strip()
    except Exception:  # noqa: BLE001 — a broken header degrades to its raw form
        return str(raw).strip()


_SUBJECT_PREFIX_RE = re.compile(
    r"^\s*(?:re|fw|fwd|aw|wg|sv|vs|antw|rif)\s*(?:\[\d+\])?\s*:\s*",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def _thread_key_for_subject(subject: str) -> str:
    """A mail subject folded to its conversation key (``Re:``/``AW:`` gone)."""
    text = subject
    for _ in range(8):
        stripped = _SUBJECT_PREFIX_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _iso_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text_stream(entry: _Entry) -> io.TextIOWrapper:
    """A UTF-8 text view of an entry's bytes; undecodable bytes are replaced."""
    return io.TextIOWrapper(
        entry.open_binary(), encoding="utf-8", errors="replace", newline=""
    )


def _read_whole_text(entry: _Entry, report: ScanReport) -> str | None:
    """The entry's full text, or ``None`` when it exceeds the whole-read cap."""
    if entry.size > MAX_WHOLE_READ_BYTES:
        report.refuse(
            entry.key,
            f"this format is parsed as one document and the file is larger "
            f"than {MAX_WHOLE_READ_BYTES // (1024 * 1024)} MB",
        )
        return None
    try:
        with entry.open_binary() as stream:
            raw = stream.read(MAX_WHOLE_READ_BYTES + 1)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
        return None
    if len(raw) > MAX_WHOLE_READ_BYTES:
        report.refuse(
            entry.key,
            f"the file is larger than {MAX_WHOLE_READ_BYTES // (1024 * 1024)} MB",
        )
        return None
    return raw.decode("utf-8", errors="replace").lstrip("﻿")


def _item(
    entry: _Entry,
    *,
    natural_id: str,
    body: str,
    title: str = "",
    thread_key: str = "",
    author_raw: str = "",
    timestamp_utc: str = "",
    metadata: dict[str, Any] | None = None,
) -> RawItem:
    """Assemble one :class:`RawItem` with the shared cap + id conventions."""
    capped, truncated = cap_body(body)
    extra = {
        "format": entry.fmt,
        "source_file": entry.key,
        "import_kind": "export-file",
    }
    if truncated:
        extra["truncated"] = True
    if metadata:
        extra.update(metadata)
    external_id = entry.key if not natural_id else f"{entry.key}#{natural_id}"
    return RawItem(
        external_id=external_id,
        body=capped,
        permalink=entry.permalink,
        timestamp_utc=timestamp_utc or entry.timestamp_utc,
        title=title,
        thread_key=thread_key,
        author_raw=author_raw,
        metadata=extra,
    )


class _IdSequencer:
    """Keeps natural ids unique inside one file without inventing new ones.

    Two calendar events legitimately share a ``UID`` (a recurring series with
    an override), and a broken export can repeat a ``Message-ID``. The first
    occurrence keeps the clean id; later ones get an explicit ``~2`` suffix so
    the second record is stored rather than silently overwriting the first.
    """

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def unique(self, natural_id: str) -> str:
        count = self._seen.get(natural_id, 0) + 1
        self._seen[natural_id] = count
        return natural_id if count == 1 else f"{natural_id}~{count}"


# ---------------------------------------------------------------------------
# Per-format parsers — each is a blocking generator over ONE entry
# ---------------------------------------------------------------------------


def _iter_mbox_messages(stream: IO[bytes]) -> Iterator[bytes]:
    """Split an mbox stream into raw message bytes, one at a time.

    Constant memory: only the current message is buffered, and that buffer is
    itself bounded by :data:`MAX_MESSAGE_BYTES` so a file WITHOUT separators
    cannot become one unbounded message. ``>From`` escaping (mboxo/mboxrd) is
    left exactly as written — unescaping it would corrupt a body that legally
    contains that text.
    """
    buffer: list[bytes] = []
    size = 0
    started = False
    previous_blank = True
    for line in stream:
        if previous_blank and line.startswith(b"From "):
            if started and buffer:
                yield b"".join(buffer)
            buffer, size, started = [], 0, True
            previous_blank = False
            continue
        if started and size < MAX_MESSAGE_BYTES:
            buffer.append(line)
            size += len(line)
        previous_blank = not line.strip(b"\r\n")
    if started and buffer:
        yield b"".join(buffer)


def _mail_body(message: Message) -> str:
    """The readable text of a mail: ``text/plain`` first, stripped HTML next."""
    plain: list[str] = []
    html: list[str] = []
    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_maintype() == "multipart":
            continue
        disposition = str(part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            continue
        subtype = part.get_content_subtype()
        if subtype not in ("plain", "html"):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001 — a broken part is not a broken mail
            payload = None
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, AttributeError):
            text = payload.decode("utf-8", errors="replace")
        (plain if subtype == "plain" else html).append(text)
    if plain:
        return "\n\n".join(plain).strip()
    return strip_html("\n\n".join(html)) if html else ""


def _mail_item(
    entry: _Entry, raw: bytes, sequencer: _IdSequencer, *, single: bool
) -> RawItem | None:
    try:
        message = message_from_bytes(raw)
    except Exception:  # noqa: BLE001 — one unparseable mail is skipped
        return None
    subject = _decode_header_value(message.get("Subject"))
    sender = _decode_header_value(message.get("From"))
    recipient = _decode_header_value(message.get("To"))
    copied = _decode_header_value(message.get("Cc"))
    date_raw = str(message.get("Date") or "")
    body_text = _mail_body(message)

    timestamp = ""
    if date_raw:
        try:
            timestamp = _iso_utc(email.utils.parsedate_to_datetime(date_raw))
        except (TypeError, ValueError, OverflowError):
            timestamp = ""

    message_id = str(message.get("Message-ID") or "").strip().strip("<>")
    natural = message_id or _hash_id(subject, sender, date_raw, body_text[:2000])

    header_lines = [f"From: {sender}" if sender else ""]
    if recipient:
        header_lines.append(f"To: {recipient}")
    if copied:
        header_lines.append(f"Cc: {copied}")
    if date_raw:
        header_lines.append(f"Date: {date_raw}")
    if subject:
        header_lines.append(f"Subject: {subject}")
    header = "\n".join(line for line in header_lines if line)

    return _item(
        entry,
        # A single .eml file IS the item: its id stays the file key so it
        # reads like every other one-item file in the inventory.
        natural_id="" if single else sequencer.unique(natural),
        body=f"{header}\n\n{body_text}".strip(),
        title=subject or entry.name,
        thread_key=_thread_key_for_subject(subject),
        author_raw=sender,
        timestamp_utc=timestamp,
        metadata={"message_id": message_id, "subject": subject},
    )


def _parse_mbox(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    sequencer = _IdSequencer()
    try:
        with entry.open_binary() as stream:
            for raw in _iter_mbox_messages(stream):
                item = _mail_item(entry, raw, sequencer, single=False)
                if item is not None:
                    yield item
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")


def _parse_eml(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    try:
        with entry.open_binary() as stream:
            raw = stream.read(MAX_MESSAGE_BYTES)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
        return
    item = _mail_item(entry, raw, _IdSequencer(), single=True)
    if item is not None:
        yield item


def _ical_moment(value: Any) -> tuple[str, bool]:
    """``(ISO-8601 UTC, is_all_day)`` for one icalendar date/datetime value."""
    from datetime import date as date_cls  # noqa: PLC0415 — tiny, local

    if isinstance(value, datetime):
        return _iso_utc(value), False
    if isinstance(value, date_cls):
        return f"{value.isoformat()}T00:00:00Z", True
    return "", False


def _parse_ics(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    try:
        from icalendar import Calendar  # noqa: PLC0415 — lazy (AP-26)
    except Exception as exc:  # noqa: BLE001 — a missing extra is not a crash
        report.refuse(
            entry.key,
            "calendar files need the 'icalendar' package, which is not "
            f"installed in this build ({type(exc).__name__})",
        )
        return
    text = _read_whole_text(entry, report)
    if text is None:
        return
    try:
        calendar = Calendar.from_ical(text)
    except Exception as exc:  # noqa: BLE001 — a malformed calendar is skipped
        report.refuse(entry.key, f"not a readable calendar ({type(exc).__name__})")
        return
    sequencer = _IdSequencer()
    for component in calendar.walk("VEVENT"):
        item = _ical_event_item(entry, component, sequencer)
        if item is not None:
            yield item


def _ical_event_item(
    entry: _Entry, component: Any, sequencer: _IdSequencer
) -> RawItem | None:
    def _text(name: str) -> str:
        try:
            value = component.get(name)
        except Exception:  # noqa: BLE001 — a broken property is not an event
            return ""
        return "" if value is None else str(value).strip()

    def _moment(name: str) -> tuple[str, bool]:
        try:
            value = component.get(name)
            return _ical_moment(getattr(value, "dt", None))
        except Exception:  # noqa: BLE001
            return "", False

    summary = _text("SUMMARY")
    description = _text("DESCRIPTION")
    location = _text("LOCATION")
    organizer = _text("ORGANIZER")
    start, all_day = _moment("DTSTART")
    end, _ = _moment("DTEND")

    attendees: list[str] = []
    try:
        raw_attendees = component.get("ATTENDEE")
    except Exception:  # noqa: BLE001
        raw_attendees = None
    if raw_attendees is not None:
        values = raw_attendees if isinstance(raw_attendees, list) else [raw_attendees]
        attendees = [str(value).replace("MAILTO:", "").strip() for value in values]

    uid = _text("UID")
    natural = uid or _hash_id(summary, start, location)

    lines = [f"Summary: {summary}" if summary else ""]
    if start:
        lines.append(f"Start: {start}")
    if end:
        lines.append(f"End: {end}")
    if all_day:
        lines.append("All day: yes")
    if location:
        lines.append(f"Location: {location}")
    if organizer:
        lines.append(f"Organizer: {organizer}")
    if attendees:
        lines.append(f"Attendees: {', '.join(attendees[:50])}")
    body = "\n".join(line for line in lines if line)
    if description:
        body = f"{body}\n\n{description}".strip()

    return _item(
        entry,
        natural_id=sequencer.unique(natural),
        body=body,
        title=summary or "Calendar event",
        author_raw=organizer,
        timestamp_utc=start,
        metadata={"uid": uid, "all_day": all_day, "attendees": attendees[:50]},
    )


#: vCard properties dropped from the body: base64 blobs (unreadable payload)
#: and the format's own bookkeeping (which says nothing about the person).
_VCARD_BLOB_PROPERTIES = frozenset(
    {"PHOTO", "LOGO", "SOUND", "KEY", "VERSION", "PRODID", "REV"}
)
_VCARD_MAX_VALUE_CHARS = 2000


def _parse_vcard(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    """Parse ``BEGIN:VCARD`` blocks with the stdlib only.

    vCard is a line-folded key/value grammar, not a format that needs a
    parser library: unfold continuation lines, split each property at its
    first colon, done. Base64 blobs (photos, keys) are dropped rather than
    stored as unreadable payload.
    """
    text = _read_whole_text(entry, report)
    if text is None:
        return
    sequencer = _IdSequencer()
    block: list[str] = []
    inside = False
    for raw_line in _unfold_vcard_lines(text):
        line = raw_line.strip()
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            inside, block = True, []
            continue
        if upper == "END:VCARD":
            if inside and block:
                item = _vcard_item(entry, block, sequencer)
                if item is not None:
                    yield item
            inside, block = False, []
            continue
        if inside and line:
            block.append(line)
    if inside and block:  # an export truncated mid-card is still a contact
        item = _vcard_item(entry, block, sequencer)
        if item is not None:
            yield item


def _unfold_vcard_lines(text: str) -> Iterator[str]:
    """vCard line unfolding: a leading space/tab continues the line before."""
    current = ""
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and current:
            current += raw[1:]
            continue
        if current:
            yield current
        current = raw
    if current:
        yield current


def _vcard_item(
    entry: _Entry, block: list[str], sequencer: _IdSequencer
) -> RawItem | None:
    name = ""
    uid = ""
    lines: list[str] = []
    for line in block:
        key, _, value = line.partition(":")
        prop = key.split(";", 1)[0].strip().upper()
        value = value.strip()
        if not prop or not value:
            continue
        if prop in _VCARD_BLOB_PROPERTIES:
            continue
        if prop == "FN" and not name:
            name = value
        elif prop == "UID" and not uid:
            uid = value
        lines.append(f"{prop.title()}: {value[:_VCARD_MAX_VALUE_CHARS]}")
    if not lines:
        return None
    natural = uid or _hash_id(name, "\n".join(lines[:20]))
    return _item(
        entry,
        natural_id=sequencer.unique(natural),
        body="\n".join(lines),
        title=name or "Contact",
        author_raw=name,
        metadata={"uid": uid},
    )


def _parse_tabular(
    entry: _Entry, report: ScanReport, *, delimiter: str
) -> Iterator[RawItem]:
    """Stream a CSV/TSV file as chunks of rows with the header as context.

    The header is repeated in EVERY chunk on purpose: a chunk retrieved on
    its own has to say what its columns mean, or the values are noise.
    """
    try:
        stream = _text_stream(entry)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
        return
    try:
        reader = csv.reader(stream, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            return
        header_line = "Columns: " + delimiter.join(header)
        chunk: list[str] = []
        first_row = 1
        row_number = 0
        for row in reader:
            row_number += 1
            chunk.append(delimiter.join(row))
            if len(chunk) >= TABULAR_CHUNK_ROWS:
                yield _tabular_item(entry, header_line, chunk, first_row, row_number)
                chunk = []
                first_row = row_number + 1
        if chunk:
            yield _tabular_item(entry, header_line, chunk, first_row, row_number)
    except (csv.Error, OSError, UnicodeError, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            stream.close()
        except OSError:  # pragma: no cover — closing a wrapper cannot fail
            pass


def _tabular_item(
    entry: _Entry, header_line: str, rows: list[str], first: int, last: int
) -> RawItem:
    return _item(
        entry,
        natural_id=f"rows-{first:08d}-{last:08d}",
        body=f"{header_line}\n\n" + "\n".join(rows),
        title=f"{entry.name} rows {first}-{last}",
        metadata={"row_start": first, "row_end": last},
    )


def _parse_jsonl(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    try:
        stream = _text_stream(entry)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
        return
    try:
        chunk: list[str] = []
        first_line = 1
        line_number = 0
        for raw in stream:
            line = raw.strip()
            line_number += 1
            if not line:
                continue
            chunk.append(line[:MAX_JSONL_LINE_CHARS])
            if len(chunk) >= TABULAR_CHUNK_ROWS:
                yield _jsonl_item(entry, chunk, first_line, line_number)
                chunk = []
                first_line = line_number + 1
        if chunk:
            yield _jsonl_item(entry, chunk, first_line, line_number)
    except (OSError, UnicodeError, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            stream.close()
        except OSError:  # pragma: no cover
            pass


def _jsonl_item(
    entry: _Entry, lines: list[str], first: int, last: int
) -> RawItem:
    return _item(
        entry,
        natural_id=f"lines-{first:08d}-{last:08d}",
        body="\n".join(lines),
        title=f"{entry.name} lines {first}-{last}",
        metadata={"line_start": first, "line_end": last},
    )


def _parse_json(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    """One JSON document as bounded pretty text.

    A document that does not parse is NOT dropped: it is imported as plain
    text instead, because a half-valid export is still the user's data and
    losing it silently would be worse than storing it unpretty.
    """
    text = _read_whole_text(entry, report)
    if text is None:
        return
    try:
        rendered = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (ValueError, RecursionError):
        rendered = text
    yield _item(
        entry,
        natural_id="",
        body=rendered,
        title=entry.name,
    )


def _parse_text(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    text = _read_whole_text(entry, report)
    if text is None:
        return
    yield _item(entry, natural_id="", body=text, title=_title_for_text(entry, text))


def _parse_html(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    markup = _read_whole_text(entry, report)
    if markup is None:
        return
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.IGNORECASE | re.DOTALL)
    if match:
        title = strip_html(match.group(1))[:200].strip()
    yield _item(
        entry,
        natural_id="",
        body=strip_html(markup),
        title=title or Path(entry.name).stem,
    )


_H1_RE = re.compile(r"^#{1,3} +(.+?)\s*$", re.MULTILINE)


def _title_for_text(entry: _Entry, body: str) -> str:
    match = _H1_RE.search(body[:4000])
    if match:
        return match.group(1).strip()[:200]
    for line in body[:2000].splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return Path(entry.name).stem


def _parse_pdf(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    """Extract a PDF's text page by page, stopping at the body cap.

    Encrypted and structurally broken PDFs are skipped with a reason on the
    record rather than failing the run — a folder of scans routinely holds
    one file no extractor can open.
    """
    try:
        from pypdf import PdfReader  # noqa: PLC0415 — lazy (AP-26)
    except Exception as exc:  # noqa: BLE001 — a missing extra is not a crash
        report.refuse(
            entry.key,
            "PDF files need the 'pypdf' package, which is not installed in "
            f"this build ({type(exc).__name__})",
        )
        return

    source: Any
    if entry.disk_path is not None:
        source = entry.disk_path
    else:
        try:
            with entry.open_binary() as stream:
                data = stream.read(MAX_BUFFERED_PDF_BYTES + 1)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
            return
        if len(data) > MAX_BUFFERED_PDF_BYTES:
            report.refuse(
                entry.key,
                "the PDF inside the archive is larger than "
                f"{MAX_BUFFERED_PDF_BYTES // (1024 * 1024)} MB",
            )
            return
        source = io.BytesIO(data)

    try:
        reader = PdfReader(source)
        if reader.is_encrypted:
            try:
                opened = reader.decrypt("")
            except Exception:  # noqa: BLE001 — an unsupported cipher is a skip
                opened = 0
            if not opened:
                report.refuse(entry.key, "the PDF is password protected")
                return
        pages = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 — a broken PDF is a skip, not a stop
        report.refuse(entry.key, f"the PDF could not be opened ({type(exc).__name__})")
        return

    chunks: list[str] = []
    length = 0
    extracted_pages = 0
    for index in range(pages):
        if length >= BODY_CAP:
            break
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception as exc:  # noqa: BLE001 — one bad page, not the file
            log.debug(
                "%s: page %d could not be extracted: %s",
                entry.key,
                index + 1,
                exc,
            )
            continue
        text = text.strip()
        if not text:
            continue
        extracted_pages += 1
        chunks.append(f"[page {index + 1}]\n{text}")
        length += len(text)
    if not chunks:
        report.refuse(
            entry.key,
            "the PDF holds no extractable text (it is most likely a scan "
            "without OCR)",
        )
        return
    yield _item(
        entry,
        natural_id="",
        body="\n\n".join(chunks),
        title=_pdf_title(reader) or Path(entry.name).stem,
        metadata={"pages": pages, "pages_with_text": extracted_pages},
    )


def _pdf_title(reader: Any) -> str:
    try:
        title = (reader.metadata or {}).get("/Title") or ""
    except Exception:  # noqa: BLE001 — broken metadata is not a broken PDF
        return ""
    return str(title).strip()[:200]


# --- WhatsApp ---------------------------------------------------------------

#: Matched message lines inspected before the file's date order is fixed.
_WA_ORDER_PROBE_LINES = 500


@dataclass(frozen=True, slots=True)
class _WhatsAppLine:
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    author: str
    text: str


def _wa_match(line: str) -> re.Match[str] | None:
    return _WA_BRACKET_RE.match(line) or _WA_DASH_RE.match(line)


def _wa_date_order(matches: list[re.Match[str]]) -> str:
    """``"dmy"`` or ``"mdy"`` for one file, decided from its own numbers.

    Day-first is the default because it is what most of the world's phones
    write; a value above 12 in either position is hard evidence and wins over
    the default.
    """
    for match in matches:
        first, second = int(match.group("a")), int(match.group("b"))
        if first > 12 and first <= 31:
            return "dmy"
        if second > 12 and second <= 31:
            return "mdy"
    return "dmy"


def _wa_parse_line(match: re.Match[str], order: str) -> _WhatsAppLine | None:
    a, b, c = int(match.group("a")), int(match.group("b")), int(match.group("c"))
    if a > 31:  # a four-digit year in front: an ISO-ish export
        year, month, day = a, b, c
    elif order == "dmy":
        day, month, year = a, b, c
    else:
        month, day, year = a, b, c
    if year < 100:
        year += 2000 if year < 70 else 1900
    hour, minute = int(match.group("hh")), int(match.group("mm"))
    ampm = (match.group("ampm") or "").lower().replace(".", "")
    if ampm.startswith("p") and hour < 12:
        hour += 12
    elif ampm.startswith("a") and hour == 12:
        hour = 0
    try:
        moment = datetime(year, month, day, hour, minute, tzinfo=UTC)
    except ValueError:
        return None
    return _WhatsAppLine(
        date=moment.strftime("%Y-%m-%d"),
        time=moment.strftime("%H:%M"),
        author=(match.group("author") or "").strip(),
        text=(match.group("text") or "").strip(),
    )


def _parse_whatsapp(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    """One item per (chat, day) — a day of a conversation is one memory.

    WhatsApp writes local wall-clock times with no zone anywhere in the
    export, so the timestamps are read as UTC and the item says so in its
    metadata rather than pretending to a precision the file does not carry.
    """
    chat = chat_name_from_filename(entry.name)
    try:
        stream = _text_stream(entry)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
        return
    try:
        yield from _iter_whatsapp_days(entry, stream, chat)
    except (OSError, UnicodeError, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            stream.close()
        except OSError:  # pragma: no cover
            pass


def _iter_whatsapp_days(
    entry: _Entry, stream: Any, chat: str
) -> Iterator[RawItem]:
    probe: list[str] = []
    probe_matches: list[re.Match[str]] = []
    for raw in stream:
        line = _normalize_line(raw.rstrip("\r\n"))
        probe.append(line)
        match = _wa_match(line)
        if match is not None:
            probe_matches.append(match)
            if len(probe_matches) >= _WA_ORDER_PROBE_LINES:
                break
    order = _wa_date_order(probe_matches)

    def _lines() -> Iterator[str]:
        yield from probe
        for raw in stream:
            yield _normalize_line(raw.rstrip("\r\n"))

    current_date = ""
    first_time = ""
    buffer: list[str] = []
    authors: list[str] = []
    for line in _lines():
        match = _wa_match(line)
        if match is None:
            if buffer:  # a wrapped continuation of the previous message
                buffer[-1] = f"{buffer[-1]}\n{line}"
            continue
        parsed = _wa_parse_line(match, order)
        if parsed is None:
            continue
        if parsed.date != current_date:
            if buffer:
                yield _whatsapp_item(
                    entry, chat, current_date, first_time, buffer, authors
                )
            current_date, first_time = parsed.date, parsed.time
            buffer, authors = [], []
        rendered = (
            f"{parsed.time} {parsed.author}: {parsed.text}"
            if parsed.author
            else f"{parsed.time} {parsed.text}"
        )
        buffer.append(rendered)
        if parsed.author and parsed.author not in authors:
            authors.append(parsed.author)
    if buffer:
        yield _whatsapp_item(entry, chat, current_date, first_time, buffer, authors)


def _whatsapp_item(
    entry: _Entry,
    chat: str,
    date: str,
    first_time: str,
    lines: list[str],
    authors: list[str],
) -> RawItem:
    return _item(
        entry,
        natural_id=date,
        body="\n".join(lines),
        title=f"{chat} ({date})",
        thread_key=chat,
        author_raw=", ".join(authors[:10]),
        timestamp_utc=f"{date}T{first_time or '00:00'}:00Z",
        metadata={
            "chat": chat,
            "day": date,
            "messages": len(lines),
            "participants": authors[:20],
            # No zone is recorded anywhere in a WhatsApp export.
            "timezone": "assumed-utc",
        },
    )


# --- Documents and media, through the shared extractor ----------------------


def _parse_document(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    """One Office / OpenDocument / EPUB file, read by the shared service.

    Not parsed here on purpose: those formats are supported once, in
    :mod:`jarvis.ultrawiki.extract`, so a folder walk and a dropped export
    read the same file identically. A file that yields no text is still
    stored, carrying the honest reason — a later run with the missing extra
    installed can then reclaim it instead of it never having existed.
    """
    from jarvis.ultrawiki.extract import extract_text  # noqa: PLC0415 — lazy (AP-26)

    source = _entry_bytes(entry, report, MAX_BUFFERED_DOCUMENT_BYTES, "document")
    if source is None:
        return
    result = extract_text(source, filename=entry.name)
    if result.ok:
        yield _item(
            entry,
            natural_id="",
            body=result.text,
            title=_document_title(entry, result.text),
            metadata={"document_kind": result.kind, **result.meta},
        )
        return
    yield _item(
        entry,
        natural_id="",
        body=_file_facts(entry, {}),
        title=Path(entry.name).stem,
        metadata={
            "document_kind": result.kind,
            "content_missing": True,
            "content_missing_reason": result.reason,
        },
    )


def _parse_media(entry: _Entry, report: ScanReport) -> Iterator[RawItem]:
    """A photo, recording or video: stored now, understood later.

    The body holds only what the FILE states — name, folder, capture date,
    camera, coordinates. That is enough to find it by time or place
    immediately, and it claims nothing about what the picture shows.
    """
    from jarvis.ultrawiki.extract import extract_text  # noqa: PLC0415 — lazy (AP-26)

    # Only the header is needed (EXIF sits in front of the image data), which
    # is what keeps a folder of 4K video from being read into memory.
    source = _entry_bytes(entry, report, MEDIA_HEADER_BYTES, "media header", partial=True)
    if source is None:
        return
    result = extract_text(source, filename=entry.name)
    facts = dict(result.meta)
    metadata: dict[str, Any] = {
        "media_kind": entry.fmt,
        "enrich_pending": True,
        **facts,
    }
    reference = _media_reference(entry)
    if reference is not None:
        metadata.update(reference.as_metadata())
    else:
        # Honest rather than silent: a tar entry cannot be reopened without
        # decompressing the whole stream again, so nothing will describe it.
        metadata["enrich_pending"] = False
        metadata["enrich_blocked_reason"] = (
            "this file came out of a tar archive, which cannot be reopened "
            "entry by entry; import the same export as a .zip, or unpack it "
            "first, to have it described"
        )
    captured = str(facts.get("captured_at") or "")
    yield _item(
        entry,
        natural_id="",
        body=_file_facts(entry, facts),
        title=Path(entry.name).stem,
        timestamp_utc=captured or entry.timestamp_utc,
        metadata=metadata,
    )


def _media_reference(entry: _Entry) -> Any:
    """A reopenable reference for this entry, or ``None`` when there is none."""
    from jarvis.ultrawiki.media import MediaRef  # noqa: PLC0415 — lazy (AP-26)

    if entry.disk_path is not None:
        return MediaRef(kind="file", path=str(entry.disk_path.resolve()))
    if entry.archive_kind == ARCHIVE_FORMAT and entry.archive_path:
        return MediaRef(
            kind="zip-entry", path=entry.archive_path, entry=entry.archive_entry
        )
    return None


def _document_title(entry: _Entry, text: str) -> str:
    heading = _H1_RE.search(text[:4000])
    if heading:
        return heading.group(1).strip()[:200]
    return Path(entry.name).stem


def _file_facts(entry: _Entry, facts: dict[str, Any]) -> str:
    """What is known about a file before anything has read its content.

    Every line comes from the file itself. Never a guess — an item that
    speculates about content it has not read is worse than no item at all.
    """
    lines = [f"File: {entry.name}"]
    folder = Path(entry.key.replace("!", "/")).parent.name
    if folder:
        lines.append(f"Folder: {folder}")
    for label, key in (("Taken", "captured_at"), ("Camera", "camera")):
        value = facts.get(key)
        if value:
            lines.append(f"{label}: {value}")
    latitude, longitude = facts.get("latitude"), facts.get("longitude")
    if latitude is not None and longitude is not None:
        lines.append(f"Location: {latitude}, {longitude}")
    return "\n".join(lines)


def _entry_bytes(
    entry: _Entry,
    report: ScanReport,
    limit: int,
    what: str,
    *,
    partial: bool = False,
) -> Any:
    """The entry's bytes, or its path when it has one. ``None`` when refused.

    A path is preferred wherever it exists: the extractors that can stream
    (PDF above all) then never hold the file in memory. ``partial`` reads only
    the leading ``limit`` bytes instead of refusing an oversized file, which is
    what a media header needs.
    """
    if entry.disk_path is not None:
        if partial:
            try:
                with entry.disk_path.open("rb") as stream:
                    return stream.read(limit)
            except OSError as exc:
                report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
                return None
        if entry.size > limit:
            report.refuse(
                entry.key,
                f"the {what} is larger than {limit // (1024 * 1024)} MB",
            )
            return None
        return entry.disk_path
    try:
        with entry.open_binary() as stream:
            data = stream.read(limit if partial else limit + 1)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.refuse(entry.key, f"{type(exc).__name__}: {exc}")
        return None
    if not partial and len(data) > limit:
        report.refuse(
            entry.key,
            f"the {what} inside the archive is larger than "
            f"{limit // (1024 * 1024)} MB, which is more than this import will "
            "hold in memory at once",
        )
        return None
    return data


_PARSERS: dict[str, Callable[[_Entry, ScanReport], Iterator[RawItem]]] = {
    "mbox": _parse_mbox,
    "eml": _parse_eml,
    "ics": _parse_ics,
    "vcard": _parse_vcard,
    "whatsapp": _parse_whatsapp,
    "csv": lambda entry, report: _parse_tabular(entry, report, delimiter=","),
    "tsv": lambda entry, report: _parse_tabular(entry, report, delimiter="\t"),
    "jsonl": _parse_jsonl,
    "json": _parse_json,
    "pdf": _parse_pdf,
    "document": _parse_document,
    "html": _parse_html,
    "markdown": _parse_text,
    "text": _parse_text,
    "image": _parse_media,
    "audio": _parse_media,
    "video": _parse_media,
}


# ---------------------------------------------------------------------------
# Preview / scan
# ---------------------------------------------------------------------------


def _count_occurrences(entry: _Entry, needle: bytes, remaining: list[int]) -> tuple[int, bool]:
    """Count ``needle`` at line starts, bounded by a shared byte budget."""
    total = 0
    exact = True
    try:
        with entry.open_binary() as stream:
            for line in stream:
                remaining[0] -= len(line)
                if line[: len(needle)].upper() == needle:
                    total += 1
                if remaining[0] <= 0:
                    exact = False
                    break
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return total, False
    return total, exact


def _count_mbox_messages(entry: _Entry, remaining: list[int]) -> tuple[int, bool]:
    """Count mbox separators with the SAME rule the parser splits on.

    A body line that happens to begin with ``From `` is not a separator, so
    counting every such line would inflate the preview against the import
    that follows it.
    """
    total = 0
    exact = True
    previous_blank = True
    try:
        with entry.open_binary() as stream:
            for line in stream:
                remaining[0] -= len(line)
                if previous_blank and line.startswith(b"From "):
                    total += 1
                previous_blank = not line.strip(b"\r\n")
                if remaining[0] <= 0:
                    exact = False
                    break
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return total, False
    return total, exact


def _count_lines(entry: _Entry, remaining: list[int]) -> tuple[int, bool]:
    total = 0
    exact = True
    try:
        with entry.open_binary() as stream:
            for line in stream:
                remaining[0] -= len(line)
                if line.strip():
                    total += 1
                if remaining[0] <= 0:
                    exact = False
                    break
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return total, False
    return total, exact


def _count_whatsapp_days(entry: _Entry, remaining: list[int]) -> tuple[int, bool]:
    days: set[str] = set()
    matches: list[re.Match[str]] = []
    exact = True
    try:
        with entry.open_binary() as stream:
            for raw in stream:
                remaining[0] -= len(raw)
                line = _normalize_line(raw.decode("utf-8", errors="replace").rstrip())
                match = _wa_match(line)
                if match is not None:
                    matches.append(match)
                    days.add(f"{match.group('a')}-{match.group('b')}-{match.group('c')}")
                if remaining[0] <= 0 or len(days) > 20000:
                    exact = False
                    break
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return len(days), False
    return len(days), exact


def _estimate_items(
    entry: _Entry, remaining: list[int]
) -> tuple[int, bool]:
    """``(item estimate, exact)`` for one entry, without parsing any body."""
    fmt = entry.fmt
    if fmt == "mbox":
        return _count_mbox_messages(entry, remaining)
    if fmt == "ics":
        return _count_occurrences(entry, b"BEGIN:VEVENT", remaining)
    if fmt == "vcard":
        return _count_occurrences(entry, b"BEGIN:VCARD", remaining)
    if fmt == "whatsapp":
        return _count_whatsapp_days(entry, remaining)
    if fmt in ("csv", "tsv"):
        rows, exact = _count_lines(entry, remaining)
        body_rows = max(0, rows - 1)
        return -(-body_rows // TABULAR_CHUNK_ROWS), exact
    if fmt == "jsonl":
        lines, exact = _count_lines(entry, remaining)
        return -(-lines // TABULAR_CHUNK_ROWS), exact
    # eml / json / pdf / html / markdown / text are exactly one item each.
    return 1, True


def scan_export(
    path: str | Path, *, budget_files: int = 5000
) -> dict[str, Any]:
    """Report what a drop holds, without importing anything.

    A cheap streaming pass: it detects each file's format, counts the records
    inside it, and never materialises a body. Counting still means reading
    bytes, so the byte budget (:data:`SCAN_BYTES_BUDGET`) and ``budget_files``
    bound the work — when either runs out the answer says ``truncated`` and
    the affected formats stop claiming to be ``exact``, instead of quietly
    reporting a number that is only part of the truth.

    BLOCKING — callers run it through ``asyncio.to_thread``.
    """
    root = Path(str(path)).expanduser()
    report = ScanReport()
    if not root.exists():
        report.note("Nothing is at that path.")
        payload = report.as_dict()
        payload.update({"path": str(root), "exists": False, "is_dir": False})
        return payload

    budget = _Budget()
    remaining = [SCAN_BYTES_BUDGET]
    walked = 0
    for entry in _walk_entries(root, report, budget):
        walked += 1
        if walked > max(1, int(budget_files)):
            report.truncated = True
            report.note(
                f"More than {budget_files} files were found; the preview "
                "stopped counting there. The import itself reads everything."
            )
            break
        count, exact = _estimate_items(entry, remaining)
        if not exact:
            report.truncated = True
        report.add_items(entry.fmt, count, exact=exact)
    if budget.exhausted:
        report.truncated = True
    payload = report.as_dict()
    payload.update({"path": str(root), "exists": True, "is_dir": root.is_dir()})
    return payload


# ---------------------------------------------------------------------------
# Upload destination
# ---------------------------------------------------------------------------


def uploads_dir(data_dir: str | Path | None = None) -> Path:
    """The directory dropped export files are streamed into.

    Anchored exactly like :func:`jarvis.ultrawiki.store.resolve_ultrawiki_db_path`
    so a relative ``memory.data_dir`` ("./data", the default) resolves against
    the repo root rather than whatever the process CWD happens to be.
    """
    from jarvis.core.paths import repo_root  # noqa: PLC0415 — lazy (AP-26)

    raw = Path(data_dir) if data_dir is not None else Path("data")
    directory = raw if raw.is_absolute() else repo_root() / raw
    return (directory / "ultrawiki" / "uploads").resolve(strict=False)


# ---------------------------------------------------------------------------
# The connector
# ---------------------------------------------------------------------------


def _origin_of(checkpoint: str) -> str:
    """The on-disk file an ``external_id`` came from (the resume unit).

    Split at the FIRST ``#`` and the FIRST ``!``. A path that itself contains
    one of those characters therefore yields a SHORTER prefix, which sorts
    earlier and makes the resume re-read more than it strictly must — the
    safe direction, since every re-read upserts as unchanged.
    """
    head = checkpoint.split("#", 1)[0]
    return head.split("!", 1)[0]


def _drain(iterator: Iterator[RawItem], size: int) -> list[RawItem]:
    """Pull up to ``size`` items out of the blocking walk (worker thread)."""
    batch: list[RawItem] = []
    for item in iterator:
        batch.append(item)
        if len(batch) >= size:
            break
    return batch


class ExportImportConnector:
    """Import a dropped export file or folder — whatever formats it holds.

    Config keys:

    - ``path`` (required): the file or folder to read. A folder is walked
      recursively; hidden entries and ``__MACOSX`` are skipped.

    Everything else is detection. See the module docstring for the resume
    contract and the archive rules.
    """

    id = "export-import"
    label = "Export file import"
    auth = AuthKind.EXPORT_FILE
    capabilities = ConnectorCapabilities(
        backfill=True,
        # An export file is a snapshot: there is no cursor to poll and no
        # tombstone to detect. Re-importing the same drop is a full,
        # idempotent re-read.
        incremental=IncrementalMode.NONE,
        deletes=False,
    )

    async def backfill(
        self, ctx: ConnectorContext, checkpoint: str | None = None
    ) -> AsyncIterator[RawItem]:
        root = await asyncio.to_thread(self._resolve_path, ctx)
        if root is None:
            return
        report = ScanReport()
        iterator = self._iter_items(root, checkpoint, report)
        try:
            while True:
                batch = await asyncio.to_thread(_drain, iterator, ITEM_BATCH)
                if not batch:
                    break
                for item in batch:
                    yield item
        finally:
            # Closing the blocking generator runs its `finally` blocks, which
            # is what closes every archive and stream it still holds. Cheap
            # enough to do inline even on a cancelled sync.
            iterator.close()
            self._log_report(ctx, report)

    async def incremental(
        self, ctx: ConnectorContext, cursor: str | None = None
    ) -> AsyncIterator[RawItem]:
        """No incremental mode: a dropped file does not change under us.

        Declared ``IncrementalMode.NONE``, so the scheduler never calls this;
        it exists to satisfy the protocol and yields nothing.
        """
        return
        yield  # pragma: no cover - unreachable, keeps this an async generator

    # ------------------------------------------------------------------
    # Blocking internals — only ever reached through asyncio.to_thread
    # ------------------------------------------------------------------

    def _resolve_path(self, ctx: ConnectorContext) -> Path | None:
        raw = ctx.config.get("path") or ctx.config.get("root")
        if not raw:
            log.warning(
                "%s: source %s has no 'path' configured; yielding nothing",
                self.id,
                ctx.source_id,
            )
            return None
        root = Path(str(raw)).expanduser()
        if not root.exists():
            log.warning(
                "%s: %s does not exist; yielding nothing", self.id, root
            )
            return None
        return root

    def _iter_items(
        self, root: Path, checkpoint: str | None, report: ScanReport
    ) -> Iterator[RawItem]:
        resume_origin = _origin_of(checkpoint) if checkpoint else ""
        budget = _Budget()
        for entry in _walk_entries(root, report, budget):
            if resume_origin and entry.origin < resume_origin:
                continue
            parser = _PARSERS.get(entry.fmt)
            if parser is None:  # pragma: no cover - detection only emits known ids
                report.count_unknown(entry.name, entry.size)
                continue
            yield from parser(entry, report)

    def _log_report(self, ctx: ConnectorContext, report: ScanReport) -> None:
        """One honest summary line per run: what was read, what was not.

        The notes and refusals are the WHOLE point on a run that produced
        nothing — "the archive nests deeper than we follow" is exactly the
        line a user needs when their import came back empty.
        """
        if not (
            report.files_seen
            or report.unreadable
            or report.notes
            or report.unknown
        ):
            return
        formats = ", ".join(
            f"{fmt}x{row['files']}" for fmt, row in sorted(report.formats.items())
        )
        log.info(
            "%s: source %s read %d file(s) [%s]; %d skipped as unrecognised, "
            "%d could not be read",
            self.id,
            ctx.source_id,
            report.files_seen,
            formats or "none",
            sum(report.unknown.values()),
            len(report.unreadable),
        )
        for row in report.unreadable[:20]:
            log.info("%s: skipped %s — %s", self.id, row["path"], row["reason"])
        for note in report.notes:
            log.warning("%s: %s", self.id, note)
