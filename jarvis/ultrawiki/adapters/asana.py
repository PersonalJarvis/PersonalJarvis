"""Asana pull adapter — every task the connected account can list.

Scope, honestly stated. The adapter walks every workspace the marketplace
token reaches, every project in those workspaces (archived projects included —
the listing simply omits the ``archived`` filter), and every task in those
projects, completed ones included. On top of the project walk it lists the
tasks assigned to the connected user per workspace, so personal tasks that
live in no walked project still arrive. Comment stories are fetched per task
and folded into the body chronologically; system activity ("moved to section
X") is excluded because it is bookkeeping, not memory.

What the token structurally cannot yield: subtasks that are neither placed in
a project nor assigned to the user (reaching them needs one extra request per
task, doubling the budget for a corner case), and tasks that sit in no project
and are assigned to someone else (only Asana's paid search API can enumerate
those). Everything else the API can list, this adapter imports.

Cursor convention (the reference adapter's): a numeric checkpoint is the sync
runner's nanosecond cursor and narrows every task listing with
``modified_since`` (minus a five-minute overlap so a boundary is safe;
re-yielded items upsert as ``unchanged``). A ``task:<gid>`` checkpoint is the
bridge's backfill resume marker: tasks stream oldest-to-newest in a
deterministic order, so the walk resumes strictly after that id. Any other
checkpoint is read as "no cursor" and yields the full set. Every item carries
``metadata["mtime_ns"]`` so the runner can advance its cursor.

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
    "AsanaAdapterError",
    "asana_pull_adapter",
    "item_from_task",
]

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:asana"

_API = "https://app.asana.com/api/1.0"

_PAGE_SIZE = 100
#: Safety valve per collection walk; at 100 rows a page this is 20 000 rows.
_MAX_PAGES = 200

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

_TASK_FIELDS = (
    "name,notes,completed,completed_at,created_at,modified_at,"
    "due_on,due_at,assignee.name,created_by.name,permalink_url"
)
_STORY_FIELDS = "created_at,created_by.name,resource_subtype,text"


class AsanaAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace Asana token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("asana")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise AsanaAdapterError(
            "The Asana connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise AsanaAdapterError(
            "Asana is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise AsanaAdapterError(
            "The Asana connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
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
            raise AsanaAdapterError(
                f"Asana was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if response.status_code != 429:
            break
    if response is None:  # pragma: no cover - loop always runs at least once
        raise AsanaAdapterError("Asana answered nothing for this request.")
    if response.status_code == 401:
        raise AsanaAdapterError(
            "Asana rejected the connection. Reconnect it in the Plugins store."
        )
    if response.status_code == 429:
        raise AsanaAdapterError(
            "Asana's rate limit is exhausted. The next sync continues where "
            "this one stopped."
        )
    if response.status_code >= 400:
        raise AsanaAdapterError(
            f"Asana answered HTTP {response.status_code} for this request."
        )
    return response


async def _paged(
    client: httpx.AsyncClient, url: str, token: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every row behind one Asana collection URL, following offset pages."""
    rows: list[dict[str, Any]] = []
    offset = ""
    for page in range(_MAX_PAGES):
        page_params: dict[str, Any] = {**params, "limit": _PAGE_SIZE}
        if offset:
            page_params["offset"] = offset
        response = await _get(client, url, token, params=page_params)
        payload = response.json() or {}
        data = payload.get("data")
        if isinstance(data, list):
            rows.extend(row for row in data if isinstance(row, dict))
        next_page = payload.get("next_page")
        offset = str(next_page.get("offset") or "") if isinstance(next_page, dict) else ""
        if not offset:
            return rows
        if page == _MAX_PAGES - 1:
            log.info(
                "Asana adapter: stopping a walk at %d pages; the next sync "
                "resumes from the cursor",
                _MAX_PAGES,
            )
    return rows


def _since_iso(checkpoint: str | None) -> str:
    """The stored numeric cursor as an ISO instant for ``modified_since``."""
    if not checkpoint:
        return ""
    try:
        ns = int(checkpoint)
    except (TypeError, ValueError):
        return ""
    if ns <= 0:
        return ""
    seconds = ns / 1_000_000_000 - _SINCE_OVERLAP_SECONDS
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resume_marker(checkpoint: str | None) -> str:
    """The checkpoint as a backfill resume id (``task:<gid>``), or empty."""
    if checkpoint and checkpoint.startswith("task:"):
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


def _comment_lines(stories: list[dict[str, Any]]) -> list[str]:
    """Comment stories as chronological body lines; system activity excluded."""
    comments: list[tuple[str, str]] = []
    for story in stories:
        if not isinstance(story, dict):
            continue
        if str(story.get("resource_subtype") or "") != "comment_added":
            continue
        text = str(story.get("text") or "").strip()
        if not text:
            continue
        author = str((story.get("created_by") or {}).get("name") or "")
        created = str(story.get("created_at") or "")
        prefix = " · ".join(part for part in (created, author) if part)
        comments.append((created, f"[{prefix}] {text}" if prefix else text))
    comments.sort(key=lambda pair: pair[0])
    return [line for _created, line in comments]


