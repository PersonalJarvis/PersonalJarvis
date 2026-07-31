"""Notion pull adapter — every page and database the integration can reach.

Of everything Notion holds, this pulls what belongs in a *memory*: the words.
It walks the Search API (ascending by ``last_edited_time``, cursor paging)
across everything shared with the integration, renders each standalone page's
block tree to plain text lines (``blocks.children``, recursive, bounded depth,
cycle-guarded), and reads every database's rows through
``databases/<id>/query`` with the row properties flattened to ``Name: value``
lines.

Item shape: ONE item per page (title plus rendered block content) and ONE item
per database row (title plus flattened properties). ``external_id`` is the
Notion page/row id, so a re-sync upserts the same rows instead of duplicating
them; the permalink is Notion's own ``url`` field.

HONEST SCOPE — what a token structurally cannot yield:

* An internal integration sees ONLY the pages and databases a user has
  explicitly shared with it (children included). Nothing else appears in
  Search, so "everything" here means everything SHARED — a workspace where the
  integration was never invited into the top-level pages imports nothing, and
  the fix is sharing those pages, not reconnecting.
* Database rows import their PROPERTIES, not their page body — fetching each
  row's block tree would cost one extra request per row and burn the rate
  budget (3 requests/second) on large databases. A row whose knowledge lives
  in its body appears with title and properties only.
* Comments are not imported (a separate API needing its own capability, one
  request per block). Files and images are imported as ``[file: ...]``-style
  markers, never as binary content — text is the memory, blobs are not.
* Nesting beyond 8 block levels is not descended into; a marker line says so
  in the rendered page.

Rate limits: Notion answers HTTP 429 with ``Retry-After``; every request
honours it with bounded waits (at most four attempts, at most 30 s each)
before failing honestly.

The incremental cursor rides on ``metadata["mtime_ns"]`` — the key the sync
runner already advances — carrying each item's ``last_edited_time``. A numeric
checkpoint narrows the import to items edited on or after the day before the
cursor's day (Search itself has no time filter, so the listing is still
walked, but the expensive per-page block fetches are skipped for untouched
pages; database queries filter server-side). Touched pages are re-yielded in
full and upsert on their content hash. A non-numeric checkpoint — the bridge
passes the last item's id during a backfill — is read as "no cursor" and
yields the full set.

Credentials come from the marketplace token the user connected in the Plugins
store — this adapter never asks for a second one and never logs the token.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from jarvis.ultrawiki.types import ConnectorContext, RawItem

log = logging.getLogger(__name__)

__all__ = [
    "INTEGRATION_ID",
    "NotionAdapterError",
    "notion_pull_adapter",
]

#: The plugin-bridge candidate id this adapter serves (catalog id ``notion``).
INTEGRATION_ID = "plugin:notion"

_API = "https://api.notion.com/v1"

#: Pinned API version: the last version where ``databases/<id>/query`` is the
#: one stable row-reading path (later versions split databases into data
#: sources). Upgrading is a deliberate change, never an accident.
_NOTION_VERSION = "2022-06-28"

#: Notion's maximum page size for search, query and block listings.
_PAGE_SIZE = 100
_MAX_SEARCH_PAGES = 200  # 20 000 shared pages/databases per walk
_MAX_QUERY_PAGES = 200  # 20 000 rows per database
_MAX_BLOCK_PAGES = 50  # 5 000 blocks per nesting level

#: Recursive block rendering stops here; a marker line keeps the record honest.
_MAX_DEPTH = 8

#: Bounded 429 handling: honour Retry-After, but never wait unboundedly.
_MAX_ATTEMPTS = 4
_MAX_RETRY_WAIT = 30.0

#: Cap any single item body; a marker says the record is incomplete.
_MAX_BODY_BYTES = 1_000_000
_TRUNCATION_MARKER = "\n\n[truncated — this item exceeded the 1 MB import cap]"
_DEPTH_MARKER = "[deeper content not imported — nesting exceeds the depth cap]"

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class NotionAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace Notion token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("notion")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise NotionAdapterError(
            "The Notion connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise NotionAdapterError(
            "Notion is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise NotionAdapterError(
            "The Notion connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Notion-Version": _NOTION_VERSION}


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    token: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """One API call with bounded 429 retries and honest failure mapping.

    Returns the response — including 4xx responses, which each caller handles
    per its own degradation rule. Only transport failures, dead credentials
    (401) and exhausted rate budgets raise.
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = await client.request(
                method, f"{_API}{path}", headers=_headers(token), json=json_body, params=params
            )
        except httpx.HTTPError as exc:
            raise NotionAdapterError(
                f"Notion was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if response.status_code == 429:
            if attempt == _MAX_ATTEMPTS:
                break
            try:
                delay = float(response.headers.get("retry-after") or "")
            except ValueError:
                delay = 1.0
            await asyncio.sleep(min(max(delay, 0.0), _MAX_RETRY_WAIT))
            continue
        if response.status_code == 401:
            raise NotionAdapterError(
                "Notion rejected the connection. Reconnect it in the Plugins store."
            )
        return response
    raise NotionAdapterError(
        "Notion's rate limit persisted after several bounded waits. "
        "The next sync continues where this one stopped."
    )


async def _validate(client: httpx.AsyncClient, token: str) -> None:
    """Confirm the token works before walking anything (401 raises inside)."""
    response = await _request(client, "GET", "/users/me", token)
    if response.status_code >= 400:
        raise NotionAdapterError(
            f"Notion could not confirm the connection (HTTP {response.status_code})."
        )


def _to_ns(iso: str) -> int:
    """ISO-8601 → nanoseconds since the epoch; 0 when unparseable."""
    if not iso:
        return 0
    try:
        moment = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1_000_000_000)


