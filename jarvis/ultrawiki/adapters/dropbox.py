"""Dropbox pull adapter — the readable files in the connected Dropbox.

Of everything a Dropbox holds, this pulls the part that belongs in a *memory*:
the files with text inside — notes, exports, configs, and the documents a
Dropbox is actually full of (PDF, Word, Excel, PowerPoint, OpenDocument,
EPUB, RTF), read through the shared extraction service. Matching follows the
same extension-allowlist philosophy the local-folder connector uses (a curated
default set, overridable per source via ``config["extensions"]``). True blobs
— pictures, video, archives — are skipped honestly, by extension, before a
single byte is downloaded: this adapter ships no OCR, and pretending a JPEG is
a memory would only pollute search. Dropbox Paper docs and other entries the
API marks non-downloadable are excluded by the listing itself.

Scope, stated plainly:

* **Everything the token can list is walked** — ``files/list_folder`` with
  ``recursive=true`` plus ``list_folder/continue`` cursor paging, so team
  folders and deep trees are all reached. Only the extension allowlist and the
  size caps narrow what is *imported*.
* **Size caps:** a text file above 2 MiB (the local-folder connector's limit)
  and a document above ``MAX_DOCUMENT_BYTES`` are not downloaded; a downloaded
  text body is cut at 1 MiB with a visible truncation marker. Documents are
  never cut — half a container parses as nothing — so for them the ceiling
  refuses instead. **A file over any ceiling still becomes an item** carrying
  the reason: silence is indistinguishable from a missing file, and one of
  those two is a bug.
* **Incremental is client-side.** Dropbox's ``list_folder`` has no
  modified-since filter, and its own delta cursor (``list_folder/continue``
  across runs) is an opaque string that cannot ride the sync runner's NUMERIC
  cursor convention — so that cursor is used for in-run paging only. Instead
  every item carries ``metadata["mtime_ns"]`` (``server_modified``), the key
  the runner already advances; a numeric checkpoint filters files by that time
  BEFORE downloading, which is where the cost lives. The re-listing itself is
  cheap, and unchanged items upsert as ``unchanged``.

The walk is deterministic: entries are sorted by ``path_lower`` — which IS the
``external_id`` — so the bridge's backfill checkpoint (the last persisted id)
resumes strictly after it instead of restarting.

Credentials come from the marketplace token the user connected in the Plugins
store — this adapter never asks for a second one and never logs the token. A
token the REST API rejects (for instance one scoped away from it) surfaces as
the honest reconnect error on the source card.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from jarvis.ultrawiki.extract import (
    DOCUMENT_EXTENSIONS,
    MAX_DOCUMENT_BYTES,
    extract_text,
)
from jarvis.ultrawiki.types import ConnectorContext, RawItem

log = logging.getLogger(__name__)

__all__ = [
    "INCLUDED_EXTENSIONS",
    "INTEGRATION_ID",
    "TEXT_EXTENSIONS",
    "DropboxAdapterError",
    "dropbox_pull_adapter",
    "item_from_entry",
]

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:dropbox"

_API = "https://api.dropboxapi.com"
_CONTENT = "https://content.dropboxapi.com"

#: Dropbox's maximum page size for ``files/list_folder``.
_PAGE_LIMIT = 2000
#: Hard bound on ``list_folder/continue`` hops — 5 000 × 2 000 entries is far
#: beyond any personal account; the bound only exists so a misbehaving server
#: can never loop this walk forever.
_MAX_LIST_PAGES = 5000

#: Generous on read: downloads stream real file bodies, not JSON envelopes.
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

#: Listed size above this → the file is skipped before download (the
#: local-folder connector's limit, same philosophy).
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
#: A downloaded body is cut here, with a visible marker.
_MAX_BODY_BYTES = 1024 * 1024
_TRUNCATION_MARKER = "\n\n[truncated at 1 MiB — open the link for the full file]"

#: Bounded 429 handling: worst case a few short waits, never an unbounded stall.
_MAX_RETRIES = 3
_MAX_RETRY_WAIT = 30.0

#: The extension allowlist that decides "text-like". Same philosophy as the
#: local-folder connector's DEFAULT_EXTENSIONS, widened to the document-shaped
#: formats a Dropbox typically holds. Override per source with
#: ``config["extensions"]``.
TEXT_EXTENSIONS: tuple[str, ...] = (
    ".md",
    ".markdown",
    ".txt",
    ".text",
    ".rst",
    ".org",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
    ".tex",
    ".html",
    ".htm",
    ".xml",
)

#: What a walk actually looks at: the text shapes above PLUS every document
#: the extraction service can read. The two are separated because they are
#: fetched differently — a text file may be cut, a container may not.
INCLUDED_EXTENSIONS: tuple[str, ...] = TEXT_EXTENSIONS + tuple(
    sorted(DOCUMENT_EXTENSIONS)
)


def _is_document(path: str) -> bool:
    """True when this name belongs to the extractor rather than a UTF-8 read."""
    return PurePosixPath(path).suffix.lower() in DOCUMENT_EXTENSIONS


class DropboxAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace Dropbox token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("dropbox")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise DropboxAdapterError(
            "The Dropbox connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise DropboxAdapterError(
            "Dropbox is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise DropboxAdapterError(
            "The Dropbox connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _retry_wait(response: httpx.Response, attempt: int) -> float:
    """How long a 429 asks us to wait — honored, but never beyond the bound."""
    raw = response.headers.get("retry-after", "")
    try:
        wait = float(raw)
    except ValueError:
        wait = float(2**attempt)
    return min(max(wait, 0.0), _MAX_RETRY_WAIT)


async def _request(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    *,
    json_body: dict[str, Any] | None = None,
    api_arg: dict[str, Any] | None = None,
) -> httpx.Response:
    """One POST with bounded 429 retries; network failures become sentences.

    ``json_body`` is the RPC-endpoint shape (JSON body); ``api_arg`` is the
    content-endpoint shape (``Dropbox-API-Arg`` header, empty body). The two
    are never combined.
    """
    headers = {"Authorization": f"Bearer {token}"}
    if api_arg is not None:
        headers["Dropbox-API-Arg"] = json.dumps(api_arg)
    for attempt in range(_MAX_RETRIES + 1):
        try:
            if json_body is not None:
                response = await client.post(url, headers=headers, json=json_body)
            else:
                response = await client.post(url, headers=headers)
        except httpx.HTTPError as exc:
            raise DropboxAdapterError(
                f"Dropbox was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if response.status_code == 429 and attempt < _MAX_RETRIES:
            await asyncio.sleep(_retry_wait(response, attempt))
            continue
        return response
    raise DropboxAdapterError(  # pragma: no cover — the loop always returns
        "Dropbox kept rate-limiting this sync."
    )


def _raise_for_status(response: httpx.Response) -> None:
    """Map a failed response to a sentence a user can act on."""
    if response.status_code == 401:
        raise DropboxAdapterError(
            "Dropbox rejected the connection. Reconnect it in the Plugins store."
        )
    if response.status_code == 429:
        raise DropboxAdapterError(
            "Dropbox's rate limit is exhausted. The next sync continues where "
            "this one stopped."
        )
    if response.status_code >= 400:
        raise DropboxAdapterError(
            f"Dropbox answered HTTP {response.status_code} for this request."
        )


async def _api_post(
    client: httpx.AsyncClient, url: str, token: str, json_body: dict[str, Any]
) -> httpx.Response:
    response = await _request(client, url, token, json_body=json_body)
    _raise_for_status(response)
    return response


def _extensions(ctx: ConnectorContext) -> tuple[str, ...]:
    """The allowlist for this source — config override or the curated default.

    Same normalization the local-folder connector applies: entries may omit
    the leading dot, empties are dropped, an empty override falls back.
    """
    raw = ctx.config.get("extensions")
    if not raw:
        return INCLUDED_EXTENSIONS
    normalized: list[str] = []
    for entry in raw:
        text = str(entry).strip().lower()
        if not text:
            continue
        normalized.append(text if text.startswith(".") else f".{text}")
    return tuple(normalized) or INCLUDED_EXTENSIONS


async def _list_text_files(
    client: httpx.AsyncClient, token: str, extensions: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Every text-like file entry the token can list, via cursor paging."""
    entries: list[dict[str, Any]] = []
    response = await _api_post(
        client,
        f"{_API}/2/files/list_folder",
        token,
        {
            "path": "",
            "recursive": True,
            "include_deleted": False,
            "include_non_downloadable_files": False,
            "limit": _PAGE_LIMIT,
        },
    )
    for _page in range(_MAX_LIST_PAGES):
        payload = response.json() or {}
        rows = payload.get("entries")
        for entry in rows if isinstance(rows, list) else []:
            if not isinstance(entry, dict) or entry.get(".tag") != "file":
                continue
            path_lower = str(entry.get("path_lower") or "")
            if not path_lower:
                continue
            if PurePosixPath(path_lower).suffix not in extensions:
                continue
            if entry.get("is_downloadable") is False:
                # Paper docs and friends: files/download cannot fetch them.
                log.debug("dropbox adapter: %s is not downloadable; skipping", path_lower)
                continue
            entries.append(entry)
        if not payload.get("has_more"):
            break
        response = await _api_post(
            client,
            f"{_API}/2/files/list_folder/continue",
            token,
            {"cursor": str(payload.get("cursor") or "")},
        )
    else:  # pragma: no cover — needs 5 000 real pages
        log.warning(
            "dropbox adapter: stopped listing after %d pages; the next sync "
            "resumes from the checkpoint",
            _MAX_LIST_PAGES,
        )
    return entries


