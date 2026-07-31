"""Read plain text out of the opaque document formats a real folder holds.

A folder a user actually chooses is not a notes vault: it holds PDFs, Word
files and slide decks next to the markdown. Those are containers, not text —
reading them as UTF-8 yields megabytes of replacement characters — so the
folder walk routes them here instead.

Two rules govern everything below:

* **A failure is a SKIP, never a raise.** One password-protected PDF or one
  truncated ``.docx`` must not end a walk over ten thousand files. Every
  extractor returns ``None`` and logs; the caller drops that one file.
* **No new dependency for the Office formats.** ``.docx`` and ``.pptx`` are
  zip archives of XML, which the standard library opens on every platform.
  Only PDF needs ``pypdf``, which is already a base requirement — and a
  install without it degrades to a logged skip rather than an import error
  (charter §3: the walk still has to work on a bare server).

Nothing here touches the event loop: every function BLOCKS on file I/O and is
called from the connector's worker thread.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

#: Suffixes routed to an extractor instead of being read as UTF-8 text.
DOCUMENT_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx", ".pptx")

#: Size ceiling for a document. Far above the plain-text limit (a 40-page PDF
#: with figures is routinely 20 MB and holds a few pages of prose), far below
#: anything that could exhaust memory on a small VPS.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

#: Characters kept from one document. A 900-page manual is a reference work,
#: not one retrievable thought; the chunker downstream splits what arrives.
MAX_DOCUMENT_CHARS = 2 * 1024 * 1024

#: OOXML text-bearing leaf elements, by namespace. WordprocessingML uses
#: ``<w:t>``; DrawingML (slides, and text boxes inside any Office file) uses
#: ``<a:t>``. Matched on the LOCAL name so a file written by a different
#: producer with a different prefix still reads.
_TEXT_TAGS = frozenset({"t"})

#: Elements that end a line: a Word paragraph / line break, a slide paragraph.
#: Without these, "The ledger" and "reconciliation" concatenate into one token
#: that no search will ever match.
_BREAK_TAGS = frozenset({"p", "br", "tab", "cr"})

#: A document part that declares a DTD or an entity. OOXML producers never do;
#: an "entity bomb" is nothing but declarations, so their PRESENCE is the
#: signal. Checked on the raw bytes before any parser sees them, because this
#: check cannot be broken by a parser-internals change (defence in depth: the
#: parser below refuses the same thing again).
_DTD_MARKERS: tuple[bytes, ...] = (b"<!DOCTYPE", b"<!ENTITY", b"<!doctype")


def is_document(path: Path) -> bool:
    """``True`` when ``path`` needs an extractor rather than a UTF-8 read."""
    return path.suffix.lower() in DOCUMENT_EXTENSIONS


def extract_document_text(path: Path) -> str | None:
    """Plain text of one document, or ``None`` when it holds none we can read.

    ``None`` covers every honest miss: oversized, encrypted, corrupt, a scan
    without a text layer, or an extractor that is not installed. Blocking.

    **Delegates to** :func:`jarvis.ultrawiki.extract.extract_text`. This
    function is kept for callers that predate the shared service and for the
    ``None``-means-nothing contract they were written against; the parsing
    below is gone rather than left as a second copy to drift. Reaching for the
    shared service directly is preferred in new code — it reports WHY a file
    yielded nothing, which this signature cannot express.

    The import is local: ``extract`` imports this module's hardened XML parser,
    so a module-level import here would be circular.
    """
    from jarvis.ultrawiki.extract import extract_text  # noqa: PLC0415 — see above

    try:
        size = path.stat().st_size
    except OSError as exc:
        log.debug("document skipped, stat failed for %s: %s", path, exc)
        return None
    if size > MAX_DOCUMENT_BYTES:
        log.info(
            "document skipped: %s is %d bytes, over the %d-byte limit",
            path,
            size,
            MAX_DOCUMENT_BYTES,
        )
        return None
    result = extract_text(path, filename=path.name)
    if not result.ok:
        log.info("document yielded no text: %s (%s)", path, result.reason)
        return None
    return _clean(result.text)


def _clean(text: str | None) -> str | None:
    """Normalize whitespace; whitespace-only extraction counts as nothing."""
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    return stripped[:MAX_DOCUMENT_CHARS]


def _pdf_text(path: Path) -> str | None:
    try:
        from pypdf import PdfReader  # noqa: PLC0415 — lazy (AP-26)
    except ImportError:
        log.info(
            "PDF skipped: %s needs the 'pypdf' package, which is not installed in this environment",
            path,
        )
        return None
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # An empty user password is the common "protected but readable"
            # case; anything else is genuinely locked and gets skipped.
            try:
                opened = reader.decrypt("")
            except Exception:  # noqa: BLE001 — a locked file is a skip
                opened = 0
            if not opened:
                log.info("PDF skipped: %s is password protected", path)
                return None
        pages = list(reader.pages)
    except Exception as exc:  # noqa: BLE001 — a broken PDF is a skip, not a stop
        log.info("PDF skipped: %s could not be opened (%s)", path, type(exc).__name__)
        return None
    parts: list[str] = []
    total = 0
    for index, page in enumerate(pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 — one bad page, not a bad file
            log.debug("page %d of %s could not be extracted: %s", index, path, exc)
            continue
        if not text.strip():
            continue
        parts.append(text)
        total += len(text)
        if total >= MAX_DOCUMENT_CHARS:
            break
    return "\n\n".join(parts) if parts else None


def _ooxml_text(path: Path) -> str | None:
    """Text of a Word document or slide deck, in reading order."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = _ooxml_parts(path.suffix.lower(), archive.namelist())
            if not names:
                log.info("document skipped: %s holds no readable text part", path)
                return None
            chunks = [_part_text(archive, name, path) for name in names]
    except (zipfile.BadZipFile, OSError) as exc:
        log.info("document skipped: %s could not be opened (%s)", path, type(exc).__name__)
        return None
    body = "\n\n".join(chunk for chunk in chunks if chunk)
    return body or None


