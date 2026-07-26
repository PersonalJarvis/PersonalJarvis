"""GitHub pull adapter — the issues and pull requests you were part of.

The first real reader behind the plugin bridge. Of everything GitHub holds,
this pulls the part that belongs in a *memory*:

1. the issues and pull requests the account is **involved in** — authored,
   assigned, mentioned, or commented on — **with their comment threads**,
   because the decision usually lives in the discussion rather than in the
   opening post;
2. one **profile per repository** — description, topics, primary language and
   README — which is what makes "which project was that again?" answerable.

**Nothing is cloned.** No repository is downloaded, no file tree walked, no
commit history read. That is a decision, not a gap: the repositories reachable
from one personal account here total roughly 900 MB of git data, and a
lockfile, a minified bundle or a vendored dependency answers no question a
person asks their own memory. Source code is a code search's job — Jarvis has
CLI tools for that. A memory needs what was decided and why.

One search query does all of it (``involves:<login>``), which matters: the
Search API allows 30 requests a minute, an order of magnitude tighter than the
5 000/hour core bucket, and walking every repository's issue list would burn
that budget on a personal account within seconds.

Re-syncs are cheap by construction, so the bridge's current
``IncrementalMode.NONE`` (every run is a full backfill) costs one or two
requests on a personal account, and every unchanged item upserts as
``unchanged`` on its content hash — no duplicates, no wasted processing.

The adapter is nevertheless already cursor-aware: hand it a numeric checkpoint
and the query gains ``updated:>=<date>``. It rides on ``metadata["mtime_ns"]``,
the convention the sync runner already advances (the file connectors use the
same key for mtimes), so switching the bridge to incremental later needs no
change here. A non-numeric checkpoint — the bridge passes the last item's id
during a backfill — is read as "no cursor" and yields the full set.

Credentials come from the marketplace token the user connected in the Plugins
store — this adapter never asks for a second one and never logs the token.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from jarvis.ultrawiki.types import ConnectorContext, RawItem

log = logging.getLogger(__name__)

__all__ = [
    "INTEGRATION_ID",
    "GitHubAdapterError",
    "github_pull_adapter",
    "item_from_issue",
    "item_from_repo",
]

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:github"

_API = "https://api.github.com"

#: Search results are capped at 1 000 per query by GitHub, i.e. 10 full pages.
#: Asking beyond that returns 422, so the walk stops there by construction.
_PER_PAGE = 100
_MAX_PAGES = 10

#: Generous on read: a cold search over a large account is not instant, but a
#: settings screen must not hang on it either.
_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)

#: Below this the adapter stops early and lets the next sync continue from the
#: cursor, rather than spending the user's last requests and failing mid-page.
_RATELIMIT_FLOOR = 3

#: Comment threads cost one request per item that HAS comments. The core
#: bucket allows 5 000 an hour, so this bound keeps ONE sync predictable
#: rather than protecting the quota: past it the remaining threads stay as the
#: "open the link" note they were before.
_MAX_COMMENT_FETCHES = 300

#: Per item — a 400-comment thread is a mailing list, not a decision record.
_MAX_COMMENTS_PER_ITEM = 30

#: A README is orientation, not documentation to be reproduced in full.
_README_MAX_CHARS = 8000

#: Repository pages of 100; four pages covers 400 repositories.
_MAX_REPO_PAGES = 4


class GitHubAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace GitHub token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("github")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise GitHubAdapterError(
            "The GitHub connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise GitHubAdapterError(
            "GitHub is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise GitHubAdapterError(
            "The GitHub connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get(
    client: httpx.AsyncClient, url: str, token: str, params: dict[str, Any] | None = None
) -> httpx.Response:
    """One GET, with every failure mapped to a sentence a user can act on."""
    try:
        response = await client.get(url, headers=_headers(token), params=params)
    except httpx.HTTPError as exc:
        raise GitHubAdapterError(
            f"GitHub was unreachable ({type(exc).__name__}). Check this "
            "machine's internet connection."
        ) from exc
    if response.status_code == 401:
        raise GitHubAdapterError(
            "GitHub rejected the connection. Reconnect it in the Plugins store."
        )
    if response.status_code == 403 and "rate limit" in response.text.lower():
        raise GitHubAdapterError(
            "GitHub's rate limit is exhausted. The next sync continues where "
            "this one stopped."
        )
    if response.status_code >= 400:
        raise GitHubAdapterError(
            f"GitHub answered HTTP {response.status_code} for this request."
        )
    return response


async def _login(client: httpx.AsyncClient, token: str) -> str:
    """Whose involvement to search for.

    Resolved from the token rather than configured: the account that connected
    the plugin IS the account whose memory this is, and asking the user to
    retype their own handle is a chance to get it wrong.
    """
    response = await _get(client, f"{_API}/user", token)
    login = str((response.json() or {}).get("login") or "").strip()
    if not login:
        raise GitHubAdapterError("GitHub did not report which account is connected.")
    return login


def _cursor_to_date(checkpoint: str | None) -> str:
    """The stored numeric cursor as a GitHub search date. Empty when unset.

    The cursor is nanoseconds since the epoch (the sync runner's convention).
    A day is subtracted before querying: GitHub's ``updated:>=`` works on whole
    days, so re-asking from the previous day is what guarantees nothing is
    skipped at a boundary. Re-yielded items upsert as ``unchanged``.
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
    day = moment.toordinal() - 1
    return datetime.fromordinal(day).strftime("%Y-%m-%d")


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


def _repo_of(issue: dict[str, Any]) -> str:
    """``owner/repo`` from the search result's repository_url."""
    url = str(issue.get("repository_url") or "")
    parts = url.rsplit("/", 2)
    return "/".join(parts[-2:]) if len(parts) >= 3 else ""


def item_from_issue(
    issue: dict[str, Any], comment_text: str = ""
) -> RawItem | None:
    """One search hit as a ``RawItem``; ``None`` when it is unusable.

    The body is composed rather than copied raw: a bare issue body loses which
    repository it belongs to and whether it was ever resolved, and both are
    exactly what a later question ("what did we decide about X?") turns on.
    """
    permalink = str(issue.get("html_url") or "").strip()
    number = issue.get("number")
    if not permalink or number is None:
        return None

    repo = _repo_of(issue)
    is_pr = bool(issue.get("pull_request"))
    kind = "pull request" if is_pr else "issue"
    state = str(issue.get("state") or "")
    # A merged PR and a closed-unmerged one are opposite outcomes; "closed"
    # alone would record them as the same thing.
    if is_pr and state == "closed":
        merged = (issue.get("pull_request") or {}).get("merged_at")
        state = "merged" if merged else "closed without merging"

    title = str(issue.get("title") or "").strip() or f"{kind} #{number}"
    author = str((issue.get("user") or {}).get("login") or "")
    labels = [
        str(label.get("name") or "")
        for label in (issue.get("labels") or [])
        if isinstance(label, dict) and label.get("name")
    ]
    created = str(issue.get("created_at") or "")
    updated = str(issue.get("updated_at") or "")

    header = f"{repo} · {kind} #{number} · {state}" if repo else f"{kind} #{number}"
    if labels:
        header += f" · {', '.join(labels)}"
    if author:
        header += f" · opened by {author}"
    body_text = str(issue.get("body") or "").strip()
    comments = int(issue.get("comments") or 0)
    if comment_text:
        # The discussion IS the record: the opening post says what was
        # proposed, the thread says what was decided and why it changed.
        body_text += f"\n\n--- discussion ---\n{comment_text}"
    elif comments:
        # Not fetched this run (the per-sync bound). Say so, rather than
        # let the body imply the opening post was the whole conversation.
        body_text += f"\n\n[{comments} comment(s) on GitHub — open the link to read them]"

    return RawItem(
        external_id=f"issue:{issue.get('id')}",
        title=title,
        body=f"{header}\n\n{body_text}".strip(),
        permalink=permalink,
        # WHEN IT HAPPENED, not when it was last touched — the memory is about
        # the event. The update time rides in metadata to drive the cursor.
        timestamp_utc=created or updated,
        thread_key=f"{repo}#{number}" if repo else str(number),
        author_raw=author,
        metadata={
            "mtime_ns": _to_ns(updated or created),
            "repo": repo,
            "kind": "pull_request" if is_pr else "issue",
            "state": state,
            "labels": labels,
            "comments": comments,
            "comments_fetched": bool(comment_text),
            "updated_at": updated,
        },
    )


async def _comment_text(
    client: httpx.AsyncClient, token: str, issue: dict[str, Any]
) -> str:
    """The discussion under one issue or pull request, as plain text.

    Returns an empty string on ANY failure: a thread that cannot be read must
    not lose the item it belongs to. The caller then falls back to the "open
    the link" note, which is what the record said before comments existed.
    """
    url = str(issue.get("comments_url") or "").strip()
    if not url:
        return ""
    try:
        response = await _get(
            client, url, token, params={"per_page": _MAX_COMMENTS_PER_ITEM}
        )
        rows = response.json()
    except (GitHubAdapterError, ValueError):
        log.debug("comment thread unavailable for %s", url, exc_info=True)
        return ""
    if not isinstance(rows, list):
        return ""
    lines: list[str] = []
    for row in rows[:_MAX_COMMENTS_PER_ITEM]:
        if not isinstance(row, dict):
            continue
        author = str((row.get("user") or {}).get("login") or "someone")
        when = str(row.get("created_at") or "")[:10]
        text = str(row.get("body") or "").strip()
        if not text:
            continue
        lines.append(f"{author} ({when}): {text}")
    total = int(issue.get("comments") or 0)
    if total > len(lines) and total > _MAX_COMMENTS_PER_ITEM:
        lines.append(f"[... {total - len(lines)} further comment(s) on GitHub]")
    return "\n\n".join(lines)


def item_from_repo(repo: dict[str, Any], readme: str = "") -> RawItem | None:
    """One repository as a PROFILE item — what the project is, not what it contains.

    This is the answer to "which project was that again?", and it is the only
    repository-level thing worth storing: a name, what it is for, what it is
    written in, and the README's own explanation. Deliberately NOT the code —
    see the module docstring.
    """
    full_name = str(repo.get("full_name") or "").strip()
    permalink = str(repo.get("html_url") or "").strip()
    if not full_name or not permalink:
        return None

    description = str(repo.get("description") or "").strip()
    language = str(repo.get("language") or "").strip()
    topics = [str(topic) for topic in (repo.get("topics") or []) if topic]
    visibility = "private" if repo.get("private") else "public"
    pushed = str(repo.get("pushed_at") or "")
    created = str(repo.get("created_at") or "")

    header = f"{full_name} · repository · {visibility}"
    if language:
        header += f" · {language}"
    if topics:
        header += f" · {', '.join(topics)}"
    parts = [header]
    if description:
        parts.append(description)
    if readme:
        parts.append(readme[:_README_MAX_CHARS])
        if len(readme) > _README_MAX_CHARS:
            parts.append("[README truncated — open the link for the rest]")

    return RawItem(
        external_id=f"repo:{repo.get('id')}",
        title=full_name,
        body="\n\n".join(parts).strip(),
        permalink=permalink,
        # A repository's "event time" is when it was created; the last push
        # drives the cursor instead, via metadata.
        timestamp_utc=created or pushed,
        thread_key=full_name,
        author_raw=str((repo.get("owner") or {}).get("login") or ""),
        metadata={
            "mtime_ns": _to_ns(pushed or created),
            "repo": full_name,
            "kind": "repository",
            "state": visibility,
            "language": language,
            "topics": topics,
            "pushed_at": pushed,
        },
    )


async def _readme_text(client: httpx.AsyncClient, token: str, full_name: str) -> str:
    """A repository's README as plain text, or empty when it has none.

    Asked for as raw text rather than the base64 JSON blob: the raw media type
    is one request either way and needs no decoding step to get wrong.
    """
    try:
        response = await client.get(
            f"{_API}/repos/{full_name}/readme",
            headers={**_headers(token), "Accept": "application/vnd.github.raw"},
        )
    except httpx.HTTPError:
        return ""
    if response.status_code != 200:
        # 404 simply means the repository has no README — a normal state.
        return ""
    return response.text.strip()


async def _repository_items(
    client: httpx.AsyncClient, token: str
) -> AsyncIterator[RawItem]:
    """A profile item for every repository the account can reach."""
    for page in range(1, _MAX_REPO_PAGES + 1):
        response = await _get(
            client,
            f"{_API}/user/repos",
            token,
            params={
                "per_page": _PER_PAGE,
                "page": page,
                "affiliation": "owner,collaborator,organization_member",
                "sort": "pushed",
            },
        )
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            full_name = str(row.get("full_name") or "")
            readme = await _readme_text(client, token, full_name) if full_name else ""
            item = item_from_repo(row, readme)
            if item is not None:
                yield item
        if len(rows) < _PER_PAGE:
            break


async def github_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield the issues and pull requests this account is involved in.

    ``checkpoint`` doubles as the incremental cursor: present means "only what
    changed since", absent means a full backfill. ``transport`` is a test seam;
    production leaves it ``None``.
    """
    token = _token()
    since = _cursor_to_date(checkpoint)
    yielded = 0
    comment_fetches = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        login = await _login(client, token)
        query = f"involves:{login}"
        if since:
            query += f" updated:>={since}"

        for page in range(1, _MAX_PAGES + 1):
            response = await _get(
                client,
                f"{_API}/search/issues",
                token,
                params={
                    "q": query,
                    "sort": "updated",
                    "order": "asc",
                    "per_page": _PER_PAGE,
                    "page": page,
                },
            )
            payload = response.json() or {}
            rows = payload.get("items")
            if not isinstance(rows, list) or not rows:
                break

            for row in rows:
                if not isinstance(row, dict):
                    continue
                # The thread is fetched only when there IS one, and only while
                # the per-sync budget lasts — past it the item still lands,
                # carrying its honest "open the link" note.
                thread = ""
                if int(row.get("comments") or 0) and comment_fetches < _MAX_COMMENT_FETCHES:
                    thread = await _comment_text(client, token, row)
                    comment_fetches += 1
                item = item_from_issue(row, thread)
                if item is not None:
                    yielded += 1
                    yield item

            if len(rows) < _PER_PAGE:
                break
            # Stop one step short of the wall instead of failing mid-page: the
            # cursor is already checkpointed, so the next run resumes cleanly.
            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining is not None and remaining.isdigit():
                if int(remaining) <= _RATELIMIT_FLOOR:
                    log.info(
                        "GitHub adapter: stopping at page %d — rate budget "
                        "nearly spent; the next sync resumes from the cursor",
                        page,
                    )
                    break

        # Repository profiles last: they are the smallest and least urgent
        # part of the read, so a rate-limit stop above costs the cheap thing
        # rather than the discussions.
        try:
            async for repo_item in _repository_items(client, token):
                yielded += 1
                yield repo_item
        except GitHubAdapterError as exc:
            # A failed repository walk must not discard the issues already
            # yielded — they are the valuable half.
            log.info("GitHub adapter: repository profiles skipped (%s)", exc)

    log.info(
        "GitHub adapter: yielded %d item(s) for %s (%s), %d thread(s) fetched",
        yielded,
        ctx.source_id,
        "incremental" if since else "backfill",
        comment_fetches,
    )