async def _download_body(
    client: httpx.AsyncClient, token: str, path_lower: str
) -> tuple[str, str] | None:
    """``(text, missing_reason)`` for one file; ``None`` skips it, auth aborts.

    A document (PDF, Word, deck, …) is handed to the shared extraction service
    instead of being decoded as UTF-8 — decoding a container produced a body of
    replacement characters, which is why these files were excluded outright
    and a Dropbox full of PDFs imported as the handful of notes beside them.

    A single vanished or unreadable file (deleted between list and download,
    a 409 path error) must not sink the other thousand — but a rejected token
    or an exhausted rate budget is not a per-file condition and raises.
    """
    response = await _request(
        client, f"{_CONTENT}/2/files/download", token, api_arg={"path": path_lower}
    )
    if response.status_code in (401, 429):
        _raise_for_status(response)
    if response.status_code >= 400:
        log.info(
            "dropbox adapter: skipping %s (download answered HTTP %d)",
            path_lower,
            response.status_code,
        )
        return None
    raw = response.content
    name = PurePosixPath(path_lower).name
    if _is_document(path_lower):
        result = extract_text(raw, filename=name)
        if result.ok:
            return (result.text, "")
        return ("", result.reason or "no text could be read from this file")
    text = raw[:_MAX_BODY_BYTES].decode("utf-8", errors="replace")
    if len(raw) > _MAX_BODY_BYTES:
        text += _TRUNCATION_MARKER
    return (text, "")