def _ooxml_parts(suffix: str, names: list[str]) -> list[str]:
    """The XML parts holding body text, in the order they are read."""
    if suffix == ".docx":
        return [name for name in names if name == "word/document.xml"]
    slides = [
        name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml")
    ]
    # slide2 before slide10: sort on the number, not the string.
    return sorted(slides, key=_slide_order)


def _slide_order(name: str) -> tuple[int, str]:
    digits = "".join(ch for ch in Path(name).stem if ch.isdigit())
    return (int(digits) if digits else 0, name)


def _part_text(archive: zipfile.ZipFile, name: str, path: Path) -> str:
    try:
        raw = archive.read(name)
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        log.debug("part %s of %s is unreadable: %s", name, path, exc)
        return ""
    if any(marker in raw for marker in _DTD_MARKERS):
        log.warning(
            "document skipped: %s declares a DTD or entity, which no Office "
            "producer emits and an entity-expansion attack consists of",
            path,
        )
        return ""
    try:
        root = ET.fromstring(raw, parser=_safe_parser())  # noqa: S314 — hardened
    except ET.ParseError as exc:
        log.info("document skipped: %s holds malformed XML (%s)", path, exc)
        return ""
    except Exception as exc:  # noqa: BLE001 — a refused entity surfaces here
        log.warning("document skipped: %s was refused by the parser (%s)", path, exc)
        return ""
    return _element_text(root)


class _RefusedEntity(ValueError):
    """Raised from the parser callback when a document declares an entity."""


def safe_xml_parser() -> ET.XMLParser:
    """An ElementTree parser that refuses to declare or fetch any entity.

    The stdlib parser expands internal entities, which is the whole mechanism
    behind "billion laughs": a handful of nested declarations expand to
    gigabytes and take the process with them. OOXML has no legitimate use for
    custom entities, so refusing the DECLARATION removes the class instead of
    trying to bound the expansion. External references are refused too, so a
    document can never make this machine fetch a URL.

    Best-effort by construction: ``XMLParser.parser`` is a CPython detail, and
    on a runtime that does not expose it the byte-level DTD check above is
    already the blocking guard.
    """
    # noqa S314: this factory IS the hardening the rule asks for. defusedxml is
    # not a dependency here (it would add one to the universal base install for
    # a guard the two layers below already give), so the entity handlers are
    # disarmed directly and a DTD is refused on the bytes before this runs.
    parser = ET.XMLParser()  # noqa: S314
    expat = getattr(parser, "parser", None)
    if expat is None:  # pragma: no cover - non-CPython runtime
        return parser

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise _RefusedEntity("this document declares an XML entity")

    for handler in (
        "EntityDeclHandler",
        "UnparsedEntityDeclHandler",
        "ExternalEntityRefHandler",
    ):
        try:
            setattr(expat, handler, _refuse)
        except (AttributeError, TypeError):  # pragma: no cover - defensive
            log.debug("XML hardening: %s could not be installed", handler)
    return parser


#: Public alias. ``jarvis.ultrawiki.extract`` parses XML out of the same
#: untrusted office archives and must use the SAME hardening — a second copy of
#: a security primitive is a second thing to forget to update.
_safe_parser = safe_xml_parser


def _element_text(root: ET.Element) -> str:
    """Walk the tree, joining text leaves and honoring paragraph breaks."""
    pieces: list[str] = []
    for element in root.iter():
        local = _local_name(element.tag)
        if local in _TEXT_TAGS and element.text:
            pieces.append(element.text)
        elif local in _BREAK_TAGS and pieces and pieces[-1] != "\n":
            pieces.append("\n")
    return "".join(pieces)


def _local_name(tag: object) -> str:
    """``{namespace}t`` -> ``t``; a comment's callable tag -> ``""``."""
    if not isinstance(tag, str):
        return ""
    return tag.rpartition("}")[2]


__all__ = [
    "DOCUMENT_EXTENSIONS",
    "MAX_DOCUMENT_BYTES",
    "MAX_DOCUMENT_CHARS",
    "extract_document_text",
    "is_document",
]
