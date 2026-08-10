"""Get the readable text out of one file, whatever format it is in.

The gap this closes (audit, 2026-07-26): every reader that met a binary gave
up. Google Drive produced no item at all for a PDF or a Word document, Dropbox
had an extension allowlist that excluded exactly the formats people keep their
thinking in, Gmail never downloaded an attachment, and the local-folder walk
read `.md` and `.txt` and nothing else. A contract, a slide deck, a scanned
invoice, a spreadsheet of decisions — all invisible to a system whose whole
purpose is remembering them.

One service, so a format is supported once rather than per reader.

**Almost all of it is standard library, on purpose.** Word, Excel, PowerPoint,
OpenDocument and EPUB files are ZIP archives containing XML; `zipfile` plus
`xml.etree` reads them without `python-docx`, `openpyxl`, `python-pptx`,
`odfpy` or `ebooklib`. That is not cleverness for its own sake — every avoided
dependency is one that cannot fail to build a wheel on the maintainer's Intel
Mac, cannot bloat a `python:3.11-slim` image, and cannot break a base install
(repo rule §3). PDF is the one exception: it needs `pypdf`, which is already a
dependency.

**Never raises, always answers.** A corrupt file, a missing optional
dependency, an encrypted PDF and a JPEG are four different outcomes and each
returns an :class:`ExtractResult` saying which. A reader stores the item
regardless and marks `content_missing`, so a later run — with the extra
installed, or after the file is repaired — can reclaim it instead of the item
silently never existing.

**Format is decided by content first, name second.** Magic bytes do not lie;
extensions do, constantly (`.txt` holding a PDF, a `.docx` that is really a
ZIP of something else, no extension at all on a Drive export).

Relationship to :mod:`jarvis.ultrawiki.document_text`: that module is the
folder walk's path-based helper for the three formats a notes folder actually
holds. This one is the general service — ten formats, and it accepts BYTES,
which is what an email attachment, a Slack upload or a Drive export is before
it ever touches disk. They share the hardened XML parser rather than each
keeping a copy: office archives arrive from strangers, and a second copy of a
security primitive is a second thing to forget to update.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from jarvis.ultrawiki.document_text import safe_xml_parser

log = logging.getLogger(__name__)

__all__ = [
    "DOCUMENT_EXTENSIONS",
    "ExtractResult",
    "MAX_DOCUMENT_BYTES",
    "MEDIA_EXTENSIONS",
    "MEDIA_KINDS",
    "TEXT_EXTENSIONS",
    "detect_kind",
    "extract_text",
    "is_probably_text",
]

#: Bytes read to identify a file by content.
_SNIFF_BYTES = 8192

#: A single extracted document is bounded only against pathological input —
#: the chunker handles length, so this is not a content cap. 64 MB of TEXT is
#: about 30 000 pages; beyond it the file is a database, not a document.
_MAX_TEXT_CHARS = 64 * 1024 * 1024

#: Ceiling for reading a file WHOLE, shared by every walker and adapter so the
#: policy has one home. It rejects rather than truncates, and that is the
#: point: a container format cannot be cut. Half a ZIP has no central
#: directory and half a PDF has no cross-reference table — both read as
#: nothing at all, so a range-limited download would turn a large document
#: into a silent empty item instead of an honest oversized one.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

# Archive extraction limits. A DOCX can be only a few kilobytes on disk while
# claiming gigabytes of repeated XML after decompression, so the compressed
# file limit above is necessary but not sufficient.
_MAX_ZIP_MEMBERS = 4096
_MAX_ZIP_COMPRESSION_RATIO = 200
_ZIP_RATIO_CHECK_MIN_BYTES = 1024 * 1024

#: Leading magic → kind. Order matters: ZIP-based office formats share the
#: `PK` signature and are separated afterwards by their inner layout.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"{\\rtf", "rtf"),
    (b"\x89PNG\r\n\x1a\n", "image"),
    (b"\xff\xd8\xff", "image"),
    (b"GIF87a", "image"),
    (b"GIF89a", "image"),
    (b"BM", "image"),
    (b"\x00\x00\x01\x00", "image"),
    (b"OggS", "audio"),
    (b"ID3", "audio"),
    (b"fLaC", "audio"),
    (b"\x1f\x8b", "gzip"),
    (b"7z\xbc\xaf\x27\x1c", "archive"),
    (b"Rar!\x1a\x07", "archive"),
    (b"\xd0\xcf\x11\xe0", "legacy_office"),  # .doc/.xls/.ppt (OLE2)
    (b"SQLite format 3\x00", "binary"),
    (b"\x7fELF", "binary"),
    (b"MZ", "binary"),
)

#: Inner ZIP members that identify an office format.
_ZIP_MARKERS: tuple[tuple[str, str], ...] = (
    ("word/document.xml", "docx"),
    ("xl/workbook.xml", "xlsx"),
    ("ppt/presentation.xml", "pptx"),
    ("content.xml", "odf"),
    ("META-INF/container.xml", "epub"),
)

#: Second-pass identification: a member PREFIX rather than an exact manifest
#: part, for archives that lost their manifest but kept their content.
_ZIP_LAYOUT_MARKERS: tuple[tuple[str, str], ...] = (
    ("ppt/slides/", "pptx"),
    ("word/", "docx"),
    ("xl/worksheets/", "xlsx"),
    ("xl/", "xlsx"),
    ("OEBPS/", "epub"),
    ("EPUB/", "epub"),
)

#: Last resort: the name, for a ZIP that identified as nothing at all.
_ZIP_SUFFIX_KINDS: dict[str, str] = {
    ".docx": "docx", ".docm": "docx",
    ".xlsx": "xlsx", ".xlsm": "xlsx",
    ".pptx": "pptx", ".pptm": "pptx",
    ".odt": "odf", ".ods": "odf", ".odp": "odf", ".odg": "odf",
    ".epub": "epub",
}

#: Kinds that hold meaning no text extractor can reach, but a model can.
#: The enrichment stage reads exactly these; everything else it ignores.
MEDIA_KINDS: frozenset[str] = frozenset({"image", "audio", "video"})

#: ISO-BMFF brand (bytes 8..12 after an `ftyp` box) → kind. One container
#: family holds an iPhone photo, a voice memo and a video, and the extension
#: routinely disagrees with all three — an iPhone photo shared through a chat
#: arrives as `.jpg` holding HEIC, and a WhatsApp voice note is `.opus` inside
#: an Ogg stream. The brand is the only honest answer.
_FTYP_BRANDS: tuple[tuple[bytes, str], ...] = (
    (b"heic", "image"), (b"heix", "image"), (b"heim", "image"),
    (b"heis", "image"), (b"hevc", "image"), (b"mif1", "image"),
    (b"msf1", "image"), (b"avif", "image"), (b"avis", "image"),
    (b"M4A ", "audio"), (b"M4B ", "audio"), (b"M4P ", "audio"),
    (b"qt  ", "video"), (b"M4V ", "video"), (b"M4VP", "video"),
    (b"isom", "video"), (b"iso2", "video"), (b"iso4", "video"),
    (b"iso5", "video"), (b"iso6", "video"), (b"mp41", "video"),
    (b"mp42", "video"), (b"mp4v", "video"), (b"avc1", "video"),
    (b"3gp4", "video"), (b"3gp5", "video"), (b"3g2a", "video"),
)

#: RIFF sub-format (bytes 8..12) → kind.
_RIFF_FORMS: tuple[tuple[bytes, str], ...] = (
    (b"WEBP", "image"),
    (b"WAVE", "audio"),
    (b"AVI ", "video"),
)

#: Leading magic for media families that need no container inspection.
_MEDIA_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"II*\x00", "image"),          # TIFF, little-endian
    (b"MM\x00*", "image"),          # TIFF, big-endian
    (b"\x1aE\xdf\xa3", "video"),    # Matroska / WebM
    (b"FLV\x01", "video"),          # Flash video
    (b"\xff\xfb", "audio"),         # MPEG-1 layer 3, no ID3 tag
    (b"\xff\xf3", "audio"),
    (b"\xff\xf2", "audio"),
    (b"\xff\xf1", "audio"),         # AAC (ADTS)
    (b"MAC \x00", "audio"),         # Monkey's Audio
    (b"wvpk", "audio"),             # WavPack
)

#: A tar header carries its magic at offset 257, not at the start — the one
#: format in this module that cannot be identified from a leading prefix.
_TAR_MAGIC_OFFSET = 257
_TAR_MAGICS: tuple[bytes, ...] = (b"ustar\x0000", b"ustar  \x00", b"ustar\x00")

#: Suffixes that mark a gzip stream as a COMPRESSED TAR rather than a single
#: gzipped file. Google Takeout's default download is exactly this.
_TGZ_SUFFIXES: tuple[str, ...] = (".tgz", ".tar.gz", ".taz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")

#: Extensions whose contents are text even when they look unfamiliar. Not an
#: allowlist that decides what gets imported — a hint for the sniffer. Anything
#: that decodes cleanly is treated as text regardless of its name.
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        # prose + notes
        ".txt", ".text", ".md", ".markdown", ".rst", ".org", ".adoc", ".tex",
        ".log", ".srt", ".vtt", ".sub",
        # data + config
        ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".conf", ".properties", ".env", ".csv", ".tsv", ".tab", ".xml", ".plist",
        # code
        ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".rs",
        ".go", ".java", ".kt", ".kts", ".scala", ".swift", ".c", ".h", ".cc",
        ".cpp", ".hpp", ".cs", ".rb", ".php", ".pl", ".lua", ".r", ".jl",
        ".dart", ".ex", ".exs", ".erl", ".hs", ".clj", ".sql", ".sh", ".bash",
        ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd", ".vim", ".el",
        ".gradle", ".groovy", ".m", ".mm", ".f90", ".asm", ".s",
        # web + templates
        ".html", ".htm", ".xhtml", ".css", ".scss", ".sass", ".less", ".vue",
        ".svelte", ".astro", ".hbs", ".ejs", ".jinja", ".j2", ".twig",
        # build + infra
        ".dockerfile", ".tf", ".tfvars", ".gitignore", ".gitattributes",
        ".editorconfig", ".graphql", ".gql", ".proto", ".patch", ".diff",
    }
)

#: Extension → media kind, consulted only when the content was inconclusive.
#: A file the sniffer could not place is still worth calling a photo when it
#: is named like one: a truncated download, an unusual camera profile or a
#: RAW format nobody ships magic for is exactly the picture a person cares
#: about most.
_MEDIA_EXTENSIONS: dict[str, str] = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".jpe": "image",
    ".gif": "image", ".bmp": "image", ".webp": "image", ".tiff": "image",
    ".tif": "image", ".heic": "image", ".heif": "image", ".avif": "image",
    ".psd": "image", ".ico": "image",
    # Camera RAW: one per major vendor, because a photographer's archive is
    # otherwise entirely invisible to a memory system.
    ".raw": "image", ".dng": "image", ".cr2": "image", ".cr3": "image",
    ".nef": "image", ".arw": "image", ".orf": "image", ".rw2": "image",
    ".raf": "image", ".srw": "image", ".pef": "image",
    ".mp3": "audio", ".wav": "audio", ".flac": "audio", ".ogg": "audio",
    ".oga": "audio", ".opus": "audio", ".m4a": "audio", ".m4b": "audio",
    ".aac": "audio", ".wma": "audio", ".aiff": "audio", ".aif": "audio",
    ".amr": "audio", ".3ga": "audio", ".caf": "audio", ".wv": "audio",
    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
    ".webm": "video", ".wmv": "video", ".flv": "video", ".m4v": "video",
    ".mpg": "video", ".mpeg": "video", ".3gp": "video", ".3g2": "video",
    ".mts": "video", ".m2ts": "video", ".ogv": "video",
    # `.ts` is deliberately absent: it is TypeScript far more often than it is
    # an MPEG transport stream, and calling a source file a video would drop it
    # from a developer's own knowledge base.
}

#: Public view of the mapping above, for the walkers that decide which files to
#: even look at. A folder walk needs the list BEFORE it opens anything.
MEDIA_EXTENSIONS: frozenset[str] = frozenset(_MEDIA_EXTENSIONS)

#: Suffixes this service can turn into text but a plain UTF-8 read cannot.
#: Same purpose: a walker's allowlist has to name them or they are never seen.
DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf", ".docx", ".xlsx", ".pptx", ".epub", ".rtf",
        ".odt", ".ods", ".odp", ".odg", ".odf",
        # Pre-2007 Office: not readable, but naming them means the user gets
        # the "re-save it in the modern format" line instead of silence.
        ".doc", ".xls", ".ppt",
    }
)

#: Files whose NAME says binary regardless of what decodes. Media extensions
#: are deliberately NOT here any more — they resolve to their media kind
#: above, which is what makes a photo library importable at all.
_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".svgz", ".ai",
        ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tar", ".tgz", ".jar",
        ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".db", ".sqlite",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".pyc", ".pyo", ".class", ".o", ".a", ".lib", ".wasm",
    }
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]{2,}")
_BLANKS_RE = re.compile(r"\n{3,}")
_RTF_CONTROL_RE = re.compile(r"\\[a-zA-Z]+-?\d* ?|[{}]|\\\n")


@dataclass(slots=True)
class ExtractResult:
    """What one file yielded, and honestly why when it yielded nothing.

    ``ok`` false with a non-empty ``reason`` is a first-class outcome, not an
    error: an image has no text to give, and saying so lets the caller store
    the item with ``content_missing`` rather than drop it.
    """

    text: str = ""
    #: The format actually detected ("pdf", "docx", "text", "image", ...).
    kind: str = "unknown"
    ok: bool = False
    reason: str = ""
    #: Free-form extras a reader may pass through (page counts, sheet names).
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def content_missing(self) -> bool:
        """True when text COULD exist but this build/file did not produce it.

        Distinguishes "nothing to extract" (an image) from "we could not
        extract it" (no pypdf, a corrupt archive) — only the second is worth
        retrying later.
        """
        return not self.ok and self.kind not in MEDIA_KINDS and self.kind != "empty"

    @property
    def media_kind(self) -> str:
        """``image`` / ``audio`` / ``video`` when this file carries meaning a
        model could read; ``""`` for everything else.

        The third outcome the original two could not express. A photo is
        neither "extracted" nor "we failed to extract it" — it is *not text,
        and never will be*, while still being one of the most memory-dense
        things a person owns. Naming that case is what lets a later stage
        describe the picture instead of the file being quietly worthless.
        """
        return self.kind if self.kind in MEDIA_KINDS else ""


def _read(data: bytes | Path) -> bytes:
    return data.read_bytes() if isinstance(data, Path) else bytes(data)


def _head(data: bytes | Path) -> bytes:
    if isinstance(data, Path):
        try:
            with data.open("rb") as handle:
                return handle.read(_SNIFF_BYTES)
        except OSError:
            return b""
    return bytes(data[:_SNIFF_BYTES])


def is_probably_text(head: bytes) -> bool:
    """Whether a leading sample decodes as text without binary noise.

    A NUL byte is the practical separator: real text files do not contain one,
    and every binary format of consequence does within its first few KB.

    UTF-8 is not enough on its own. A Windows-authored note is cp1252, an older
    export is latin-1, and "Gr\u00fc\u00dfe aus M\u00fcnchen" in either one is invalid UTF-8 \u2014
    a UTF-8-only check calls that file binary and drops it, which is precisely
    the class of everyday document this service exists to stop losing. So a
    failed UTF-8 decode falls through to the legacy encodings, and only text
    that survives NONE of them, or that carries control characters no prose
    contains, is called binary.
    """
    if not head:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        pass
    for encoding in ("cp1252", "latin-1"):
        try:
            decoded = head.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # Control characters other than the usual whitespace mean binary; real
        # single-byte text has none.
        controls = sum(
            1 for ch in decoded if ord(ch) < 32 and ch not in "\t\n\r\f\v"
        )
        if controls <= len(decoded) * 0.01:
            return True
    # A truncated multi-byte character at the sample boundary is normal, so a
    # mostly-clean lenient decode still counts as text.
    lenient = head.decode("utf-8", errors="replace")
    return lenient.count("\ufffd") <= max(2, len(lenient) * 0.02)


def _zip_kind(data: bytes | Path, filename: str = "") -> str:
    """Which office format a ZIP actually is, or plain ``archive``.

    Three passes, weakest evidence last. The manifest part is conclusive, but
    an archive can be missing it and still hold every slide: a file repaired by
    a converter, truncated by a sync client, or produced by a tool that writes
    only what it needs. Falling straight to "archive" there discards a deck
    whose text is sitting right in front of us, so the layout of the members —
    and finally the name — get their turn.
    """
    try:
        source: Any = data if isinstance(data, Path) else io.BytesIO(data)
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
    except Exception:  # noqa: BLE001 — a broken zip is just not an office file
        return "archive"
    for marker, kind in _ZIP_MARKERS:
        if marker in names:
            return kind
    for prefix, kind in _ZIP_LAYOUT_MARKERS:
        if any(name.startswith(prefix) for name in names):
            return kind
    suffix = Path(filename).suffix.lower() if filename else ""
    return _ZIP_SUFFIX_KINDS.get(suffix, "archive")


def _container_kind(head: bytes) -> str:
    """Media kind of an ISO-BMFF or RIFF container, or ``""``.

    Both families put a four-byte form marker at offset 8, and both are used
    for pictures, sound and film alike — so the outer magic alone says nothing
    useful. `.heic` from an iPhone and `.mp4` from the same phone differ only
    here.
    """
    if len(head) >= 12:
        if head[4:8] == b"ftyp":
            brand = head[8:12]
            for marker, kind in _FTYP_BRANDS:
                if brand == marker:
                    return kind
            # An unlisted brand is still an ISO-BMFF file, and those are
            # overwhelmingly video. Better a video we transcribe than a
            # "binary" we drop.
            return "video"
        if head[:4] == b"RIFF":
            form = head[8:12]
            for marker, kind in _RIFF_FORMS:
                if form == marker:
                    return kind
    return ""


def _is_tar(data: bytes | Path) -> bool:
    """Whether these bytes are an uncompressed tar (magic lives at offset 257)."""
    header = b""
    if isinstance(data, Path):
        try:
            with data.open("rb") as handle:
                header = handle.read(_TAR_MAGIC_OFFSET + 8)
        except OSError:
            return False
    else:
        header = bytes(data[: _TAR_MAGIC_OFFSET + 8])
    if len(header) < _TAR_MAGIC_OFFSET + 5:
        return False
    tail = header[_TAR_MAGIC_OFFSET:]
    return any(tail.startswith(magic) for magic in _TAR_MAGICS)


def _looks_like_tarball(filename: str) -> bool:
    """Whether a compressed stream's NAME says it wraps a tar archive.

    Compression carries no record of what it compressed, so the name is the
    only evidence available without decompressing — and `.tar.gz` is a double
    suffix `Path.suffix` alone does not see.
    """
    lowered = Path(filename).name.lower()
    return any(lowered.endswith(suffix) for suffix in _TGZ_SUFFIXES)


def detect_kind(data: bytes | Path, filename: str = "", mime: str = "") -> str:
    """The file's real format — content first, name second, mime last.

    Extensions lie constantly (a `.txt` holding a PDF, a Drive export with no
    extension at all), so the magic bytes decide whenever they are conclusive.
    """
    head = _head(data)
    if not head:
        return "empty"

    # Containers before flat magic: `RIFF` and `ftyp` files would otherwise
    # fall through to the extension, which is where HEIC photos got lost.
    container = _container_kind(head)
    if container:
        return container
    for magic, kind in _MEDIA_MAGIC:
        if head.startswith(magic):
            return kind
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            if kind == "gzip" and _looks_like_tarball(filename):
                return "tar"
            return kind
    if head.startswith(b"PK\x03\x04"):
        return _zip_kind(data, filename)
    if _is_tar(data):
        return "tar"

    suffix = Path(filename).suffix.lower() if filename else ""
    media = _MEDIA_EXTENSIONS.get(suffix, "")
    if media:
        return media
    if suffix in _BINARY_EXTENSIONS:
        return "binary"
    lowered = (mime or "").lower()
    if lowered.startswith("image/"):
        return "image"
    if lowered.startswith("audio/"):
        return "audio"
    if lowered.startswith("video/"):
        return "video"
    if lowered == "application/pdf":
        return "pdf"

    if is_probably_text(head):
        if suffix in {".html", ".htm", ".xhtml"} or head.lstrip()[:6].lower() in (
            b"<html>",
            b"<!doct",
        ):
            return "html"
        return "text"
    return "binary"


def _clean(text: str) -> str:
    """Collapse the whitespace an extractor leaves behind, keep the structure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = _BLANKS_RE.sub("\n\n", text)
    return text.strip()[:_MAX_TEXT_CHARS]


