"""Todoist pull adapter — the full task snapshot plus completed history.

Scope, honestly stated. One Sync-API request returns EVERY active task the
account holds, with its project names, comments ("notes") and collaborator
names in the same payload — no per-task requests at all. A second, paginated
walk over ``completed/get_all`` adds the completed history, as far back as
the user's Todoist plan retains it (free plans keep less history than Pro;
the adapter imports whatever the endpoint yields and cannot reach past that).
Deleted tasks are skipped, not tombstoned — the bridge does not reconcile
deletes for this source. File attachments on comments are never downloaded;
only their text survives, which is what a memory is for.

Structural limits, stated plainly: the Sync API gives active tasks NO
modification timestamp, so a numeric cursor cannot narrow the read — every
run is a full snapshot, which costs one request plus the completed walk and
upserts unchanged items as ``unchanged``. ``metadata["mtime_ns"]`` is
therefore the best honest signal (the newest of creation, completion and
latest comment), not a true modified time. The API also provides no web URL
per task; the permalink is the canonical ``app.todoist.com`` task address.

Cursor convention: a ``task:<id>`` checkpoint is the bridge's backfill resume
marker — items stream oldest-to-newest deterministically, so the walk resumes
strictly after that id. A numeric checkpoint (the runner's nanosecond cursor)
is accepted but, per the limit above, still yields the full snapshot. Any
other non-empty checkpoint is treated as a Todoist ``sync_token`` and asked
as an incremental delta; a token the server rejects falls back to a full
snapshot rather than failing the sync.

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
    "TodoistAdapterError",
    "item_from_task",
    "todoist_pull_adapter",
]

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:todoist"

_API = "https://api.todoist.com/sync/v9"

_RESOURCE_TYPES = '["projects","items","notes","collaborators"]'

#: completed/get_all pages at up to 200 rows; 200 pages bounds the walk at
#: 40 000 completed tasks per sync — the next sync continues from the cursor.
_COMPLETED_PAGE = 200
_MAX_COMPLETED_PAGES = 200

_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)

_MAX_ATTEMPTS = 3
_RETRY_WAIT_CAP = 30.0
_DEFAULT_RETRY_WAIT = 2.0

_MAX_BODY_CHARS = 1_000_000
_TRUNCATION_NOTE = "\n\n[truncated — the full text lives at the link above]"


class TodoistAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace Todoist token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("todoist")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise TodoistAdapterError(
            "The Todoist connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise TodoistAdapterError(
            "Todoist is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise TodoistAdapterError(
            "The Todoist connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _retry_wait(response: httpx.Response | None) -> float:
    if response is None:
        return 0.0
    header = str(response.headers.get("Retry-After") or "").strip()
    try:
        wait = float(header)
    except ValueError:
        wait = _DEFAULT_RETRY_WAIT
    return min(max(wait, 0.0), _RETRY_WAIT_CAP)


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    tolerate_400: bool = False,
) -> httpx.Response | None:
    """One round trip, every failure mapped to an actionable sentence.

    ``tolerate_400`` returns ``None`` on HTTP 400 instead of raising — the
    seam for a stale ``sync_token`` the caller wants to retry as a full read.
    """
    response: httpx.Response | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_retry_wait(response))
        try:
            response = await client.request(
                method, url, headers=_headers(token), params=params, data=data
            )
        except httpx.HTTPError as exc:
            raise TodoistAdapterError(
                f"Todoist was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if response.status_code != 429:
            break
    if response is None:  # pragma: no cover - loop always runs at least once
        raise TodoistAdapterError("Todoist answered nothing for this request.")
    if response.status_code in (401, 403):
        raise TodoistAdapterError(
            "Todoist rejected the connection. Reconnect it in the Plugins store."
        )
    if response.status_code == 429:
        raise TodoistAdapterError(
            "Todoist's rate limit is exhausted. The next sync continues where "
            "this one stopped."
        )
    if response.status_code == 400 and tolerate_400:
        return None
    if response.status_code >= 400:
        raise TodoistAdapterError(
            f"Todoist answered HTTP {response.status_code} for this request."
        )
    return response


def _resume_marker(checkpoint: str | None) -> str:
    """The checkpoint as a backfill resume id (``task:<id>``), or empty."""
    if checkpoint and checkpoint.startswith("task:"):
        return checkpoint
    return ""


def _sync_token_of(checkpoint: str | None) -> str:
    """A checkpoint that is neither numeric nor a resume id is a sync token."""
    if not checkpoint or checkpoint.startswith("task:"):
        return ""
    try:
        int(checkpoint)
    except (TypeError, ValueError):
        return checkpoint
    return ""


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


def _cap(text: str) -> str:
    if len(text) <= _MAX_BODY_CHARS:
        return text
    return text[:_MAX_BODY_CHARS] + _TRUNCATION_NOTE


def _comment_lines(notes: list[dict[str, Any]]) -> tuple[list[str], str]:
    """One task's notes as chronological body lines, plus the newest post time.

    Attachment files are never downloaded; only the note's text survives.
    """
    rows: list[tuple[str, str]] = []
    for note in notes:
        if not isinstance(note, dict) or note.get("is_deleted"):
            continue
        text = str(note.get("content") or "").strip()
        if not text:
            continue
        posted = str(note.get("posted_at") or "")
        rows.append((posted, f"[{posted}] {text}" if posted else text))
    rows.sort(key=lambda pair: pair[0])
    latest = rows[-1][0] if rows else ""
    return [line for _posted, line in rows], latest


def item_from_task(
    task: dict[str, Any],
    *,
    project_name: str,
    project_id: str,
    assignee: str,
    comments: list[str],
    last_comment_at: str = "",
) -> RawItem | None:
    """One Todoist task as a ``RawItem``; ``None`` when it is unusable."""
    task_id = str(task.get("id") or "")
    title = str(task.get("content") or "").strip()
    if not task_id or not title:
        return None

    state = "completed" if task.get("checked") else "open"
    due = str((task.get("due") or {}).get("date") or "") if task.get("due") else ""
    created = str(task.get("added_at") or "")
    completed_at = str(task.get("completed_at") or "")

    header_parts = [part for part in (project_name, "task", state) if part]
    if assignee:
        header_parts.append(f"assigned to {assignee}")
    if due:
        header_parts.append(f"due {due}")
    header = " · ".join(header_parts)

    body_text = str(task.get("description") or "").strip()
    if comments:
        body_text += ("\n\n" if body_text else "") + "Comments:\n" + "\n".join(comments)

    # The newest signal the API exposes — creation, completion or the latest
    # comment. NOT a true modified time; the Sync API does not provide one.
    mtime_ns = max(_to_ns(created), _to_ns(completed_at), _to_ns(last_comment_at))

    return RawItem(
        external_id=f"task:{task_id}",
        title=title,
        body=_cap(f"{header}\n\n{body_text}".strip()),
        # The Sync API carries no web URL; this is the canonical task address.
        permalink=f"https://app.todoist.com/app/task/{task_id}",
        timestamp_utc=created or completed_at,
        thread_key=project_id,
        author_raw=assignee,
        metadata={
            "mtime_ns": mtime_ns,
            "state": state,
            "project": project_id,
            "due": due,
            "comments": len(comments),
            "completed_at": completed_at,
        },
    )


async def _sync_payload(
    client: httpx.AsyncClient, token: str, sync_token: str
) -> dict[str, Any] | None:
    """One Sync-API read; ``None`` when the server rejected a stale token."""
    response = await _request(
        client,
        "POST",
        f"{_API}/sync",
        token,
        data={"sync_token": sync_token, "resource_types": _RESOURCE_TYPES},
        # "*" is Todoist's "give me everything" sentinel and sync_token is its
        # PAGINATION cursor — neither is a credential; the names merely collide
        # with the hardcoded-secret heuristic.
        tolerate_400=sync_token != "*",  # noqa: S105
    )
    if response is None:
        return None
    payload = response.json() or {}
    return payload if isinstance(payload, dict) else {}


async def _completed_rows(
    client: httpx.AsyncClient, token: str
) -> list[dict[str, Any]]:
    """Every completed task the plan's history retains, oldest data included."""
    rows: list[dict[str, Any]] = []
    for page in range(_MAX_COMPLETED_PAGES):
        response = await _request(
            client,
            "GET",
            f"{_API}/completed/get_all",
            token,
            params={
                "limit": _COMPLETED_PAGE,
                "offset": page * _COMPLETED_PAGE,
                "annotate_items": "true",
                "annotate_notes": "true",
            },
        )
        payload = (response.json() or {}) if response is not None else {}
        page_rows = payload.get("items")
        if not isinstance(page_rows, list) or not page_rows:
            break
        rows.extend(row for row in page_rows if isinstance(row, dict))
        if len(page_rows) < _COMPLETED_PAGE:
            break
        if page == _MAX_COMPLETED_PAGES - 1:
            log.info(
                "Todoist adapter: stopping the completed walk at %d pages; "
                "the next sync resumes from the cursor",
                _MAX_COMPLETED_PAGES,
            )
    return rows


