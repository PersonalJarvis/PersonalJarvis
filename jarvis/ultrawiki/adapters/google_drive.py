"""Google Drive pull adapter — the readable text of every non-trashed file.

Of everything Drive holds, this pulls what belongs in a *memory*: the TEXT.
Google-native documents are exported to plain text (Docs → ``text/plain``,
Sheets → ``text/csv``, Slides → ``text/plain``); text-shaped regular files
(``text/*``, JSON, XML, YAML, …) are downloaded directly. The walk lists the
complete non-trashed corpus ordered by ``modifiedTime`` ascending, so an
interrupted backfill resumes strictly after the last imported file id without
re-downloading anything before it.

Scope honesty — what the token allows vs. what is imported:

- **Binaries are skipped, deliberately and visibly.** Images, videos,
  archives, PDFs and Office binaries have no text the Drive API can hand
  over as such (Drive exports only Google-native types), and a knowledge
  base must never swallow blobs. Skipped files are counted in one log line;
  they yield NO item.
- **Folders and shortcuts are skipped** — structure, not content; a
  shortcut's target imports as itself.
- **Bodies are capped at ~1 MB** with a visible truncation marker. Direct
  downloads are bounded at the source with an HTTP ``Range`` header, so an
  oversized file never crosses the wire whole; Google-native exports are
  additionally subject to Google's own ~10 MB export ceiling — a document
  whose export fails yields its metadata with an honest note instead of
  failing the sync.
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
from typing import Any

import httpx

from jarvis.ultrawiki.adapters import _google as g
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


def _content_plan(mime: str) -> tuple[str, str] | None:
    """How to obtain a file's text: ``("export", target)``, ``("download", "")``,
    or ``None`` when the file has no text form and is skipped honestly."""
    if mime in _GOOGLE_EXPORTS:
        return ("export", _GOOGLE_EXPORTS[mime])
    if mime.startswith("application/vnd.google-apps."):
        return None  # folder, shortcut, form, drawing, … — no text export
    if mime.startswith("text/") or mime in _TEXT_MIMES:
        return ("download", "")
    return None


def item_from_file(
    file: dict[str, Any], content: str, content_truncated: bool
) -> RawItem | None:
    """One listed file plus its fetched text as a ``RawItem``.

    The body is composed rather than copied raw: bare file content loses its
    own name, kind and owner — the handles a later question reaches for.
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
) -> tuple[str, bool] | None:
    """``(text, truncated)`` for one file, or ``None`` when it has no text form.

    A per-file fetch failure (export ceiling, permission quirk on one item)
    degrades to an honest note — one stubborn file must never sink the whole
    walk. Credential failures and exhausted rate budgets still raise, because
    they concern the sync, not the file.
    """
    plan = _content_plan(str(file.get("mimeType") or ""))
    if plan is None:
        return None
    mode, target = plan
    file_id = str(file.get("id") or "")

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
                f"[content could not be exported (HTTP {response.status_code}) — "
                "open the link to view it]",
                False,
            )
        return response.text, False

    # Direct download, bounded at the source: the Range header caps what
    # crosses the wire, so an oversized file costs one capped request.
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
        return "", False
    if response.status_code >= 400:
        return (
            f"[content could not be downloaded (HTTP {response.status_code}) — "
            "open the link to view it]",
            False,
        )
    try:
        declared = int(file.get("size") or 0)
    except (TypeError, ValueError):
        declared = 0
    truncated = response.status_code == 206 and declared > g.BODY_CAP
    return response.text, truncated


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
            content, content_truncated = fetched
            item = item_from_file(file, content, content_truncated)
            if item is not None:
                yielded += 1
                yield item

    if skipped:
        log.info(
            "Google Drive adapter: skipped %d file(s) with no text form "
            "(binaries are never imported)",
            skipped,
        )
    log.info(
        "Google Drive adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if cutoff else "backfill",
    )