#: UTF-16 is only ever tried when the file SAYS it is UTF-16. Without a byte
#: order mark the codec accepts almost any even-length input and silently
#: produces CJK-looking noise: `b"caf\xe9 notes"` — an ordinary Windows-encoded
#: note — decoded to `"慣ﺩ潮整"` and was stored that way, unfindable and
#: unreadable, with nothing anywhere reporting a problem. A BOM is the only
#: honest evidence for this encoding.
_UTF16_BOMS: tuple[bytes, ...] = (b"\xff\xfe", b"\xfe\xff")


def _decode(raw: bytes) -> str:
    """Text from bytes, trying the encodings that actually occur in the wild."""
    encodings = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
    if raw[:2] in _UTF16_BOMS:
        encodings = ("utf-16", *encodings)
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_markup(markup: str) -> str:
    """Tags out, text in — with block elements becoming line breaks.

    Deliberately not an HTML parser: this runs over XML from office archives
    as well, and all that is wanted is the readable run of text.
    """
    markup = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(
        r"(?i)</(p|div|br|li|tr|h[1-6]|section|article)\s*/?>", "\n", markup
    )
    markup = re.sub(r"(?i)<br\s*/?>", "\n", markup)
    text = _TAG_RE.sub(" ", markup)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
    ):
        text = text.replace(entity, char)
    return text