def _cursor_to_since(checkpoint: str | None) -> str:
    """The stored numeric cursor as an ISO date for ``on_or_after``. Empty when unset.

    The cursor is nanoseconds since the epoch (the sync runner's convention).
    A day is subtracted before filtering: re-asking from the previous day is
    what guarantees nothing is skipped at a boundary, and re-yielded items
    upsert as ``unchanged``. A non-numeric checkpoint — the bridge passes the
    last item's id during a backfill — is read as "no cursor".
    """
    if not checkpoint:
        return ""
    try:
        seconds = int(checkpoint) / 1_000_000_000
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    moment = datetime.fromtimestamp(seconds, tz=UTC)
    return datetime.fromordinal(moment.toordinal() - 1).strftime("%Y-%m-%d")


def _plain(rich: Any) -> str:
    """A Notion rich-text array as plain prose; empty for anything else."""
    if not isinstance(rich, list):
        return ""
    return "".join(
        str(part.get("plain_text") or "") for part in rich if isinstance(part, dict)
    ).strip()


def _title_of(properties: Any) -> str:
    """The value of the one ``title``-typed property; empty when absent."""
    if not isinstance(properties, dict):
        return ""
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return _plain(prop.get("title"))
    return ""


def _permalink_of(obj: dict[str, Any]) -> str:
    """Notion's own ``url``, or the canonical constructed form as fallback."""
    url = str(obj.get("url") or "").strip()
    if url:
        return url
    object_id = str(obj.get("id") or "").replace("-", "")
    return f"https://www.notion.so/{object_id}" if object_id else ""


def _parent_id(obj: dict[str, Any]) -> str:
    """The direct parent's id (page, database or block); empty for workspace."""
    parent = obj.get("parent")
    if not isinstance(parent, dict):
        return ""
    parent_type = str(parent.get("type") or "")
    return str(parent.get(parent_type) or "") if parent_type != "workspace" else ""


def _cap_body(body: str) -> str:
    encoded = body.encode("utf-8")
    if len(encoded) <= _MAX_BODY_BYTES:
        return body
    kept = encoded[:_MAX_BODY_BYTES].decode("utf-8", errors="ignore").rstrip()
    return kept + _TRUNCATION_MARKER


# ---------------------------------------------------------------------------
# Block rendering — a page's block tree as plain text lines
# ---------------------------------------------------------------------------


