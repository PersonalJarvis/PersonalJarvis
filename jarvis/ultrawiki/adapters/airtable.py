"""Airtable pull adapter — every record in every base the token can read.

Airtable is structured data, and a record's memory value lives in its field
values: this adapter walks ``meta/bases`` → tables → records and yields one
``RawItem`` per record, its fields flattened to ``Field: value`` lines in the
table's own schema order. Attachments are listed by name and URL and NEVER
downloaded — binaries do not belong in a knowledge base, but knowing a record
carries "photo.jpg" absolutely does.

Scope, stated plainly:

* **Everything the token allows is walked** — every base ``meta/bases``
  returns (offset-paged), every table of each base, every record of each
  table (offset-paged, 100 per request). A single base the token can list but
  not read (a missing scope on just that base, HTTP 403) is skipped with a
  log line so the rest still imports; a rejected token aborts honestly.
* **Freshness is a full rescan.** Airtable's list API has NO global
  modified-since filter, so there is nothing a cursor could ask the server
  for. The honest freshness path is re-running the walk and letting the
  idempotent upserts sort new/changed/unchanged — cheap in writes, one
  request per 100 records in reads. ``metadata["mtime_ns"]`` is still
  carried (from a ``lastModifiedTime`` field when the table has one, else
  ``createdTime``) so the runner's cursor advances, and a NUMERIC checkpoint
  is deliberately read as "full rescan" rather than mis-filtering.
* **Record comments are not imported** — the records API does not return
  them, and fetching them would cost one request per record.

The walk is deterministic: tables are visited in the string order of their
``<base>:<table>:`` prefix and records sorted by record id, which makes the
stream strictly increasing in ``external_id`` (``<base>:<table>:<record>``) —
exactly what lets the bridge's backfill checkpoint resume strictly after the
last persisted id. Tables whose every record sorts before the checkpoint are
skipped without a single records request.

Credentials come from the marketplace token the user connected in the Plugins
store — this adapter never asks for a second one and never logs the token.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from jarvis.ultrawiki.types import ConnectorContext, RawItem

log = logging.getLogger(__name__)

__all__ = [
    "INTEGRATION_ID",
    "AirtableAdapterError",
    "airtable_pull_adapter",
    "item_from_record",
]

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:airtable"

_API = "https://api.airtable.com"

#: Airtable's maximum records page size.
_PAGE_SIZE = 100
#: Hard bounds so a misbehaving server can never loop a walk forever. 5 000
#: record pages is half a million records in ONE table — beyond any plan.
_MAX_RECORD_PAGES = 5000
_MAX_BASE_PAGES = 100

_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)

#: A flattened record is normally tiny; the cap only guards a pathological
#: long-text field.
_MAX_BODY_BYTES = 1024 * 1024
_TRUNCATION_MARKER = "\n\n[truncated at 1 MiB — open the link for the full record]"

#: Bounded 429 handling. Airtable documents a 30-second penalty window, so
#: that is the default wait when no Retry-After header says otherwise.
_MAX_RETRIES = 3
_MAX_RETRY_WAIT = 30.0


class AirtableAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token.

    ``status`` carries the HTTP status when one applies, so the walk can skip
    a single denied base without abandoning everything the token CAN read.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _token() -> str:
    """The marketplace Airtable token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("airtable")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise AirtableAdapterError(
            "The Airtable connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise AirtableAdapterError(
            "Airtable is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise AirtableAdapterError(
            "The Airtable connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _retry_wait(response: httpx.Response) -> float:
    """How long a 429 asks us to wait — honored, but never beyond the bound."""
    raw = response.headers.get("retry-after", "")
    try:
        wait = float(raw)
    except ValueError:
        wait = _MAX_RETRY_WAIT  # Airtable's documented penalty window
    return min(max(wait, 0.0), _MAX_RETRY_WAIT)


async def _get(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """One GET with bounded 429 retries and user-actionable failure mapping."""
    response: httpx.Response | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}, params=params
            )
        except httpx.HTTPError as exc:
            raise AirtableAdapterError(
                f"Airtable was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if response.status_code == 429 and attempt < _MAX_RETRIES:
            await asyncio.sleep(_retry_wait(response))
            continue
        break
    assert response is not None  # the loop always assigns or raises
    if response.status_code == 401:
        raise AirtableAdapterError(
            "Airtable rejected the connection. Reconnect it in the Plugins store.",
            status=401,
        )
    if response.status_code == 403:
        raise AirtableAdapterError(
            "Airtable denied access to this data — the connection is missing a "
            "permission scope. Reconnect it in the Plugins store.",
            status=403,
        )
    if response.status_code == 429:
        raise AirtableAdapterError(
            "Airtable's rate limit is exhausted. The next sync continues where "
            "this one stopped.",
            status=429,
        )
    if response.status_code >= 400:
        raise AirtableAdapterError(
            f"Airtable answered HTTP {response.status_code} for this request.",
            status=response.status_code,
        )
    return response


async def _list_bases(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    """Every base the token can see, via offset paging."""
    bases: list[dict[str, Any]] = []
    offset: str | None = None
    for _page in range(_MAX_BASE_PAGES):
        params: dict[str, Any] = {"offset": offset} if offset else {}
        response = await _get(client, f"{_API}/v0/meta/bases", token, params=params)
        payload = response.json() or {}
        rows = payload.get("bases")
        bases.extend(row for row in (rows or []) if isinstance(row, dict))
        offset = str(payload.get("offset") or "") or None
        if not offset:
            break
    else:  # pragma: no cover — needs 100 real pages
        log.warning("airtable adapter: stopped listing bases after %d pages", _MAX_BASE_PAGES)
    return bases


async def _list_tables(
    client: httpx.AsyncClient, token: str, base_id: str
) -> list[dict[str, Any]]:
    """Every table of one base (the endpoint returns them all at once)."""
    response = await _get(client, f"{_API}/v0/meta/bases/{base_id}/tables", token)
    rows = (response.json() or {}).get("tables")
    return [row for row in (rows or []) if isinstance(row, dict)]


async def _list_records(
    client: httpx.AsyncClient, token: str, base_id: str, table_id: str
) -> list[dict[str, Any]]:
    """Every record of one table, via offset paging."""
    records: list[dict[str, Any]] = []
    offset: str | None = None
    for _page in range(_MAX_RECORD_PAGES):
        params: dict[str, Any] = {"pageSize": _PAGE_SIZE}
        if offset:
            params["offset"] = offset
        response = await _get(client, f"{_API}/v0/{base_id}/{table_id}", token, params=params)
        payload = response.json() or {}
        rows = payload.get("records")
        records.extend(row for row in (rows or []) if isinstance(row, dict))
        offset = str(payload.get("offset") or "") or None
        if not offset:
            break
    else:  # pragma: no cover — needs half a million real records
        log.warning(
            "airtable adapter: stopped listing records of %s/%s after %d pages",
            base_id,
            table_id,
            _MAX_RECORD_PAGES,
        )
    return records


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


def _resume_checkpoint(checkpoint: str | None) -> str | None:
    """The backfill resume id, or ``None`` for a full walk.

    A backfill checkpoint is the last persisted ``external_id`` — here always
    ``<base>:<table>:<record>``, so it contains colons. A NUMERIC checkpoint
    is the runner's mtime cursor; Airtable has no server-side modified filter
    to hand it to, so it deliberately reads as "full rescan" (idempotent
    upserts make that correct, the module docstring makes it honest).
    """
    if not checkpoint:
        return None
    text = str(checkpoint).strip()
    if ":" not in text:
        return None
    return text


def _render_value(value: Any) -> str:
    """One field value as display text; attachments become ``name (url)``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float | str):
        return str(value).strip()
    if isinstance(value, dict):
        # Attachments carry a filename; listed, never downloaded.
        if value.get("filename") or value.get("url"):
            name = str(value.get("filename") or "attachment")
            url = str(value.get("url") or "")
            return f"{name} ({url})" if url else name
        # Collaborators and similar objects: prefer a human-readable field.
        for key in ("name", "email", "label", "text", "id"):
            if value.get(key):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        parts = [_render_value(entry) for entry in value]
        return ", ".join(part for part in parts if part)
    return str(value)


def _schema_fields(table: dict[str, Any]) -> list[dict[str, Any]]:
    rows = table.get("fields")
    return [row for row in (rows or []) if isinstance(row, dict)]


def _modified_value(table: dict[str, Any], fields: dict[str, Any]) -> str:
    """The record's last-modified time, when the table tracks one."""
    for field in _schema_fields(table):
        if str(field.get("type") or "") == "lastModifiedTime":
            value = fields.get(str(field.get("name") or ""))
            if isinstance(value, str) and value:
                return value
    return ""


def _title_for(table: dict[str, Any], fields: dict[str, Any], record_id: str) -> str:
    """The primary field's value — the name Airtable itself shows the record as."""
    primary_id = str(table.get("primaryFieldId") or "")
    for field in _schema_fields(table):
        if str(field.get("id") or "") == primary_id:
            rendered = _render_value(fields.get(str(field.get("name") or "")))
            if rendered:
                return rendered
            break
    table_name = str(table.get("name") or "table")
    return f"{table_name} record {record_id}"


def item_from_record(
    base: dict[str, Any], table: dict[str, Any], record: dict[str, Any]
) -> RawItem | None:
    """One record as a ``RawItem``; ``None`` when it is unusable.

    The body flattens the fields in the table's own schema order (extra keys
    the schema does not know follow, sorted), because that order is the one
    the user arranged and reads the record in.
    """
    base_id = str(base.get("id") or "")
    table_id = str(table.get("id") or "")
    record_id = str(record.get("id") or "")
    if not base_id or not table_id or not record_id:
        return None
    fields = record.get("fields")
    if not isinstance(fields, dict):
        fields = {}

    created = str(record.get("createdTime") or "")
    modified = _modified_value(table, fields)
    timestamp = modified or created

    base_name = str(base.get("name") or base_id)
    table_name = str(table.get("name") or table_id)
    schema_order = [
        str(field.get("name") or "")
        for field in _schema_fields(table)
        if field.get("name")
    ]
    ordered = [name for name in schema_order if name in fields]
    ordered += sorted(name for name in fields if name not in schema_order)
    lines = []
    for name in ordered:
        rendered = _render_value(fields.get(name))
        if rendered:
            lines.append(f"{name}: {rendered}")

    body = f"{base_name} · {table_name} · record"
    if lines:
        body += "\n\n" + "\n".join(lines)
    if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        body = body.encode("utf-8")[:_MAX_BODY_BYTES].decode("utf-8", errors="replace")
        body += _TRUNCATION_MARKER

    return RawItem(
        external_id=f"{base_id}:{table_id}:{record_id}",
        title=_title_for(table, fields, record_id),
        body=body,
        permalink=f"https://airtable.com/{base_id}/{table_id}/{record_id}",
        timestamp_utc=timestamp,
        # The table is the natural conversation: its records belong together.
        thread_key=f"{base_id}:{table_id}",
        metadata={
            "mtime_ns": _to_ns(timestamp),
            "base": base_id,
            "base_name": base_name,
            "table": table_id,
            "table_name": table_name,
            "created_time": created,
            "modified_time": modified,
        },
    )


async def airtable_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every record of every base the connected Airtable token can read.

    ``checkpoint`` is the bridge's backfill resume id (see
    :func:`_resume_checkpoint`). ``transport`` is a test seam; production
    leaves it ``None``.
    """
    token = _token()
    resume_after = _resume_checkpoint(checkpoint)
    yielded = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        walk: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for base in await _list_bases(client, token):
            base_id = str(base.get("id") or "")
            if not base_id:
                continue
            try:
                tables = await _list_tables(client, token, base_id)
            except AirtableAdapterError as exc:
                if exc.status in (403, 404):
                    log.info("airtable adapter: skipping base %s (%s)", base_id, exc)
                    continue
                raise
            for table in tables:
                table_id = str(table.get("id") or "")
                if table_id:
                    walk.append((f"{base_id}:{table_id}:", base, table))
        # Sorted by the external_id PREFIX: with records sorted by id below,
        # the whole stream is strictly increasing in external_id — the only
        # order under which "resume strictly after the checkpoint" is correct.
        walk.sort(key=lambda row: row[0])

        for prefix, base, table in walk:
            if resume_after and not resume_after.startswith(prefix) and prefix < resume_after:
                # Every record of this table sorts at or before the
                # checkpoint — skip it without a single records request.
                continue
            base_id = str(base.get("id") or "")
            table_id = str(table.get("id") or "")
            try:
                records = await _list_records(client, token, base_id, table_id)
            except AirtableAdapterError as exc:
                if exc.status in (403, 404):
                    log.info(
                        "airtable adapter: skipping table %s/%s (%s)",
                        base_id,
                        table_id,
                        exc,
                    )
                    continue
                raise
            records.sort(key=lambda row: str(row.get("id") or ""))
            for record in records:
                item = item_from_record(base, table, record)
                if item is None:
                    continue
                if resume_after and item.external_id <= resume_after:
                    continue
                yielded += 1
                yield item

    log.info(
        "Airtable adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "resumed backfill" if resume_after else "full rescan",
    )