def _zip_members(data: bytes | Path, predicate: Any) -> list[tuple[str, bytes]]:
    """Matching members of a ZIP, in archive order. Empty on any failure."""
    try:
        source: Any = data if isinstance(data, Path) else io.BytesIO(data)
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > _MAX_ZIP_MEMBERS:
                log.warning("extract: refusing archive with too many members")
                return []
            selected = [member for member in members if predicate(member.filename)]
            if sum(member.file_size for member in selected) > MAX_DOCUMENT_BYTES:
                log.warning("extract: refusing archive with oversized extracted content")
                return []
            for member in selected:
                if member.file_size > MAX_DOCUMENT_BYTES:
                    log.warning("extract: refusing oversized archive member")
                    return []
                if member.file_size < _ZIP_RATIO_CHECK_MIN_BYTES:
                    continue
                if member.compress_size == 0:
                    log.warning("extract: refusing archive member with invalid compression size")
                    return []
                if member.file_size / member.compress_size > _MAX_ZIP_COMPRESSION_RATIO:
                    log.warning("extract: refusing archive member with unsafe compression ratio")
                    return []
            return [(member.filename, archive.read(member)) for member in selected]
    except Exception:  # noqa: BLE001 — a broken archive yields nothing, never raises
        log.debug("zip member read failed", exc_info=True)
        return []