def _block_line(block: dict[str, Any], number: int) -> str:
    """One block as one plain-text line (or fenced lines for code).

    ``number`` is the running index inside a contiguous numbered-list run.
    Media blocks become markers — the name or caption is memory, the bytes
    are not (and never will be pulled).
    """
    btype = str(block.get("type") or "")
    payload = block.get(btype) if isinstance(block.get(btype), dict) else {}
    text = _plain(payload.get("rich_text"))
    if btype == "paragraph":
        return text
    if btype == "heading_1":
        return f"# {text}" if text else ""
    if btype == "heading_2":
        return f"## {text}" if text else ""
    if btype == "heading_3":
        return f"### {text}" if text else ""
    if btype == "bulleted_list_item":
        return f"- {text}" if text else ""
    if btype == "numbered_list_item":
        return f"{number}. {text}" if text else ""
    if btype == "to_do":
        mark = "x" if payload.get("checked") else " "
        return f"[{mark}] {text}" if text else ""
    if btype == "quote":
        return f"> {text}" if text else ""
    if btype == "code":
        language = str(payload.get("language") or "").strip()
        return f"```{language}\n{text}\n```" if text else ""
    if btype == "callout":
        return f"[callout] {text}" if text else ""
    if btype == "toggle":
        return text
    if btype == "child_page":
        title = str(payload.get("title") or "").strip()
        return f"[subpage: {title}]" if title else "[subpage]"
    if btype == "child_database":
        title = str(payload.get("title") or "").strip()
        return f"[database: {title}]" if title else "[database]"
    if btype == "table_row":
        cells = payload.get("cells")
        if isinstance(cells, list):
            return " | ".join(_plain(cell) for cell in cells)
        return ""
    if btype == "equation":
        return str(payload.get("expression") or "").strip()
    if btype == "divider":
        return "---"
    if btype in ("bookmark", "embed", "link_preview"):
        url = str(payload.get("url") or "").strip()
        caption = _plain(payload.get("caption"))
        if caption and url:
            return f"{caption} ({url})"
        return url or caption
    if btype in ("image", "file", "pdf", "video", "audio"):
        label = _plain(payload.get("caption")) or str(payload.get("name") or "").strip()
        if not label:
            external = payload.get("external")
            label = str(external.get("url") or "") if isinstance(external, dict) else ""
        return f"[{btype}: {label}]" if label else f"[{btype}]"
    # Unknown or structural types (synced_block, column_list, ...): keep any
    # words they carry; their children are still descended into.
    return text


async def _block_children(
    client: httpx.AsyncClient, token: str, block_id: str
) -> list[dict[str, Any]]:
    """One level of a block's children, fully paged. Empty on a refusal."""
    blocks: list[dict[str, Any]] = []
    cursor = ""
    for _page in range(_MAX_BLOCK_PAGES):
        params: dict[str, Any] = {"page_size": _PAGE_SIZE}
        if cursor:
            params["start_cursor"] = cursor
        response = await _request(
            client, "GET", f"/blocks/{block_id}/children", token, params=params
        )
        if response.status_code >= 400:
            log.debug(
                "Notion adapter: block children unavailable for %s (HTTP %d)",
                block_id,
                response.status_code,
            )
            break
        payload: dict[str, Any] = response.json() or {}
        blocks.extend(b for b in payload.get("results") or [] if isinstance(b, dict))
        if not payload.get("has_more"):
            break
        cursor = str(payload.get("next_cursor") or "")
        if not cursor:
            break
    else:
        log.warning(
            "Notion adapter: block %s exceeded the children page cap; "
            "imported what was read",
            block_id,
        )
    return blocks


async def _render_children(
    client: httpx.AsyncClient,
    token: str,
    block_id: str,
    depth: int,
    visited: set[str],
    lines: list[str],
) -> None:
    """Render one block's children into ``lines``, bounded and cycle-guarded."""
    if block_id in visited:
        return  # a synced/self-referencing block must not loop the import
    visited.add(block_id)
    if depth >= _MAX_DEPTH:
        lines.append("  " * depth + _DEPTH_MARKER)
        return
    number = 0
    for block in await _block_children(client, token, block_id):
        btype = str(block.get("type") or "")
        number = number + 1 if btype == "numbered_list_item" else 0
        line = _block_line(block, number)
        if line:
            indent = "  " * depth
            lines.append("\n".join(indent + part for part in line.split("\n")))
        # Sub-pages and inline databases are their own search results (when
        # shared); descending into them here would import them twice.
        if block.get("has_children") and btype not in ("child_page", "child_database"):
            child_id = str(block.get("id") or "")
            if child_id:
                await _render_children(client, token, child_id, depth + 1, visited, lines)


# ---------------------------------------------------------------------------
# Property flattening — a database row's properties as ``Name: value`` lines
# ---------------------------------------------------------------------------


