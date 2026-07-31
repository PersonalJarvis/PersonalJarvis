"""Gmail pull adapter — every message the connected mailbox holds.

Of everything Gmail is, this pulls what belongs in a *memory*: the text of
every message — who wrote, to whom, when, and what was said. The walk is the
full mailbox (``users.messages.list`` paged to the end), oldest to newest, so
an interrupted backfill resumes strictly after the last imported message id.

Scope honesty — what the token allows vs. what is imported:

- **Message text.** The plain-text part is preferred; an HTML-only message is
  stripped to readable text; a message with neither decodable part falls back
  to the API's snippet.
- **Readable attachments become their own items**, one per file, linked to
  the message by ``thread_key`` and named ``<message-id>#att-<n>``. This is
  where the invoice, the contract and the signed offer actually live: mail
  bodies say "see attached", and a memory that stops at the body knows only
  that something was attached. Blobs (photos, signature images, video) are
  still never fetched — no OCR ships here — and the message body names every
  attachment it carries either way, so the record never implies completeness.
- **Spam and trash are excluded** (the API's default listing). A memory keeps
  what the user kept.
- **Bodies are capped at ~1 MB** with a visible truncation marker.
- **Incremental rides the ``internalDate`` cursor** via
  ``metadata["mtime_ns"]`` — the key the sync runner already advances. Gmail's
  history API (``historyId``) is finer-grained but its log expires after days
  and the bridge persists exactly one numeric cursor, so the receive time is
  the honest durable choice; each message's ``historyId`` is stashed in
  metadata for a future history-based mode. A numeric checkpoint narrows the
  listing with ``after:<epoch seconds>`` (one day earlier, so a boundary can
  never skip a message; re-yielded items upsert as ``unchanged``).

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
    TEXT_EXTENSIONS,
    extract_text,
)
from jarvis.ultrawiki.types import ConnectorContext, RawItem

log = logging.getLogger(__name__)

__all__ = [
    "INTEGRATION_ID",
    "GoogleAdapterError",
    "gmail_pull_adapter",
    "item_from_message",
    "items_from_attachments",
]

#: Re-exported so callers can catch the family's error type from this module.
GoogleAdapterError = g.GoogleAdapterError

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:gmail"

_PLUGIN_ID = "gmail"
_PRODUCT = "Gmail"
_API = "https://gmail.googleapis.com/gmail/v1/users/me"

#: The listing is ids only, so the largest page the API allows is the cheap
#: way to enumerate a big mailbox.
_LIST_PAGE_SIZE = 500

#: Defensive bound on MIME-part recursion; real messages nest a handful deep.
_MAX_PART_DEPTH = 20

#: Attachment types worth fetching that are neither ``text/*`` nor named by a
#: known extension. Everything else with a name is a blob (photo, signature
#: image, video) — no OCR ships here, so fetching one would cost a request per
#: message to import nothing.
_READABLE_MIMES: frozenset[str] = frozenset(
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
        "application/json",
        "application/xml",
    }
)


def _header(headers: list[Any], name: str) -> str:
    wanted = name.lower()
    for row in headers:
        if isinstance(row, dict) and str(row.get("name") or "").lower() == wanted:
            return str(row.get("value") or "").strip()
    return ""


def _collect_text(
    part: Any, plain: list[str], html_parts: list[str], depth: int = 0
) -> None:
    """Walk the MIME tree collecting decodable text; attachments stay behind."""
    if depth > _MAX_PART_DEPTH or not isinstance(part, dict):
        return
    filename = str(part.get("filename") or "")
    mime = str(part.get("mimeType") or "").lower()
    data = str((part.get("body") or {}).get("data") or "")
    if not filename and data:
        if mime.startswith("text/plain"):
            plain.append(g.decode_base64url(data))
        elif mime.startswith("text/html"):
            html_parts.append(g.decode_base64url(data))
    for sub in part.get("parts") or []:
        _collect_text(sub, plain, html_parts, depth + 1)


def _attachment_count(part: Any, depth: int = 0) -> int:
    if depth > _MAX_PART_DEPTH or not isinstance(part, dict):
        return 0
    count = 1 if str(part.get("filename") or "") else 0
    for sub in part.get("parts") or []:
        count += _attachment_count(sub, depth + 1)
    return count


def _attachment_parts(part: Any, found: list[dict[str, Any]], depth: int = 0) -> None:
    """Every named part of the MIME tree, in the order the message carries it."""
    if depth > _MAX_PART_DEPTH or not isinstance(part, dict):
        return
    if str(part.get("filename") or "").strip():
        found.append(part)
    for sub in part.get("parts") or []:
        _attachment_parts(sub, found, depth + 1)


def _is_readable_attachment(filename: str, mime: str) -> bool:
    """Whether this attachment holds text worth fetching.

    A document or a text file does; a photo, a video and a signature image do
    not, and must not cost one request each just to be discarded. The name is
    checked as well as the type because mail clients label attachments with
    ``application/octet-stream`` more often than they label them correctly.
    """
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in DOCUMENT_EXTENSIONS or suffix in TEXT_EXTENSIONS:
        return True
    lowered = mime.lower()
    return lowered.startswith("text/") or lowered in _READABLE_MIMES


def _body_text(payload: dict[str, Any], snippet: str) -> str:
    plain: list[str] = []
    html_parts: list[str] = []
    _collect_text(payload, plain, html_parts)
    if any(text.strip() for text in plain):
        return "\n".join(text for text in plain if text.strip()).strip()
    if any(text.strip() for text in html_parts):
        return g.strip_html("\n".join(html_parts))
    return snippet.strip()


def item_from_message(message: dict[str, Any]) -> RawItem | None:
    """One fetched message as a ``RawItem``; ``None`` when it is unusable.

    The body is composed rather than copied raw: a bare body loses who wrote
    it and to whom, and both are exactly what a later question ("what did Ada
    write about X?") turns on.
    """
    message_id = str(message.get("id") or "").strip()
    if not message_id:
        return None
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    subject = _header(headers, "Subject")
    sender = _header(headers, "From")
    recipient = _header(headers, "To")
    thread_id = str(message.get("threadId") or "")
    internal_ms = message.get("internalDate")

    descriptor: list[str] = []
    if sender:
        descriptor.append(f"from {sender}")
    if recipient:
        descriptor.append(f"to {recipient}")
    header_line = "email" + (f" {' · '.join(descriptor)}" if descriptor else "")
    body_text = _body_text(payload, str(message.get("snippet") or ""))
    parts: list[dict[str, Any]] = []
    _attachment_parts(payload, parts)
    attachments = len(parts)
    if attachments:
        # NAMES, not a count. "3 attachments" is unsearchable; "Invoice
        # 2026-03.pdf" is how a person actually looks for the thing, and the
        # readable ones are imported as their own items beside this message.
        names = ", ".join(
            str(part.get("filename") or "(unnamed)").strip() for part in parts
        )
        body_text += f"\n\nAttachments: {names}"
    body, truncated = g.cap_body(f"{header_line}\n\n{body_text}".strip())

    return RawItem(
        external_id=message_id,
        title=subject or "(no subject)",
        body=body,
        permalink=f"https://mail.google.com/mail/u/0/#all/{message_id}",
        # WHEN IT ARRIVED — the memory is about the event. internalDate also
        # drives the cursor via metadata, so both stay consistent.
        timestamp_utc=g.iso_utc_from_ms(internal_ms),
        thread_key=thread_id or message_id,
        author_raw=sender,
        metadata={
            "mtime_ns": g.ms_to_ns(internal_ms),
            "thread_id": thread_id,
            "label_ids": [str(label) for label in message.get("labelIds") or []],
            "history_id": str(message.get("historyId") or ""),
            "attachments": attachments,
            "truncated": truncated,
        },
    )


def _attachment_item(
    message: dict[str, Any],
    index: int,
    filename: str,
    text: str,
    *,
    content_missing_reason: str = "",
) -> RawItem:
    """One attachment as a child of its message.

    It carries the message's ``thread_key`` and time, so the file and the
    conversation it arrived in stay one story: a question about the offer
    finds the PDF, and the PDF still knows who sent it and when.
    """
    message_id = str(message.get("id") or "")
    headers = (message.get("payload") or {}).get("headers") or []
    sender = _header(headers, "From")
    subject = _header(headers, "Subject")
    descriptor = f"attachment · {filename}"
    if subject:
        descriptor += f" · from the email “{subject}”"
    if sender:
        descriptor += f" · sent by {sender}"
    content = text or f"[no text imported: {content_missing_reason}]"
    body, truncated = g.cap_body(f"{descriptor}\n\n{content}".strip())
    return RawItem(
        external_id=f"{message_id}#att-{index}",
        title=filename,
        body=body,
        permalink=f"https://mail.google.com/mail/u/0/#all/{message_id}",
        timestamp_utc=g.iso_utc_from_ms(message.get("internalDate")),
        thread_key=str(message.get("threadId") or "") or message_id,
        author_raw=sender,
        metadata={
            "mtime_ns": g.ms_to_ns(message.get("internalDate")),
            "parent_external_id": message_id,
            "attachment_filename": filename,
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


async def items_from_attachments(
    client: httpx.AsyncClient, token: str, message: dict[str, Any]
) -> list[RawItem]:
    """Every readable attachment of one message, as child items.

    A blob costs nothing: it is filtered by name and type BEFORE any request.
    A fetch or parse that fails still yields an item saying why — an invoice
    that silently disappears is indistinguishable from an invoice that was
    never sent.
    """
    parts: list[dict[str, Any]] = []
    _attachment_parts(message.get("payload") or {}, parts)
    message_id = str(message.get("id") or "")
    items: list[RawItem] = []

    for index, part in enumerate(parts, start=1):
        filename = str(part.get("filename") or "").strip()
        mime = str(part.get("mimeType") or "")
        if not _is_readable_attachment(filename, mime):
            continue
        body_meta = part.get("body") or {}
        attachment_id = str(body_meta.get("attachmentId") or "")
        try:
            size = int(body_meta.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > MAX_DOCUMENT_BYTES:
            items.append(
                _attachment_item(
                    message,
                    index,
                    filename,
                    "",
                    content_missing_reason=(
                        f"the attachment is {size // (1024 * 1024)} MB, above "
                        f"the {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB import "
                        "limit"
                    ),
                )
            )
            continue

        # A small part arrives inline with the message; only a large one needs
        # its own request. Reading the inline copy first saves a round trip
        # per attachment on exactly the mail a mailbox has most of.
        data = str(body_meta.get("data") or "")
        if not data and attachment_id:
            # Tolerated per attachment: one file deleted between listing and
            # fetch, or one the scope cannot reach, must never sink the sync.
            # Credential and rate-limit failures still raise — they are about
            # the whole run, not this file.
            response = await g.google_get(
                client,
                f"{_API}/messages/{message_id}/attachments/{attachment_id}",
                token,
                _PRODUCT,
                tolerate=(403, 404),
            )
            if response.status_code < 400:
                try:
                    payload = response.json() or {}
                except ValueError:
                    payload = {}
                data = str(payload.get("data") or "")
        if not data:
            items.append(
                _attachment_item(
                    message,
                    index,
                    filename,
                    "",
                    content_missing_reason="the attachment could not be fetched",
                )
            )
            continue

        raw = g.decode_base64url_bytes(data)
        result = extract_text(raw, filename=filename, mime=mime)
        items.append(
            _attachment_item(
                message,
                index,
                filename,
                result.text if result.ok else "",
                content_missing_reason=(
                    ""
                    if result.ok
                    else (result.reason or "no text could be read from this file")
                ),
            )
        )
    return items


async def _all_message_ids(
    client: httpx.AsyncClient, token: str, query: str
) -> list[str]:
    """Every message id the token can list, oldest first.

    The API lists newest-first; reversing the complete listing gives the
    deterministic oldest-to-newest walk the checkpoint convention needs. The
    listing is ids only, so even a very large mailbox costs a few hundred
    cheap requests before any message body is fetched.
    """
    ids: list[str] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"maxResults": _LIST_PAGE_SIZE}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        payload = await g.google_get_json(
            client, f"{_API}/messages", token, _PRODUCT, params=params
        )
        for row in payload.get("messages") or []:
            if isinstance(row, dict) and row.get("id"):
                ids.append(str(row["id"]))
        page_token = str(payload.get("nextPageToken") or "") or None
        if not page_token:
            break
    ids.reverse()
    return ids


async def gmail_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every message in the connected mailbox, oldest to newest.

    ``checkpoint`` doubles as the resume/cursor convention: a numeric value is
    the incremental cursor (nanoseconds → ``after:`` query); anything else is
    the last imported message id from an interrupted backfill, and the walk
    resumes strictly after it. A checkpoint id that no longer exists (the
    message was deleted) degrades to a full walk — unchanged items upsert
    cheaply, and silently skipping the whole mailbox would be the real bug.
    ``transport`` is a test seam; production leaves it ``None``.
    """
    token = g.google_token(_PLUGIN_ID, _PRODUCT)
    cutoff = g.cursor_cutoff(checkpoint)
    query = f"after:{int(cutoff.timestamp())}" if cutoff else ""
    resume_after = checkpoint if (checkpoint and cutoff is None) else None
    yielded = 0
    attachments = 0

    async with httpx.AsyncClient(timeout=g.TIMEOUT, transport=transport) as client:
        ids = await _all_message_ids(client, token, query)
        if resume_after:
            try:
                ids = ids[ids.index(resume_after) + 1 :]
            except ValueError:
                log.info(
                    "Gmail adapter: checkpoint message %s no longer exists; "
                    "walking the full mailbox instead",
                    resume_after,
                )
        for message_id in ids:
            payload = await g.google_get_json(
                client,
                f"{_API}/messages/{message_id}",
                token,
                _PRODUCT,
                params={"format": "full"},
            )
            item = item_from_message(payload)
            if item is not None:
                yielded += 1
                yield item
            # After the message, never instead of it: a failure while reading
            # an attachment must not cost the mail it belongs to.
            for child in await items_from_attachments(client, token, payload):
                attachments += 1
                yielded += 1
                yield child

    log.info(
        "Gmail adapter: yielded %d item(s) for %s (%s), %d of them attachments",
        yielded,
        ctx.source_id,
        "incremental" if cutoff else "backfill",
        attachments,
    )