def _parse_xml(raw: bytes) -> ElementTree.Element | None:
    """Parse office XML with entity declarations refused. ``None`` on anything.

    Office archives arrive from strangers — a mail attachment, a shared Drive
    file — so the "billion laughs" entity bomb is a real input, not a
    theoretical one. The shared parser refuses the DECLARATION rather than
    trying to bound the expansion, and a DTD in the bytes is refused before
    parsing starts.
    """
    if b"<!DOCTYPE" in raw[:4096] or b"<!ENTITY" in raw[:4096]:
        log.debug("extract: refusing office XML that declares a DTD or entity")
        return None
    try:
        return ElementTree.fromstring(raw, parser=safe_xml_parser())  # noqa: S314 — hardened
    except Exception:  # noqa: BLE001 — a refused or broken part yields nothing
        log.debug("extract: office XML part unreadable", exc_info=True)
        return None


def _from_docx(data: bytes | Path) -> ExtractResult:
    """Word text — `word/document.xml` plus headers, footers and notes.

    A paragraph is `<w:p>`; making each one a line is what keeps a contract's
    clauses from running into one wall of text.
    """
    members = _zip_members(
        data,
        lambda n: n == "word/document.xml"
        or n.startswith(("word/header", "word/footer"))
        or n in ("word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"),
    )
    if not members:
        return ExtractResult(kind="docx", reason="the document part is unreadable")
    parts: list[str] = []
    for _name, raw in members:
        xml = _decode(raw)
        xml = re.sub(r"(?i)</w:p>", "\n", xml)
        xml = re.sub(r"(?i)</w:tr>", "\n", xml)
        xml = re.sub(r"(?i)</w:tc>", "\t", xml)
        parts.append(_strip_markup(xml))
    text = _clean("\n".join(parts))
    return ExtractResult(
        text=text,
        kind="docx",
        ok=bool(text),
        reason="" if text else "the document holds no text",
    )