def _flatten_property(prop: Any) -> str:
    """One property value as a short plain string; empty when it has none."""
    if not isinstance(prop, dict):
        return ""
    ptype = str(prop.get("type") or "")
    value = prop.get(ptype)
    if ptype in ("title", "rich_text"):
        return _plain(value)
    if ptype in ("number",):
        return "" if value is None else str(value)
    if ptype in ("select", "status"):
        return str((value or {}).get("name") or "") if isinstance(value, dict) else ""
    if ptype == "multi_select":
        if not isinstance(value, list):
            return ""
        return ", ".join(
            str(entry.get("name") or "") for entry in value if isinstance(entry, dict)
        )
    if ptype == "date":
        if not isinstance(value, dict):
            return ""
        start = str(value.get("start") or "")
        end = str(value.get("end") or "")
        return f"{start} → {end}" if start and end else start
    if ptype == "people":
        if not isinstance(value, list):
            return ""
        return ", ".join(
            str(person.get("name") or person.get("id") or "")
            for person in value
            if isinstance(person, dict)
        )
    if ptype == "files":
        if not isinstance(value, list):
            return ""
        return ", ".join(
            f"[file: {entry.get('name') or 'attachment'}]"
            for entry in value
            if isinstance(entry, dict)
        )
    if ptype in ("checkbox", "boolean"):
        return "yes" if value else "no"
    if ptype in ("url", "email", "phone_number", "string", "created_time", "last_edited_time"):
        return str(value or "")
    if ptype in ("formula", "rollup"):
        # Both wrap an inner ``{"type": X, X: value}`` shape — recurse once.
        if isinstance(value, dict) and str(value.get("type") or "") == "array":
            entries = value.get("array")
            if not isinstance(entries, list):
                return ""
            return ", ".join(
                flat for entry in entries if (flat := _flatten_property(entry))
            )
        return _flatten_property(value)
    if ptype in ("created_by", "last_edited_by"):
        return str((value or {}).get("name") or "") if isinstance(value, dict) else ""
    if ptype == "relation":
        count = len(value) if isinstance(value, list) else 0
        return f"[{count} linked page(s)]" if count else ""
    if ptype == "unique_id":
        if not isinstance(value, dict):
            return ""
        prefix = str(value.get("prefix") or "")
        num = value.get("number")
        if num is None:
            return ""
        return f"{prefix}-{num}" if prefix else str(num)
    return ""


# ---------------------------------------------------------------------------
# Item mapping
# ---------------------------------------------------------------------------


def _row_item(row: dict[str, Any], database_title: str, database_id: str) -> RawItem | None:
    """One database row as one item — title plus flattened properties."""
    row_id = str(row.get("id") or "")
    permalink = _permalink_of(row)
    if not row_id or not permalink:
        return None
    properties = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    title = _title_of(properties) or "Untitled"
    lines = [
        f"{name}: {flat}"
        for name in sorted(properties)
        if isinstance(properties.get(name), dict)
        and properties[name].get("type") != "title"
        and (flat := _flatten_property(properties[name]))
    ]
    header = f"{database_title} · database row" if database_title else "Notion database row"
    edited = str(row.get("last_edited_time") or row.get("created_time") or "")
    return RawItem(
        external_id=row_id,
        title=title,
        body=_cap_body("\n".join([header, "", title, *lines]).strip()),
        permalink=permalink,
        timestamp_utc=edited,
        thread_key=database_id or _parent_id(row) or row_id,
        metadata={
            "mtime_ns": _to_ns(edited),
            "kind": "database_row",
            "database_id": database_id or _parent_id(row),
            "database_title": database_title,
            "created_time": str(row.get("created_time") or ""),
        },
    )


def _page_item(page: dict[str, Any], lines: list[str]) -> RawItem | None:
    """One standalone page as one item — title plus its rendered block tree."""
    page_id = str(page.get("id") or "")
    permalink = _permalink_of(page)
    if not page_id or not permalink:
        return None
    title = _title_of(page.get("properties")) or "Untitled"
    edited = str(page.get("last_edited_time") or page.get("created_time") or "")
    parent = _parent_id(page)
    return RawItem(
        external_id=page_id,
        title=title,
        body=_cap_body("\n".join([f"Notion page · {title}", "", *lines]).strip()),
        permalink=permalink,
        timestamp_utc=edited,
        thread_key=parent or page_id,
        metadata={
            "mtime_ns": _to_ns(edited),
            "kind": "page",
            "parent_id": parent,
            "created_time": str(page.get("created_time") or ""),
        },
    )


