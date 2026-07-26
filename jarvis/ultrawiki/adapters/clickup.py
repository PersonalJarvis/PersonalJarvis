"""ClickUp pull adapter — every task the connected token can list.

Scope, honestly stated. The adapter walks every workspace ("team") the
marketplace token reaches through ClickUp's filtered team-task walk, with
``include_closed`` and ``subtasks`` set — so open, closed and subtask rows all
arrive in one paginated stream, no per-list hierarchy crawl. Comments are
fetched per task and folded into the body chronologically, following ClickUp's
own comment pagination to the end (bounded at 1 000 per task, with an explicit
marker when the bound stops early).

What the token structurally cannot yield: tasks living in ARCHIVED lists —
the team-task walk does not return them, and ClickUp offers no team-wide door
to archived containers. Docs, whiteboards and chat are not tasks and are not
imported; file attachments are never downloaded — only comment text survives,
which is what a memory is for. ClickUp's budget is ~100 requests a minute and
comments cost one request per task, so a very large first backfill may hit the
wall mid-walk; the bounded Retry-After handling below stops honestly and the
next sync resumes from the checkpoint.

Cursor convention (the reference adapter's): a numeric checkpoint is the sync
runner's nanosecond cursor and narrows every task listing with
``date_updated_gt`` in epoch milliseconds (minus a five-minute overlap so a
boundary is safe; re-yielded items upsert as ``unchanged``). A ``task:<id>``
checkpoint is the bridge's backfill resume marker: tasks stream
oldest-to-newest in a deterministic order, so the walk resumes strictly after
that id. Any other checkpoint is read as "no cursor" and yields the full set.
Every item carries ``metadata["mtime_ns"]`` so the runner can advance its
cursor.

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
    "ClickUpAdapterError",
    "clickup_pull_adapter",
    "item_from_task",
]

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:clickup"

_API = "https://api.clickup.com/api/v2"

#: The team-task walk pages at 100 rows; a short page ends the walk.
_PAGE_SIZE = 100
#: Safety valve per workspace walk; at 100 rows a page this is 20 000 rows.
_MAX_PAGES = 200

#: ClickUp returns the 25 most recent comments per request; older pages follow
#: via ``start``/``start_id``. 40 pages bounds one task at 1 000 comments.
_COMMENT_PAGE = 25
_MAX_COMMENT_PAGES = 40

#: Generous on read: a cold walk over a large account is not instant, but a
#: settings screen must not hang on it either.
_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)

#: Bounded 429 handling: honor Retry-After up to a cap, then give up honestly.
_MAX_ATTEMPTS = 3
_RETRY_WAIT_CAP = 30.0
_DEFAULT_RETRY_WAIT = 2.0

#: Re-ask five minutes before the cursor so a boundary can never skip an item.
_SINCE_OVERLAP_SECONDS = 300

#: Memory-shaped bodies: cap one item at ~1 MB and say so, never silently.
_MAX_BODY_CHARS = 1_000_000
_TRUNCATION_NOTE = "\n\n[truncated — the full text lives at the link above]"

_MORE_COMMENTS_NOTE = "[more comments on ClickUp — open the link to read them]"


class ClickUpAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace ClickUp token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("clickup")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise ClickUpAdapterError(
            "The ClickUp connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise ClickUpAdapterError(
            "ClickUp is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise ClickUpAdapterError(
            "The ClickUp connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _headers(token: str) -> dict[str, str]:
    # ClickUp expects the BARE token in Authorization — no "Bearer" prefix;
    # its OAuth and personal tokens share this convention.
    return {
        "Authorization": token,
        "Accept": "application/json",
    }


def _retry_wait(response: httpx.Response | None) -> float:
    if response is None:
        return 0.0
    header = str(response.headers.get("Retry-After") or "").strip()
    try:
        wait = float(header)
    except ValueError:
        wait = _DEFAULT_RETRY_WAIT
    return min(max(wait, 0.0), _RETRY_WAIT_CAP)


async def _get(
    client: httpx.AsyncClient, url: str, token: str, params: dict[str, Any] | None = None
) -> httpx.Response:
    """One GET, with every failure mapped to a sentence a user can act on."""
    response: httpx.Response | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_retry_wait(response))
        try:
            response = await client.get(url, headers=_headers(token), params=params)
        except httpx.HTTPError as exc:
            raise ClickUpAdapterError(
                f"ClickUp was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if response.status_code != 429:
            break
    if response is None:  # pragma: no cover - loop always runs at least once
        raise ClickUpAdapterError("ClickUp answered nothing for this request.")
    if response.status_code == 401:
        raise ClickUpAdapterError(
            "ClickUp rejected the connection. Reconnect it in the Plugins store."
        )
    if response.status_code == 429:
        raise ClickUpAdapterError(
            "ClickUp's rate limit is exhausted. The next sync continues where "
            "this one stopped."
        )
    if response.status_code >= 400:
        raise ClickUpAdapterError(
            f"ClickUp answered HTTP {response.status_code} for this request."
        )
    return response


def _since_ms(checkpoint: str | None) -> int:
    """The stored numeric cursor as epoch milliseconds for ``date_updated_gt``."""
    if not checkpoint:
        return 0
    try:
        ns = int(checkpoint)
    except (TypeError, ValueError):
        return 0
    if ns <= 0:
        return 0
    ms = ns // 1_000_000 - _SINCE_OVERLAP_SECONDS * 1000
    return ms if ms > 0 else 0


def _resume_marker(checkpoint: str | None) -> str:
    """The checkpoint as a backfill resume id (``task:<id>``), or empty."""
    if checkpoint and checkpoint.startswith("task:"):
        return checkpoint
    return ""


def _ms_to_iso(ms: Any) -> str:
    """Epoch milliseconds → ISO-8601 UTC; empty when unparseable."""
    try:
        value = int(ms)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms_to_ns(ms: Any) -> int:
    """Epoch milliseconds → nanoseconds since the epoch; 0 when unparseable."""
    try:
        value = int(ms)
    except (TypeError, ValueError):
        return 0
    return value * 1_000_000 if value > 0 else 0


def _cap(text: str) -> str:
    if len(text) <= _MAX_BODY_CHARS:
        return text
    return text[:_MAX_BODY_CHARS] + _TRUNCATION_NOTE


async def _comment_lines(
    client: httpx.AsyncClient, token: str, task_id: str
) -> tuple[list[str], bool]:
    """One task's comments as chronological body lines + whether some remain.

    ClickUp serves comments newest-first, 25 a page; the walk follows the
    ``start``/``start_id`` pagination to the end and only stops early at the
    per-task bound, which the caller then declares in the body.
    """
    rows: list[dict[str, Any]] = []
    params: dict[str, Any] | None = None
    more = False
    for page in range(_MAX_COMMENT_PAGES):
        response = await _get(client, f"{_API}/task/{task_id}/comment", token, params=params)
        payload = response.json() or {}
        page_rows = payload.get("comments")
        if not isinstance(page_rows, list) or not page_rows:
            break
        rows.extend(row for row in page_rows if isinstance(row, dict))
        if len(page_rows) < _COMMENT_PAGE:
            break
        oldest = page_rows[-1]
        params = {
            "start": str(oldest.get("date") or ""),
            "start_id": str(oldest.get("id") or ""),
        }
        if page == _MAX_COMMENT_PAGES - 1:
            more = True
            log.info(
                "ClickUp adapter: stopping the comment walk for task %s at %d "
                "pages; the rest stays behind the permalink",
                task_id,
                _MAX_COMMENT_PAGES,
            )
    lines: list[tuple[int, str]] = []
    for row in rows:
        text = str(row.get("comment_text") or "").strip()
        if not text:
            continue
        author = str((row.get("user") or {}).get("username") or "")
        stamp = _ms_to_iso(row.get("date"))
        prefix = " · ".join(part for part in (stamp, author) if part)
        lines.append((_ms_to_ns(row.get("date")), f"[{prefix}] {text}" if prefix else text))
    lines.sort(key=lambda pair: pair[0])
    return [line for _stamp, line in lines], more


def item_from_task(
    task: dict[str, Any],
    *,
    comments: list[str],
    more_comments: bool = False,
) -> RawItem | None:
    """One ClickUp task as a ``RawItem``; ``None`` when it is unusable.

    The body is composed rather than copied raw: a bare description loses
    which list the task lives in and whether it was ever finished, and both
    are exactly what a later question turns on.
    """
    task_id = str(task.get("id") or "")
    permalink = str(task.get("url") or "").strip()
    if not task_id or not permalink:
        return None

    title = str(task.get("name") or "").strip() or f"task {task_id}"
    status = str((task.get("status") or {}).get("status") or "")
    archived = bool(task.get("archived"))
    assignees = [
        str(row.get("username") or "")
        for row in task.get("assignees") or []
        if isinstance(row, dict) and row.get("username")
    ]
    author = str((task.get("creator") or {}).get("username") or "")
    due = _ms_to_iso(task.get("due_date"))
    created = _ms_to_iso(task.get("date_created"))
    updated = _ms_to_iso(task.get("date_updated"))
    task_list = task.get("list") or {}
    list_id = str(task_list.get("id") or "")
    folder = task.get("folder") or {}
    # ClickUp models folderless lists as a "hidden" folder; naming it would
    # put the API's plumbing word into the record.
    folder_name = "" if folder.get("hidden") else str(folder.get("name") or "")
    context = " / ".join(part for part in (folder_name, str(task_list.get("name") or "")) if part)

    header_parts = [part for part in (context, "task", status) if part]
    if archived:
        header_parts.append("archived")
    if assignees:
        header_parts.append("assigned to " + ", ".join(assignees))
    if due:
        header_parts.append(f"due {due}")
    if author:
        header_parts.append(f"created by {author}")
    header = " · ".join(header_parts)

    body_text = str(task.get("description") or task.get("text_content") or "").strip()
    if comments:
        body_text += ("\n\n" if body_text else "") + "Comments:\n" + "\n".join(comments)
    if more_comments:
        body_text += ("\n\n" if body_text else "") + _MORE_COMMENTS_NOTE

    return RawItem(
        external_id=f"task:{task_id}",
        title=title,
        body=_cap(f"{header}\n\n{body_text}".strip()),
        permalink=permalink,
        # WHEN IT HAPPENED, not when it was last touched — the memory is about
        # the event. The modification time rides in metadata for the cursor.
        timestamp_utc=created or updated,
        thread_key=list_id,
        author_raw=author,
        metadata={
            "mtime_ns": _ms_to_ns(task.get("date_updated") or task.get("date_created")),
            "state": status,
            "archived": archived,
            "assignees": assignees,
            "due": due,
            "comments": len(comments),
            "updated_at": updated,
        },
    )


async def clickup_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every task the connected ClickUp token can list.

    ``checkpoint`` doubles as the incremental cursor (see the module
    docstring). ``transport`` is a test seam; production leaves it ``None``.
    """
    token = _token()
    since_ms = _since_ms(checkpoint)
    resume_after = _resume_marker(checkpoint)
    yielded = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        response = await _get(client, f"{_API}/team", token)
        teams = (response.json() or {}).get("teams")
        rows: list[dict[str, Any]] = []
        for team in sorted(
            teams if isinstance(teams, list) else [],
            key=lambda row: str((row or {}).get("id") or ""),
        ):
            team_id = str((team or {}).get("id") or "") if isinstance(team, dict) else ""
            if not team_id:
                continue
            for page in range(_MAX_PAGES):
                params: dict[str, Any] = {
                    "page": page,
                    "order_by": "created",
                    "reverse": "false",
                    "subtasks": "true",
                    "include_closed": "true",
                }
                if since_ms:
                    params["date_updated_gt"] = since_ms
                response = await _get(client, f"{_API}/team/{team_id}/task", token, params=params)
                payload = response.json() or {}
                page_rows = payload.get("tasks")
                if not isinstance(page_rows, list) or not page_rows:
                    break
                rows.extend(row for row in page_rows if isinstance(row, dict))
                if len(page_rows) < _PAGE_SIZE:
                    break
                if page == _MAX_PAGES - 1:
                    log.info(
                        "ClickUp adapter: stopping a walk at %d pages; the "
                        "next sync resumes from the cursor",
                        _MAX_PAGES,
                    )

        # Deterministic oldest-to-newest order, so the bridge's backfill
        # checkpoint (last flushed external_id) resumes strictly after it.
        seen: set[str] = set()
        ordered: list[dict[str, Any]] = []
        for row in sorted(
            rows,
            key=lambda r: (_ms_to_ns(r.get("date_created")), str(r.get("id") or "")),
        ):
            row_id = str(row.get("id") or "")
            if not row_id or row_id in seen:
                continue
            seen.add(row_id)
            ordered.append(row)

        start = 0
        if resume_after:
            for index, row in enumerate(ordered):
                if f"task:{row.get('id')}" == resume_after:
                    start = index + 1
                    break

        for row in ordered[start:]:
            comments, more = await _comment_lines(client, token, str(row.get("id") or ""))
            item = item_from_task(row, comments=comments, more_comments=more)
            if item is not None:
                yielded += 1
                yield item

    log.info(
        "ClickUp adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if since_ms else "backfill",
    )