def _from_xlsx(data: bytes | Path) -> ExtractResult:
    """Every sheet of a workbook, cell values joined per row.

    Shared strings live in one table that the sheets reference by index — a
    workbook read without resolving it yields numbers and nothing else.
    """
    shared: list[str] = []
    for _name, raw in _zip_members(data, lambda n: n == "xl/sharedStrings.xml"):
        root = _parse_xml(raw)
        if root is None:
            continue
        for item in root:
            shared.append("".join(node.text or "" for node in item.iter() if node.text))

    sheets = _zip_members(
        data, lambda n: n.startswith("xl/worksheets/") and n.endswith(".xml")
    )
    if not sheets:
        return ExtractResult(kind="xlsx", reason="the workbook has no readable sheet")
    lines: list[str] = []
    for name, raw in sheets:
        root = _parse_xml(raw)
        if root is None:
            continue
        lines.append(f"# {Path(name).stem}")
        for row in root.iter():
            if not row.tag.endswith("}row") and row.tag != "row":
                continue
            cells: list[str] = []
            for cell in row:
                kind = cell.get("t")
                value = "".join(
                    node.text or ""
                    for node in cell.iter()
                    if node.tag.endswith(("}v", "}t")) or node.tag in ("v", "t")
                )
                if kind == "s" and value.isdigit():
                    index = int(value)
                    value = shared[index] if index < len(shared) else ""
                if value:
                    cells.append(value)
            if cells:
                lines.append(" | ".join(cells))
    text = _clean("\n".join(lines))
    return ExtractResult(
        text=text,
        kind="xlsx",
        ok=bool(text),
        reason="" if text else "the workbook holds no values",
    )