# ---------------------------------------------------------------------------
# Walks
# ---------------------------------------------------------------------------


async def _search(client: httpx.AsyncClient, token: str) -> AsyncIterator[dict[str, Any]]:
    """Every page and database shared with the integration, oldest-edited first."""
    cursor = ""
    for _page in range(_MAX_SEARCH_PAGES):
        body: dict[str, Any] = {
            "page_size": _PAGE_SIZE,
            "sort": {"direction": "ascending", "timestamp": "last_edited_time"},
        }
        if cursor:
            body["start_cursor"] = cursor
        response = await _request(client, "POST", "/search", token, json_body=body)
        if response.status_code >= 400:
            raise NotionAdapterError(
                f"Notion answered HTTP {response.status_code} while listing "
                "the shared content."
            )
        payload: dict[str, Any] = response.json() or {}
        for result in payload.get("results") or []:
            if isinstance(result, dict):
                yield result
        if not payload.get("has_more"):
            return
        cursor = str(payload.get("next_cursor") or "")
        if not cursor:
            return
    log.warning(
        "Notion adapter: the search walk exceeded the page cap; imported what was found"
    )


async def _database_rows(
    client: httpx.AsyncClient, token: str, database_id: str, since: str
) -> list[dict[str, Any]] | None:
    """Every row of one database, oldest-edited first. ``None`` = honestly skipped."""
    rows: list[dict[str, Any]] = []
    cursor = ""
    for _page in range(_MAX_QUERY_PAGES):
        body: dict[str, Any] = {
            "page_size": _PAGE_SIZE,
            "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
        }
        if since:
            body["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {"on_or_after": since},
            }
        if cursor:
            body["start_cursor"] = cursor
        response = await _request(
            client, "POST", f"/databases/{database_id}/query", token, json_body=body
        )
        if response.status_code >= 400:
            # A linked or restricted database refuses its query; rows the token
            # CAN see still arrive as search results and import there.
            log.info(
                "Notion adapter: skipping database %s (HTTP %d); rows shared "
                "with the integration still import via search",
                database_id,
                response.status_code,
            )
            return None
        payload: dict[str, Any] = response.json() or {}
        rows.extend(r for r in payload.get("results") or [] if isinstance(r, dict))
        if not payload.get("has_more"):
            break
        cursor = str(payload.get("next_cursor") or "")
        if not cursor:
            break
    else:
        log.warning(
            "Notion adapter: database %s exceeded the query page cap; "
            "imported what was read",
            database_id,
        )
    return rows


async def notion_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every page and database row the connected Notion token can read.

    ``checkpoint`` doubles as the incremental cursor: numeric means "only what
    changed since", anything else means a full backfill. ``transport`` is a
    test seam; production leaves it ``None``.
    """
    token = _token()
    since = _cursor_to_since(checkpoint)
    since_ns = _to_ns(f"{since}T00:00:00Z") if since else 0
    yielded = 0
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        await _validate(client, token)
        async for result in _search(client, token):
            kind = str(result.get("object") or "")
            if kind == "database":
                database_id = str(result.get("id") or "")
                if not database_id:
                    continue
                rows = await _database_rows(client, token, database_id, since)
                if rows is None:
                    continue
                database_title = _plain(result.get("title"))
                for row in rows:
                    item = _row_item(row, database_title, database_id)
                    if item is not None and item.external_id not in seen:
                        seen.add(item.external_id)
                        yielded += 1
                        yield item
            elif kind == "page":
                page_id = str(result.get("id") or "")
                if not page_id or page_id in seen:
                    continue
                edited_ns = _to_ns(str(result.get("last_edited_time") or ""))
                if since_ns and edited_ns and edited_ns < since_ns:
                    continue  # untouched since the cursor; skip the block fetches
                parent = result.get("parent") if isinstance(result.get("parent"), dict) else {}
                if str(parent.get("type") or "") == "database_id":
                    # A row whose database query already ran is in ``seen``;
                    # this path catches rows whose database is not queryable.
                    item = _row_item(result, "", str(parent.get("database_id") or ""))
                else:
                    lines: list[str] = []
                    await _render_children(client, token, page_id, 0, set(), lines)
                    item = _page_item(result, lines)
                if item is not None and item.external_id not in seen:
                    seen.add(item.external_id)
                    yielded += 1
                    yield item

    log.info(
        "Notion adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if since else "backfill",
    )