def _to_ns(iso: str) -> int:
    """ISO-8601 → nanoseconds since the epoch; 0 when unparseable."""
    if not iso:
        return 0
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1_000_000_000)


def _split_checkpoint(checkpoint: str | None) -> tuple[str | None, int]:
    """Read the checkpoint as (resume-after path, mtime-ns cursor).

    The bridge hands over ONE string that means two different things: during a
    backfill it is the last persisted ``external_id`` — here always a
    ``path_lower``, so it starts with ``/`` — and once the bridge runs
    incrementally it is the runner's numeric ``mtime_ns`` cursor. Anything
    else (or nothing) reads as a full walk, which idempotent upserts make safe.
    """
    if not checkpoint:
        return None, 0
    text = str(checkpoint).strip()
    if text.startswith("/"):
        return text, 0
    try:
        return None, max(int(text), 0)
    except ValueError:
        return None, 0


def _preview_url(path_display: str) -> str:
    """The Dropbox web preview link for one file path."""
    parts = [part for part in path_display.split("/") if part]
    if not parts:
        return "https://www.dropbox.com/home"
    folder = "/".join(quote(part) for part in parts[:-1])
    base = f"https://www.dropbox.com/home/{folder}" if folder else "https://www.dropbox.com/home"
    return f"{base}?preview={quote(parts[-1])}"