def _slide_number(name: str) -> int:
    """The slide's own number — ``slide10`` must not sort before ``slide2``."""
    match = re.search(r"(\d+)", Path(name).stem)
    return int(match.group(1)) if match else 0


def _from_pptx(data: bytes | Path) -> ExtractResult:
    """Slide text with each slide's speaker notes directly beneath it.

    Ordering is the whole difficulty. Archive order is arbitrary and plain
    sorting puts `notesSlides/` before `slides/` and slide 10 before slide 2 —
    so a deck came out as every note first, in the wrong order, detached from
    the slide it explains. Notes are where the actual argument lives; separated
    from their slide they retrieve as orphan sentences.
    """
    members = _zip_members(
        data,
        lambda n: (n.startswith("ppt/slides/slide") or n.startswith("ppt/notesSlides/"))
        and n.endswith(".xml"),
    )
    if not members:
        return ExtractResult(kind="pptx", reason="the deck has no readable slide")

    slides: dict[int, str] = {}
    notes: dict[int, str] = {}
    for name, raw in members:
        xml = re.sub(r"(?i)</a:p>", "\n", _decode(raw))
        body = _strip_markup(xml).strip()
        if not body:
            continue
        target = notes if name.startswith("ppt/notesSlides/") else slides
        target[_slide_number(name)] = body

    parts: list[str] = []
    for number in sorted(set(slides) | set(notes)):
        block = [f"# Slide {number}"] if number else ["# Slide"]
        if number in slides:
            block.append(slides[number])
        if number in notes:
            block.append(f"Speaker notes: {notes[number]}")
        parts.append("\n".join(block))
    text = _clean("\n\n".join(parts))
    return ExtractResult(
        text=text,
        kind="pptx",
        ok=bool(text),
        reason="" if text else "the deck holds no text",
    )