def item_from_task(
    task: dict[str, Any],
    *,
    thread_key: str,
    context: str,
    comments: list[str],
) -> RawItem | None:
    """One Asana task as a ``RawItem``; ``None`` when it is unusable.

    The body is composed rather than copied raw: a bare notes field loses
    which project the task lives in and whether it was ever finished, and both
    are exactly what a later question turns on.
    """
    gid = str(task.get("gid") or "")
    permalink = str(task.get("permalink_url") or "").strip()
    if not gid or not permalink:
        return None

    title = str(task.get("name") or "").strip() or f"task {gid}"
    state = "completed" if task.get("completed") else "open"
    assignee = str((task.get("assignee") or {}).get("name") or "")
    author = str((task.get("created_by") or {}).get("name") or "")
    due = str(task.get("due_at") or task.get("due_on") or "")
    created = str(task.get("created_at") or "")
    modified = str(task.get("modified_at") or "")

    header_parts = [part for part in (context, "task", state) if part]
    if assignee:
        header_parts.append(f"assigned to {assignee}")
    if due:
        header_parts.append(f"due {due}")
    if author:
        header_parts.append(f"created by {author}")
    header = " · ".join(header_parts)

    body_text = str(task.get("notes") or "").strip()
    if comments:
        body_text += ("\n\n" if body_text else "") + "Comments:\n" + "\n".join(comments)

    return RawItem(
        external_id=f"task:{gid}",
        title=title,
        body=_cap(f"{header}\n\n{body_text}".strip()),
        permalink=permalink,
        # WHEN IT HAPPENED, not when it was last touched — the memory is about
        # the event. The modification time rides in metadata for the cursor.
        timestamp_utc=created or modified,
        thread_key=thread_key,
        author_raw=author,
        metadata={
            "mtime_ns": _to_ns(modified or created),
            "state": state,
            "assignee": assignee,
            "due": due,
            "comments": len(comments),
            "updated_at": modified,
        },
    )


async def asana_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every task the connected Asana account can list.

    ``checkpoint`` doubles as the incremental cursor (see the module
    docstring). ``transport`` is a test seam; production leaves it ``None``.
    """
    token = _token()
    since = _since_iso(checkpoint)
    resume_after = _resume_marker(checkpoint)
    yielded = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        # (created_at, gid, task, thread_key, context) — sorted below, so the
        # stream is deterministic and a backfill resume can pick up mid-walk.
        stubs: list[tuple[str, str, dict[str, Any], str, str]] = []
        seen: set[str] = set()

        def note(task: dict[str, Any], thread_key: str, context: str) -> None:
            gid = str(task.get("gid") or "")
            if not gid or gid in seen:
                return
            seen.add(gid)
            stubs.append(
                (str(task.get("created_at") or ""), gid, task, thread_key, context)
            )

        workspaces = await _paged(client, f"{_API}/workspaces", token, {})
        for workspace in sorted(workspaces, key=lambda ws: str(ws.get("gid") or "")):
            ws_gid = str(workspace.get("gid") or "")
            ws_name = str(workspace.get("name") or "")
            if not ws_gid:
                continue
            projects = await _paged(
                client,
                f"{_API}/workspaces/{ws_gid}/projects",
                token,
                {"opt_fields": "name"},
            )
            for project in sorted(projects, key=lambda p: str(p.get("gid") or "")):
                p_gid = str(project.get("gid") or "")
                p_name = str(project.get("name") or "")
                if not p_gid:
                    continue
                params: dict[str, Any] = {"project": p_gid, "opt_fields": _TASK_FIELDS}
                if since:
                    params["modified_since"] = since
                for task in await _paged(client, f"{_API}/tasks", token, params):
                    note(task, p_gid, p_name)
            # Personal tasks living in no walked project still belong to the
            # memory; the assignee listing is the one other door the API opens.
            params = {"assignee": "me", "workspace": ws_gid, "opt_fields": _TASK_FIELDS}
            if since:
                params["modified_since"] = since
            for task in await _paged(client, f"{_API}/tasks", token, params):
                note(task, ws_gid, ws_name)

        stubs.sort(key=lambda stub: (stub[0], stub[1]))
        start = 0
        if resume_after:
            for index, stub in enumerate(stubs):
                if f"task:{stub[1]}" == resume_after:
                    start = index + 1
                    break

        for _created, gid, task, thread_key, context in stubs[start:]:
            stories = await _paged(
                client,
                f"{_API}/tasks/{gid}/stories",
                token,
                {"opt_fields": _STORY_FIELDS},
            )
            item = item_from_task(
                task,
                thread_key=thread_key,
                context=context,
                comments=_comment_lines(stories),
            )
            if item is not None:
                yielded += 1
                yield item

    log.info(
        "Asana adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if since else "backfill",
    )
