"""Gmail pull adapter — every message the connected mailbox holds.

Of everything Gmail is, this pulls what belongs in a *memory*: the text of
every message — who wrote, to whom, when, and what was said. The walk is the
full mailbox (``users.messages.list`` paged to the end), oldest to newest, so
an interrupted backfill resumes strictly after the last imported message id.

Scope honesty — what the token allows vs. what is imported:

- **Message text only.** The plain-text part is preferred; an HTML-only
  message is stripped to readable text; a message with neither decodable part
  falls back to the API's snippet. Attachments are binaries and are NEVER
  downloaded — each item states how many it left behind, so the record never
  implies the body is the whole message.
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
from typing import Any

import httpx

from jarvis.ultrawiki.adapters import _google as g
from jarvis.ultrawiki.types import ConnectorContext, RawItem

log = logging.getLogger(__name__)

__all__ = [
    "INTEGRATION_ID",
    "GoogleAdapterError",
    "gmail_pull_adapter",
    "item_from_message",
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
    attachments = _attachment_count(payload)
    if attachments:
        # Binaries are never imported (memory-shaped scope); the record must
        # say what it does not contain instead of implying completeness.
        body_text += (
            f"\n\n[{attachments} attachment(s) not imported — "
            "open the link to download them]"
        )
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

    log.info(
        "Gmail adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if cutoff else "backfill",
    )