def _from_odf(data: bytes | Path) -> ExtractResult:
    """OpenDocument text, spreadsheet or presentation — all one `content.xml`."""
    members = _zip_members(data, lambda n: n == "content.xml")
    if not members:
        return ExtractResult(kind="odf", reason="the document part is unreadable")
    xml = _decode(members[0][1])
    xml = re.sub(r"(?i)</text:p>|</text:h>|</table:table-row>", "\n", xml)
    xml = re.sub(r"(?i)</table:table-cell>", "\t", xml)
    text = _clean(_strip_markup(xml))
    return ExtractResult(
        text=text,
        kind="odf",
        ok=bool(text),
        reason="" if text else "the document holds no text",
    )


def _from_epub(data: bytes | Path) -> ExtractResult:
    """A book's chapters, in archive order (close enough to reading order)."""
    members = _zip_members(
        data, lambda n: n.lower().endswith((".xhtml", ".html", ".htm"))
    )
    if not members:
        return ExtractResult(kind="epub", reason="the book has no readable chapter")
    parts = [_strip_markup(_decode(raw)) for _name, raw in sorted(members)]
    text = _clean("\n\n".join(part for part in parts if part.strip()))
    return ExtractResult(
        text=text,
        kind="epub",
        ok=bool(text),
        reason="" if text else "the book holds no text",
    )


def _from_pdf(data: bytes | Path) -> ExtractResult:
    """Page text via pypdf. A scan without an OCR layer legitimately has none."""
    try:
        from pypdf import PdfReader  # noqa: PLC0415 — lazy (AP-26)
    except Exception as exc:  # noqa: BLE001 — a missing extra is not a crash
        return ExtractResult(
            kind="pdf",
            reason=(
                "PDF text needs the 'pypdf' package, which is not installed in "
                f"this build ({type(exc).__name__})"
            ),
        )
    try:
        source: Any = data if isinstance(data, Path) else io.BytesIO(data)
        reader = PdfReader(source)
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 — a password-locked PDF is not our failure
                return ExtractResult(
                    kind="pdf", reason="the PDF is password-protected"
                )
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 — a corrupt PDF reports, never raises
        return ExtractResult(kind="pdf", reason=f"the PDF could not be read ({type(exc).__name__})")
    text = _clean("\n\n".join(pages))
    if not text:
        return ExtractResult(
            kind="pdf",
            meta={"pages": len(pages)},
            reason=(
                "the PDF carries no text layer — it is most likely a scan, "
                "which needs OCR"
            ),
        )
    return ExtractResult(text=text, kind="pdf", ok=True, meta={"pages": len(pages)})


def _from_rtf(data: bytes | Path) -> ExtractResult:
    """Rich text with its control words removed — imperfect but readable."""
    body = _decode(_read(data))
    text = _clean(_RTF_CONTROL_RE.sub(" ", body))
    return ExtractResult(
        text=text,
        kind="rtf",
        ok=bool(text),
        reason="" if text else "the document holds no text",
    )