def item_from_entry(
    entry: dict[str, Any], body: str, *, content_missing_reason: str = ""
) -> RawItem | None:
    """One listed file plus its downloaded body as a ``RawItem``.

    ``content_missing_reason`` produces the item anyway, marked and explained:
    a scanned contract or an oversized archive stays findable by name, folder
    and date rather than disappearing without a trace.
    """
    path_lower = str(entry.get("path_lower") or "")
    if not path_lower:
        return None
    path_display = str(entry.get("path_display") or path_lower)
    modified = str(entry.get("server_modified") or entry.get("client_modified") or "")
    name = PurePosixPath(path_display)
    parent = path_lower.rsplit("/", 1)[0] or "/"
    if not body and content_missing_reason:
        body = f"File: {name.name}\n[no text imported: {content_missing_reason}]"
    return RawItem(
        external_id=path_lower,
        title=name.stem or name.name,
        body=body,
        permalink=_preview_url(path_display),
        timestamp_utc=modified,
        # The folder is the natural conversation: files that live together
        # tend to belong together.
        thread_key=parent,
        metadata={
            "mtime_ns": _to_ns(modified),
            "size_bytes": int(entry.get("size") or 0),
            "path_display": path_display,
            "rev": str(entry.get("rev") or ""),
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


async def dropbox_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every text-like file the connected Dropbox token can reach.

    ``checkpoint`` doubles as resume point and incremental cursor (see
    :func:`_split_checkpoint`). ``transport`` is a test seam; production
    leaves it ``None``.
    """
    token = _token()
    resume_after, min_mtime_ns = _split_checkpoint(checkpoint)
    yielded = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        entries = await _list_text_files(client, token, _extensions(ctx))
        # Sorted by external_id: the ONLY order under which "resume strictly
        # after the checkpointed id" is correct (the local-folder precedent).
        entries.sort(key=lambda row: str(row.get("path_lower") or ""))
        for entry in entries:
            path_lower = str(entry.get("path_lower") or "")
            if resume_after and path_lower <= resume_after:
                continue
            if min_mtime_ns:
                modified = str(
                    entry.get("server_modified") or entry.get("client_modified") or ""
                )
                if _to_ns(modified) <= min_mtime_ns:
                    continue  # unchanged since the cursor — never downloaded
            size = int(entry.get("size") or 0)
            ceiling = (
                MAX_DOCUMENT_BYTES if _is_document(path_lower) else _MAX_DOWNLOAD_BYTES
            )
            if size > ceiling:
                # Still an item. A file that vanishes from the knowledge base
                # because it was large reads exactly like a file that was never
                # there — and the second is a bug, so both must be visible.
                oversized = item_from_entry(
                    entry,
                    "",
                    content_missing_reason=(
                        f"the file is {size // (1024 * 1024)} MB, above the "
                        f"{ceiling // (1024 * 1024)} MB import limit"
                    ),
                )
                if oversized is not None:
                    yielded += 1
                    yield oversized
                continue
            fetched = await _download_body(client, token, path_lower)
            if fetched is None:
                continue
            body, missing_reason = fetched
            item = item_from_entry(
                entry, body, content_missing_reason=missing_reason
            )
            if item is not None:
                yielded += 1
                yield item

    log.info(
        "Dropbox adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if min_mtime_ns else "backfill",
    )
