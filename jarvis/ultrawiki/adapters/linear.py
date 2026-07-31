"""Linear pull adapter — every issue the connected account can query.

Scope, honestly stated. One paginated GraphQL query walks the workspace's
issues with ``includeArchived: true``, so open, completed, canceled AND
archived issues all arrive — Linear's API puts no per-team fences around a
token, so this really is everything the account can see. Each issue carries
its first 50 comments inline in the same query (no per-issue request); an
issue with more than 50 comments gets an explicit marker line instead of a
silent cut, because the record must say what it does not contain.

Structural limits: Linear documents, project updates and attachments are not
issues and are not imported — this reader is about the task stream, and the
issue body plus its comments is where the decisions live. Comment threads
beyond the first 50 per issue stay behind the permalink, as marked.

Cursor convention (the reference adapter's): a numeric checkpoint is the sync
runner's nanosecond cursor and becomes an ``updatedAt >= <instant>`` filter
(minus a five-minute overlap so a boundary is safe; re-yielded items upsert
as ``unchanged``). An ``issue:<id>`` checkpoint is the bridge's backfill
resume marker: issues stream oldest-to-newest in a deterministic order, so
the walk resumes strictly after that id. Any other checkpoint is read as "no
cursor" and yields the full set. Every item carries ``metadata["mtime_ns"]``
so the runner can advance its cursor.

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
    "LinearAdapterError",
    "item_from_issue",
    "linear_pull_adapter",
]

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:linear"

_API = "https://api.linear.app/graphql"

#: Modest page size: each issue carries a nested comment page, and Linear
#: prices queries by complexity, not by row count.
_PAGE_SIZE = 50
_MAX_PAGES = 200
_COMMENT_PAGE = 50

_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)

_MAX_ATTEMPTS = 3
_RETRY_WAIT_CAP = 30.0
_DEFAULT_RETRY_WAIT = 2.0

_SINCE_OVERLAP_SECONDS = 300

_MAX_BODY_CHARS = 1_000_000
_TRUNCATION_NOTE = "\n\n[truncated — the full text lives at the link above]"

_MORE_COMMENTS_NOTE = "[more comments on Linear — open the link to read them]"

_ISSUES_QUERY = f"""
query UltraWikiIssues($first: Int!, $after: String, $filter: IssueFilter) {{
  issues(first: $first, after: $after, filter: $filter, includeArchived: true) {{
    nodes {{
      id
      identifier
      title
      description
      url
      createdAt
      updatedAt
      archivedAt
      dueDate
      state {{ name }}
      assignee {{ name }}
      creator {{ name }}
      team {{ id key name }}
      project {{ name }}
      comments(first: {_COMMENT_PAGE}) {{
        nodes {{
          body
          createdAt
          user {{ name }}
        }}
        pageInfo {{ hasNextPage }}
      }}
    }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""


class LinearAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace Linear token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("linear")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise LinearAdapterError(
            "The Linear connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise LinearAdapterError(
            "Linear is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise LinearAdapterError(
            "The Linear connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
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


async def _query(
    client: httpx.AsyncClient, token: str, variables: dict[str, Any]
) -> dict[str, Any]:
    """One GraphQL round trip, every failure mapped to an actionable sentence."""
    response: httpx.Response | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_retry_wait(response))
        try:
            response = await client.post(
                _API,
                headers=_headers(token),
                json={"query": _ISSUES_QUERY, "variables": variables},
            )
        except httpx.HTTPError as exc:
            raise LinearAdapterError(
                f"Linear was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if response.status_code != 429:
            break
    if response is None:  # pragma: no cover - loop always runs at least once
        raise LinearAdapterError("Linear answered nothing for this request.")
    if response.status_code == 401:
        raise LinearAdapterError(
            "Linear rejected the connection. Reconnect it in the Plugins store."
        )
    if response.status_code == 429:
        raise LinearAdapterError(
            "Linear's rate limit is exhausted. The next sync continues where "
            "this one stopped."
        )
    if response.status_code >= 400:
        raise LinearAdapterError(
            f"Linear answered HTTP {response.status_code} for this request."
        )
    payload = response.json() or {}
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        codes = {
            str(((error or {}).get("extensions") or {}).get("code") or "")
            for error in errors
            if isinstance(error, dict)
        }
        if "AUTHENTICATION_ERROR" in codes:
            raise LinearAdapterError(
                "Linear rejected the connection. Reconnect it in the Plugins store."
            )
        if "RATELIMITED" in codes:
            raise LinearAdapterError(
                "Linear's rate limit is exhausted. The next sync continues "
                "where this one stopped."
            )
        first = next(
            (
                str(error.get("message") or "")
                for error in errors
                if isinstance(error, dict) and error.get("message")
            ),
            "unspecified error",
        )
        raise LinearAdapterError(f"Linear reported an error for this request: {first}")
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _since_iso(checkpoint: str | None) -> str:
    """The stored numeric cursor as an ISO instant for the updatedAt filter."""
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
    """The checkpoint as a backfill resume id (``issue:<id>``), or empty."""
    if checkpoint and checkpoint.startswith("issue:"):
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


def _comment_lines(comments: Any) -> tuple[list[str], bool]:
    """Chronological comment lines + whether more exist beyond the page."""
    nodes = (comments or {}).get("nodes") if isinstance(comments, dict) else None
    page_info = (comments or {}).get("pageInfo") if isinstance(comments, dict) else None
    more = bool(isinstance(page_info, dict) and page_info.get("hasNextPage"))
    rows: list[tuple[str, str]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        body = str(node.get("body") or "").strip()
        if not body:
            continue
        author = str((node.get("user") or {}).get("name") or "")
        created = str(node.get("createdAt") or "")
        prefix = " · ".join(part for part in (created, author) if part)
        rows.append((created, f"[{prefix}] {body}" if prefix else body))
    rows.sort(key=lambda pair: pair[0])
    return [line for _created, line in rows], more


def item_from_issue(issue: dict[str, Any]) -> RawItem | None:
    """One Linear issue as a ``RawItem``; ``None`` when it is unusable."""
    issue_id = str(issue.get("id") or "")
    permalink = str(issue.get("url") or "").strip()
    if not issue_id or not permalink:
        return None

    identifier = str(issue.get("identifier") or "") or issue_id
    title = str(issue.get("title") or "").strip() or f"issue {identifier}"
    state = str((issue.get("state") or {}).get("name") or "")
    archived = bool(issue.get("archivedAt"))
    assignee = str((issue.get("assignee") or {}).get("name") or "")
    author = str((issue.get("creator") or {}).get("name") or "")
    due = str(issue.get("dueDate") or "")
    created = str(issue.get("createdAt") or "")
    updated = str(issue.get("updatedAt") or "")
    team = issue.get("team") or {}
    team_id = str(team.get("id") or "")
    team_name = str(team.get("name") or team.get("key") or "")
    project = str((issue.get("project") or {}).get("name") or "")

    header_parts = [part for part in (team_name, f"issue {identifier}", state) if part]
    if archived:
        header_parts.append("archived")
    if project:
        header_parts.append(f"project {project}")
    if assignee:
        header_parts.append(f"assigned to {assignee}")
    if due:
        header_parts.append(f"due {due}")
    if author:
        header_parts.append(f"created by {author}")
    header = " · ".join(header_parts)

    comments, more = _comment_lines(issue.get("comments"))
    body_text = str(issue.get("description") or "").strip()
    if comments:
        body_text += ("\n\n" if body_text else "") + "Comments:\n" + "\n".join(comments)
    if more:
        body_text += ("\n\n" if body_text else "") + _MORE_COMMENTS_NOTE

    return RawItem(
        external_id=f"issue:{issue_id}",
        title=title,
        body=_cap(f"{header}\n\n{body_text}".strip()),
        permalink=permalink,
        # WHEN IT HAPPENED, not when it was last touched — the memory is about
        # the event. The update time rides in metadata to drive the cursor.
        timestamp_utc=created or updated,
        thread_key=team_id,
        author_raw=author,
        metadata={
            "mtime_ns": _to_ns(updated or created),
            "identifier": identifier,
            "state": state,
            "archived": archived,
            "assignee": assignee,
            "due": due,
            "comments": len(comments),
            "updated_at": updated,
        },
    )


async def linear_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every issue the connected Linear account can query.

    ``checkpoint`` doubles as the incremental cursor (see the module
    docstring). ``transport`` is a test seam; production leaves it ``None``.
    """
    token = _token()
    since = _since_iso(checkpoint)
    resume_after = _resume_marker(checkpoint)
    yielded = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        nodes: list[dict[str, Any]] = []
        after: str | None = None
        for page in range(_MAX_PAGES):
            variables: dict[str, Any] = {"first": _PAGE_SIZE, "after": after}
            if since:
                variables["filter"] = {"updatedAt": {"gte": since}}
            data = await _query(client, token, variables)
            connection = data.get("issues") or {}
            rows = connection.get("nodes")
            if isinstance(rows, list):
                nodes.extend(row for row in rows if isinstance(row, dict))
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = str(page_info.get("endCursor") or "") or None
            if after is None:
                break
            if page == _MAX_PAGES - 1:
                log.info(
                    "Linear adapter: stopping at %d pages; the next sync "
                    "resumes from the cursor",
                    _MAX_PAGES,
                )

        # Deterministic oldest-to-newest order, so the bridge's backfill
        # checkpoint (last flushed external_id) resumes strictly after it.
        seen: set[str] = set()
        ordered: list[dict[str, Any]] = []
        for node in sorted(
            nodes,
            key=lambda n: (str(n.get("createdAt") or ""), str(n.get("id") or "")),
        ):
            node_id = str(node.get("id") or "")
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            ordered.append(node)

        start = 0
        if resume_after:
            for index, node in enumerate(ordered):
                if f"issue:{node.get('id')}" == resume_after:
                    start = index + 1
                    break

        for node in ordered[start:]:
            item = item_from_issue(node)
            if item is not None:
                yielded += 1
                yield item

    log.info(
        "Linear adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if since else "backfill",
    )