def _from_html(data: bytes | Path) -> ExtractResult:
    text = _clean(_strip_markup(_decode(_read(data))))
    return ExtractResult(
        text=text,
        kind="html",
        ok=bool(text),
        reason="" if text else "the page holds no text",
    )


def _from_text(data: bytes | Path, kind: str = "text") -> ExtractResult:
    text = _clean(_decode(_read(data)))
    return ExtractResult(
        text=text,
        kind=kind,
        ok=bool(text),
        reason="" if text else "the file is empty",
    )


def _from_json(data: bytes | Path) -> ExtractResult:
    """JSON re-serialised readably, or its raw text when it will not parse.

    Pretty-printing matters: a one-line 2 MB JSON file is a single unbreakable
    run for the chunker, while an indented one splits on real boundaries.
    """
    raw = _decode(_read(data))
    try:
        text = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
    except (ValueError, RecursionError):
        text = raw
    cleaned = _clean(text)
    return ExtractResult(
        text=cleaned,
        kind="json",
        ok=bool(cleaned),
        reason="" if cleaned else "the file is empty",
    )


_NO_TEXT_REASONS: dict[str, str] = {
    # The three media reasons describe a PENDING state, not a dead end: the
    # item is stored and findable by name, date and folder immediately, and
    # the enrichment stage fills in what the file actually shows or says.
    "image": "a picture carries no text; it is queued to be described",
    "audio": "a recording carries no text; it is queued to be transcribed",
    "video": "a video carries no text; it is queued to be transcribed",
    "empty": "the file is empty",
    "binary": "this is a binary file with no readable text",
    "archive": "an archive holds files rather than text — import it as an export instead",
    "tar": "a tar archive holds files rather than text — import it as an export instead",
    "gzip": "a compressed file holds other files rather than text",
    "legacy_office": (
        "this is a pre-2007 Office file (.doc/.xls/.ppt); re-saving it in the "
        "modern format makes it readable"
    ),
}


def extract_text(
    data: bytes | Path, *, filename: str = "", mime: str = ""
) -> ExtractResult:
    """The readable text of one file. Never raises.

    ``data`` may be bytes (an attachment already in memory) or a
    :class:`~pathlib.Path` (a file on disk, streamed by the extractors that
    can). ``filename`` and ``mime`` are hints only — content decides.
    """
    try:
        kind = detect_kind(data, filename, mime)
    except Exception as exc:  # noqa: BLE001 — detection must never break a sync
        log.debug("extract: detection failed for %s", filename, exc_info=True)
        return ExtractResult(reason=f"the file could not be identified ({type(exc).__name__})")

    try:
        if kind == "pdf":
            return _from_pdf(data)
        if kind == "docx":
            return _from_docx(data)
        if kind == "xlsx":
            return _from_xlsx(data)
        if kind == "pptx":
            return _from_pptx(data)
        if kind == "odf":
            return _from_odf(data)
        if kind == "epub":
            return _from_epub(data)
        if kind == "rtf":
            return _from_rtf(data)
        if kind == "html":
            return _from_html(data)
        if kind == "text":
            suffix = Path(filename).suffix.lower() if filename else ""
            if suffix in (".json", ".jsonl", ".ndjson"):
                return _from_json(data)
            return _from_text(data)
        return ExtractResult(
            kind=kind,
            meta=_media_meta(data, kind),
            reason=_NO_TEXT_REASONS.get(kind, "this format is not readable as text"),
        )
    except Exception as exc:  # noqa: BLE001 — one bad file never fails a whole sync
        log.debug("extract: %s failed for %s", kind, filename, exc_info=True)
        return ExtractResult(
            kind=kind, reason=f"the file could not be read ({type(exc).__name__})"
        )


def _media_meta(data: bytes | Path, kind: str) -> dict[str, Any]:
    """What a media file states about itself — capture time, camera, GPS.

    Read here rather than in the enrichment stage on purpose: this is what
    makes a photo findable by *when and where* the moment it lands, months
    before any model gets around to describing it. It also costs nothing —
    the bytes are already open.
    """
    if kind not in MEDIA_KINDS:
        return {}
    try:
        from jarvis.ultrawiki.media import (  # noqa: PLC0415 — lazy (AP-26)
            EXIF_SCAN_BYTES,
            media_metadata,
        )

        if isinstance(data, Path):
            with data.open("rb") as handle:
                head = handle.read(EXIF_SCAN_BYTES)
        else:
            head = bytes(data[:EXIF_SCAN_BYTES])
        return media_metadata(head, kind)
    except Exception:  # noqa: BLE001 — metadata is a bonus, never a failure
        log.debug("extract: media metadata unavailable", exc_info=True)
        return {}