async def todoist_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every task the connected Todoist account can list.

    ``checkpoint`` handling is described in the module docstring.
    ``transport`` is a test seam; production leaves it ``None``.
    """
    token = _token()
    resume_after = _resume_marker(checkpoint)
    sync_token = _sync_token_of(checkpoint)
    yielded = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        incremental = bool(sync_token)
        payload = await _sync_payload(client, token, sync_token or "*")
        if payload is None:
            # A stale sync token must degrade to a full read, never fail.
            incremental = False
            payload = await _sync_payload(client, token, "*") or {}

        projects = {
            str(row.get("id") or ""): str(row.get("name") or "")
            for row in payload.get("projects") or []
            if isinstance(row, dict)
        }
        people = {
            str(row.get("id") or ""): str(row.get("full_name") or "")
            for row in payload.get("collaborators") or []
            if isinstance(row, dict)
        }
        notes_by_task: dict[str, list[dict[str, Any]]] = {}
        for note in payload.get("notes") or []:
            if isinstance(note, dict):
                notes_by_task.setdefault(str(note.get("item_id") or ""), []).append(note)

        items: list[RawItem] = []
        seen: set[str] = set()

        def note_task(task: dict[str, Any], extra_notes: list[dict[str, Any]]) -> None:
            task_id = str(task.get("id") or "")
            external_id = f"task:{task_id}"
            if not task_id or external_id in seen:
                return
            project_id = str(task.get("project_id") or "")
            assignee = people.get(str(task.get("responsible_uid") or ""), "")
            comments, last_comment_at = _comment_lines(
                notes_by_task.get(task_id, []) + extra_notes
            )
            item = item_from_task(
                task,
                project_name=projects.get(project_id, ""),
                project_id=project_id,
                assignee=assignee,
                comments=comments,
                last_comment_at=last_comment_at,
            )
            if item is not None:
                seen.add(external_id)
                items.append(item)

        for task in payload.get("items") or []:
            if not isinstance(task, dict) or task.get("is_deleted"):
                continue
            note_task(task, [])

        # An incremental delta already carries completed transitions as items
        # with ``checked``; only a full snapshot needs the history walk.
        if not incremental:
            for row in await _completed_rows(client, token):
                annotated = row.get("item_object")
                task = dict(annotated) if isinstance(annotated, dict) else {
                    "id": str(row.get("task_id") or ""),
                    "content": str(row.get("content") or ""),
                    "project_id": str(row.get("project_id") or ""),
                }
                task.setdefault("checked", True)
                task.setdefault("completed_at", str(row.get("completed_at") or ""))
                extra = [n for n in row.get("notes") or [] if isinstance(n, dict)]
                note_task(task, extra)

        items.sort(key=lambda item: (item.timestamp_utc, item.external_id))
        start = 0
        if resume_after:
            for index, item in enumerate(items):
                if item.external_id == resume_after:
                    start = index + 1
                    break

        for item in items[start:]:
            yielded += 1
            yield item

    log.info(
        "Todoist adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if incremental else "backfill",
    )
