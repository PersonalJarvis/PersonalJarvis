"""Google Drive pull adapter — the readable text of every non-trashed file.

Of everything Drive holds, this pulls what belongs in a *memory*: the TEXT.
Google-native documents are exported to plain text (Docs → ``text/plain``,
Sheets → ``text/csv``, Slides → ``text/plain``); text-shaped regular files
(``text/*``, JSON, XML, YAML, …) are downloaded directly; uploaded documents
(PDF, Word, Excel, PowerPoint, OpenDocument, EPUB, RTF) are downloaded whole
and read by the shared extraction service. The walk lists the complete
non-trashed corpus ordered by ``modifiedTime`` ascending, so an interrupted
backfill resumes strictly after the last imported file id without
re-downloading anything before it.

Scope honesty — what the token allows vs. what is imported:

- **Only true blobs are skipped**: pictures, videos, audio and archives, plus
  Drive's own structural types (folders, shortcuts, forms). They are counted
  in one log line and yield NO item. Everything with text inside is imported,
  including the formats Drive cannot export — that is the difference between
  a knowledge base holding a Drive and holding the parts of it Google happens
  to convert.
- **A document whose text cannot be read is still imported**, marked
  ``content_missing`` with the reason: a scanned PDF, an oversized upload or
  a failed download stays findable by name, owner and date instead of
  vanishing without trace.
- **Folders and shortcuts are skipped** — structure, not content; a
  shortcut's target imports as itself.
- **Bodies are capped at ~1 MB** with a visible truncation marker. A plain
  text download is bounded at the source with an HTTP ``Range`` header, so an
  oversized log file never crosses the wire whole. A document read by the
  extractor cannot be bounded that way — half a PDF or half a ZIP parses as
  nothing — so it is fetched whole below ``MAX_DOCUMENT_BYTES`` and refused
  with a sentence above it. Google-native exports are additionally subject to
  Google's own ~10 MB export ceiling; a document whose export fails yields
  its metadata with an honest note instead of failing the sync.
- **Shared drives are not enumerated.** The walk covers the user corpus (My
  Drive plus items shared with the user that surface in ``files.list``);
  separate shared-drive corpora need a per-drive walk a later change can add.
- **Incremental rides the ``modifiedTime`` cursor** via
  ``metadata["mtime_ns"]`` (the key the sync runner already advances): a
  numeric checkpoint narrows the listing with ``modifiedTime > <cutoff>``
  one day earlier, so a boundary can never skip a file.

Credentials come from the marketplace token the user connected in the Plugins
store — this adapter never asks for a second one and never logs the token.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any

import httpx

from jarvis.ultrawiki.adapters import _google as g
from jarvis.ultrawiki.extract import (
    DOCUMENT_EXTENSIONS,
    MAX_DOCUMENT_BYTES,
    extract_text,
)
from jarvis.ultrawiki.types import ConnectorContext, RawItem

log = logging.getLogger(__name__)

__all__ = [
    "INTEGRATION_ID",
    "GoogleAdapterError",
    "google_drive_pull_adapter",
    "item_from_file",
]

#: Re-exported so callers can catch the family's error type from this module.
GoogleAdapterError = g.GoogleAdapterError

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:google_drive"

_PLUGIN_ID = "google_drive"
_PRODUCT = "Google Drive"
_API = "https://www.googleapis.com/drive/v3"

_PAGE_SIZE = 1000
_FIELDS = (
    "nextPageToken,files(id,name,mimeType,size,createdTime,modifiedTime,"
    "webViewLink,description,owners(displayName,emailAddress))"
)

#: Google-native types and the text shape they export to.
_GOOGLE_EXPORTS: dict[str, str] = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

#: Non-``text/*`` MIME types that are still text on the inside.
_TEXT_MIMES: frozenset[str] = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
        "application/toml",
        "application/x-sh",
        "application/csv",
    }
)

#: Uploaded documents that carry text no API hands over as such. Drive exports
#: only its OWN formats, so every one of these used to leave as "no text
#: form" — and they are the single largest category of real documents in a
#: real Drive: the contract, the invoice, the deck someone sent by mail.
#: The shared extraction service reads them from their bytes.
_DOCUMENT_MIMES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/epub+zip",
        "application/rtf",
        "text/rtf",
    }
)


def _content_plan(mime: str, name: str = "") -> tuple[str, str] | None:
    """How to obtain a file's text: ``("export", target)``, ``("extract", "")``,
    ``("download", "")``, or ``None`` when the file has no text form.

    The name is consulted after the type because Drive's MIME is only as good
    as whatever uploaded the file: a PDF dropped in by a scanner or a sync
    client routinely arrives as ``application/octet-stream``, and judging it
    by type alone would throw the document away.
    """
    if mime in _GOOGLE_EXPORTS:
        return ("export", _GOOGLE_EXPORTS[mime])
    if mime.startswith("application/vnd.google-apps."):
        return None  # folder, shortcut, form, drawing, … — no text export
    suffix = PurePosixPath(name).suffix.lower()
    # Documents are checked BEFORE the text shapes: RTF announces itself as
    # `text/rtf` and would otherwise import as its own control words.
    if mime in _DOCUMENT_MIMES or suffix in DOCUMENT_EXTENSIONS:
        return ("extract", "")
    if mime.startswith("text/") or mime in _TEXT_MIMES:
        return ("download", "")
    return None


def item_from_file(
    file: dict[str, Any],
    content: str,
    content_truncated: bool,
    *,
    content_missing_reason: str = "",
) -> RawItem | None:
    """One listed file plus its fetched text as a ``RawItem``.

    The body is composed rather than copied raw: bare file content loses its
    own name, kind and owner — the handles a later question reaches for.

    ``content_missing_reason`` marks a file whose text could not be read. The
    item is still produced: a scanned contract that is findable by name, date
    and owner, and that SAYS its text is missing, is worth incomparably more
    than a document the knowledge base silently pretends it never saw.
    """
    file_id = str(file.get("id") or "").strip()
    if not file_id:
        return None
    name = str(file.get("name") or "").strip() or "(unnamed file)"
    mime = str(file.get("mimeType") or "")
    created = str(file.get("createdTime") or "")
    modified = str(file.get("modifiedTime") or "")
    owners = [row for row in file.get("owners") or [] if isinstance(row, dict)]
    owner = ""
    if owners:
        owner = str(owners[0].get("displayName") or owners[0].get("emailAddress") or "")
    permalink = (
        str(file.get("webViewLink") or "").strip()
        or f"https://drive.google.com/file/d/{file_id}/view"
    )
    description = str(file.get("description") or "").strip()

    header = f"Google Drive · {name} · {mime}"
    if owner:
        header += f" · owned by {owner}"
    lines = [header]
    if description:
        lines.append(description)
    if content:
        lines.append("")
        lines.append(content)
    elif content_missing_reason:
        lines.append(f"[no text imported: {content_missing_reason}]")
    body, capped = g.cap_body("\n".join(lines).strip())
    truncated = capped or content_truncated
    if content_truncated and not capped:
        body += g.TRUNCATION_MARKER

    try:
        size = int(file.get("size") or 0)
    except (TypeError, ValueError):
        size = 0

    return RawItem(
        external_id=file_id,
        title=name,
        body=body,
        permalink=permalink,
        # WHEN IT CAME TO BE — the memory is about the document, not its last
        # touch. The modification time rides in metadata to drive the cursor.
        timestamp_utc=created or modified,
        author_raw=owner,
        metadata={
            "mtime_ns": g.to_ns(modified or created),
            "mime_type": mime,
            "size": size,
            "truncated": truncated,
            **(
                {
                    "content_missing": True,
                    "content_missing_reason": content_missing_reason,
                }
                if content_missing_reason
                else {}
            ),
        },
    )


async def _all_files(
    client: httpx.AsyncClient, token: str, query: str
) -> list[dict[str, Any]]:
    """The complete non-trashed listing, oldest-modified first.

    Metadata only — no content crosses the wire here, so even a large Drive
    costs a handful of cheap requests before the first download, and the
    resume point can be located before anything expensive happens.
    """
    rows: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {
            "q": query,
            "orderBy": "modifiedTime",
            "pageSize": _PAGE_SIZE,
            "fields": _FIELDS,
        }
        if page_token:
            params["pageToken"] = page_token
        payload = await g.google_get_json(
            client, f"{_API}/files", token, _PRODUCT, params=params
        )
        for row in payload.get("files") or []:
            if isinstance(row, dict) and row.get("id"):
                rows.append(row)
        page_token = str(payload.get("nextPageToken") or "") or None
        if not page_token:
            break
    return rows


async def _file_text(
    client: httpx.AsyncClient, token: str, file: dict[str, Any]
) -> tuple[str, bool, str] | None:
    """``(text, truncated, missing_reason)``, or ``None`` when there is no text
    form at all and the file is skipped.

    A non-empty ``missing_reason`` is a FILE that exists and a CONTENT that
    could not be read — a scanned PDF, an oversized upload. That is not the
    same as having nothing to import: the item is still stored, so the
    document is findable by its name and its absence is visible instead of
    looking like it was never there.

    A per-file fetch failure (export ceiling, permission quirk on one item)
    degrades to an honest note — one stubborn file must never sink the whole
    walk. Credential failures and exhausted rate budgets still raise, because
    they concern the sync, not the file.
    """
    mime = str(file.get("mimeType") or "")
    name = str(file.get("name") or "")
    plan = _content_plan(mime, name)
    if plan is None:
        return None
    mode, target = plan
    file_id = str(file.get("id") or "")
    try:
        declared = int(file.get("size") or 0)
    except (TypeError, ValueError):
        declared = 0

    if mode == "extract":
        # Whole file or nothing: a container cannot be range-truncated (see
        # MAX_DOCUMENT_BYTES). So the ceiling REJECTS, with a sentence, rather
        # than fetching a prefix that would parse as an empty document.
        if declared > MAX_DOCUMENT_BYTES:
            return (
                "",
                False,
                f"the file is {declared // (1024 * 1024)} MB, above the "
                f"{MAX_DOCUMENT_BYTES // (1024 * 1024)} MB import limit for "
                "documents read whole",
            )
        response = await g.google_get(
            client,
            f"{_API}/files/{file_id}",
            token,
            _PRODUCT,
            params={"alt": "media"},
            tolerate=(403, 404),
        )
        if response.status_code >= 400:
            return (
                "",
                False,
                f"the download failed (HTTP {response.status_code}) — open the "
                "link to view it",
            )
        result = extract_text(response.content, filename=name, mime=mime)
        if result.ok:
            return (result.text, False, "")
        return ("", False, result.reason or "no text could be read from this file")

    if mode == "export":
        response = await g.google_get(
            client,
            f"{_API}/files/{file_id}/export",
            token,
            _PRODUCT,
            params={"mimeType": target},
            tolerate=(403, 404),
        )
        if response.status_code >= 400:
            return (
                "",
                False,
                f"the export failed (HTTP {response.status_code}) — open the "
                "link to view it",
            )
        return (response.text, False, "")

    # Direct download, bounded at the source: the Range header caps what
    # crosses the wire, so an oversized file costs one capped request. Safe
    # here and only here, because a text file cut mid-way is still text.
    response = await g.google_get(
        client,
        f"{_API}/files/{file_id}",
        token,
        _PRODUCT,
        params={"alt": "media"},
        headers={"Range": f"bytes=0-{g.BODY_CAP - 1}"},
        tolerate=(403, 404, 416),
    )
    if response.status_code == 416:  # empty file: the range has nothing to cover
        return ("", False, "")
    if response.status_code >= 400:
        return (
            "",
            False,
            f"the download failed (HTTP {response.status_code}) — open the "
            "link to view it",
        )
    truncated = response.status_code == 206 and declared > g.BODY_CAP
    return (response.text, truncated, "")


async def google_drive_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield the readable text of every non-trashed file, oldest-modified first.

    ``checkpoint`` doubles as the resume/cursor convention: a numeric value is
    the incremental cursor (nanoseconds → ``modifiedTime >`` query); anything
    else is the last imported file id from an interrupted backfill, and the
    walk resumes strictly after it — files before the resume point are never
    content-fetched again. A checkpoint id that no longer exists (the file was
    trashed) degrades to a full walk rather than silently skipping everything.
    ``transport`` is a test seam; production leaves it ``None``.
    """
    token = g.google_token(_PLUGIN_ID, _PRODUCT)
    cutoff = g.cursor_cutoff(checkpoint)
    query = "trashed = false"
    if cutoff:
        query += f" and modifiedTime > '{cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}'"
    resume_after = checkpoint if (checkpoint and cutoff is None) else None
    yielded = 0
    skipped = 0
    unreadable = 0

    async with httpx.AsyncClient(timeout=g.TIMEOUT, transport=transport) as client:
        files = await _all_files(client, token, query)
        if resume_after:
            index = next(
                (i for i, row in enumerate(files) if str(row.get("id")) == resume_after),
                None,
            )
            if index is None:
                log.info(
                    "Google Drive adapter: checkpoint file %s no longer exists; "
                    "walking the full listing instead",
                    resume_after,
                )
            else:
                files = files[index + 1 :]
        for file in files:
            fetched = await _file_text(client, token, file)
            if fetched is None:
                skipped += 1
                continue
            content, content_truncated, missing_reason = fetched
            if missing_reason:
                unreadable += 1
            item = item_from_file(
                file,
                content,
                content_truncated,
                content_missing_reason=missing_reason,
            )
            if item is not None:
                yielded += 1
                yield item

    if skipped:
        log.info(
            "Google Drive adapter: skipped %d file(s) with no text form "
            "(pictures, videos and archives are never imported)",
            skipped,
        )
    if unreadable:
        log.info(
            "Google Drive adapter: %d file(s) were imported without their text "
            "(scanned, oversized or unreadable); each item says why",
            unreadable,
        )
    log.info(
        "Google Drive adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if cutoff else "backfill",
    )
