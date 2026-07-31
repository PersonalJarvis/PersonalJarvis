"""Slack pull adapter — every conversation the connected token can see.

Of everything Slack holds, this pulls what belongs in a *memory*: the words.
It walks ``conversations.list`` across all four conversation types (public
channels, private channels, group DMs, DMs), reads each conversation's FULL
history oldest-to-newest (``conversations.history``, cursor paging), and
expands every thread through ``conversations.replies`` — a thread is
remembered as one coherent exchange, not a scatter of orphaned lines.

Item shape: ONE item per thread (parent plus replies as chronological
``author: text`` lines) and ONE item per calendar day of non-threaded
messages in a conversation. That keeps ids stable across re-runs — a re-sync
upserts the same rows instead of duplicating them — and keeps single Slack
messages from becoming thousands of one-line memory entries.

HONEST SCOPE — what a token structurally cannot yield:

* Visibility is the TOKEN's, not the workspace's. A user token sees what that
  user sees; a bot token sees only conversations the app was invited into.
  Public channels the token is not a member of appear in the listing but
  answer ``not_in_channel`` on history — those are skipped with a log line,
  never faked.
* A conversation *type* the token has no scope for (``missing_scope``)
  degrades per type: the other types still import fully. The same applies to
  ``users.list`` — without it, raw user ids appear instead of display names.
* Files and images are imported as ``[file: <name>]`` markers, never as
  binary content — text is the memory, blobs are not.
* On an incremental (cursor) run, a NEW reply to a thread that started before
  the cursor is only caught when the reply was "also sent to the channel"; a
  plain reply to an old thread surfaces on the next FULL backfill. (The
  bridge currently re-runs full backfills, so nothing is missed today.)

Permalinks are CONSTRUCTED from the workspace URL (``auth.test``) in the
canonical ``/archives/<channel>/p<ts>`` shape that ``chat.getPermalink``
returns — calling that API per item would cost one extra request per
imported item and burn the rate budget for zero new information.

Rate limits: Slack answers HTTP 429 with ``Retry-After``; every request
honours it with bounded waits (at most four attempts, at most 30 s each)
before failing honestly.

The incremental cursor rides on ``metadata["mtime_ns"]`` — the key the sync
runner already advances — carrying each item's newest message time. A numeric
checkpoint narrows history to the day before the cursor's day (a whole-day
rewind keeps day-chunk ids complete rather than half-rebuilt at a boundary);
a non-numeric checkpoint — the bridge passes the last item's id during a
backfill — is read as "no cursor" and re-yields the full set, which upserts
as ``unchanged``.

Credentials come from the marketplace token the user connected in the
Plugins store — this adapter never asks for a second one and never logs the
token.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import httpx

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
    "SlackAdapterError",
    "slack_pull_adapter",
]

#: The plugin-bridge candidate id this adapter serves (catalog id ``slack``).
INTEGRATION_ID = "plugin:slack"

_API = "https://slack.com/api"

#: Fixed walk order across conversation types — determinism, not preference.
_CONVERSATION_TYPES = ("public_channel", "private_channel", "mpim", "im")

#: Slack's recommended page size for the conversations/users families.
_PAGE_LIMIT = 200
_MAX_LIST_PAGES = 50  # 10 000 conversations or users per listing walk
_MAX_HISTORY_PAGES = 2000  # 400 000 messages per conversation
_MAX_THREAD_PAGES = 200  # 40 000 replies per thread

#: Bounded 429 handling: honour Retry-After, but never wait unboundedly.
_MAX_ATTEMPTS = 4
_MAX_RETRY_WAIT = 30.0

#: Cap any single item body; a marker says the record is incomplete.
_MAX_BODY_BYTES = 1_000_000
_TRUNCATION_MARKER = "\n\n[truncated — this item exceeded the 1 MB import cap]"

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

#: ``ok: false`` errors that mean the CREDENTIAL is dead, not the request.
_AUTH_ERRORS = frozenset(
    {"invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired"}
)

#: Join/leave/archive housekeeping is not memory; topic and purpose changes
#: carry real text and are kept.
_NOISE_SUBTYPES = frozenset(
    {
        "channel_join",
        "channel_leave",
        "group_join",
        "group_leave",
        "channel_archive",
        "channel_unarchive",
        "bot_add",
        "bot_remove",
    }
)

_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL_REF_RE = re.compile(r"<#[A-Z0-9]+\|([^>]+)>")
_LINK_RE = re.compile(r"<(https?://[^>|]+)(?:\|([^>]*))?>")


class SlackAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace Slack token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("slack")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise SlackAdapterError(
            "The Slack connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise SlackAdapterError(
            "Slack is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise SlackAdapterError(
            "The Slack connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _call(
    client: httpx.AsyncClient,
    method: str,
    token: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One Web API call with bounded 429 retries and honest failure mapping.

    Returns the parsed payload — including ``ok: false`` payloads, which each
    caller handles per its own degradation rule. Only transport failures, HTTP
    errors, exhausted rate budgets, and dead-credential errors raise.
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = await client.get(
                f"{_API}/{method}", headers=_headers(token), params=params
            )
        except httpx.HTTPError as exc:
            raise SlackAdapterError(
                f"Slack was unreachable ({type(exc).__name__}). Check this "
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
        if response.status_code >= 400:
            raise SlackAdapterError(
                f"Slack answered HTTP {response.status_code} for {method}."
            )
        payload: dict[str, Any] = response.json() or {}
        if not payload.get("ok") and str(payload.get("error") or "") in _AUTH_ERRORS:
            raise SlackAdapterError(
                "Slack rejected the connection. Reconnect it in the Plugins store."
            )
        return payload
    raise SlackAdapterError(
        "Slack's rate limit persisted after several bounded waits. "
        "The next sync continues where this one stopped."
    )


def _next_cursor(payload: dict[str, Any]) -> str:
    metadata = payload.get("response_metadata") or {}
    return str(metadata.get("next_cursor") or "").strip() if isinstance(metadata, dict) else ""


async def _team_url(client: httpx.AsyncClient, token: str) -> str:
    """The workspace's base URL (permalink prefix); also validates the token."""
    payload = await _call(client, "auth.test", token)
    if not payload.get("ok"):
        raise SlackAdapterError(
            "Slack could not confirm the connection "
            f"({payload.get('error') or 'unknown error'})."
        )
    # slack.com/archives/... redirects a signed-in user to the right
    # workspace, so a missing url degrades to a still-working link.
    return str(payload.get("url") or "").strip() or "https://slack.com/"


async def _display_names(client: httpx.AsyncClient, token: str) -> dict[str, str]:
    """User id → display name, cached for the whole run. Empty on missing scope."""
    names: dict[str, str] = {}
    cursor = ""
    for _page in range(_MAX_LIST_PAGES):
        params: dict[str, Any] = {"limit": _PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        payload = await _call(client, "users.list", token, params)
        if not payload.get("ok"):
            log.info(
                "Slack adapter: user names are unavailable (%s); raw user ids "
                "will appear instead",
                payload.get("error") or "unknown error",
            )
            return names
        for member in payload.get("members") or []:
            if not isinstance(member, dict):
                continue
            user_id = str(member.get("id") or "")
            profile = member.get("profile") if isinstance(member.get("profile"), dict) else {}
            name = str(
                profile.get("display_name")
                or profile.get("real_name")
                or member.get("real_name")
                or member.get("name")
                or ""
            ).strip()
            if user_id and name:
                names[user_id] = name
        cursor = _next_cursor(payload)
        if not cursor:
            break
    return names


async def _conversations(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    """Every conversation the token can list, in a deterministic order.

    Each type is requested separately so a scope the token lacks removes ONE
    type, never the whole import. Archived conversations are included — the
    words in them are still the user's history.
    """
    found: dict[str, dict[str, Any]] = {}
    for conv_type in _CONVERSATION_TYPES:
        cursor = ""
        for _page in range(_MAX_LIST_PAGES):
            params: dict[str, Any] = {"types": conv_type, "limit": _PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            payload = await _call(client, "conversations.list", token, params)
            if not payload.get("ok"):
                error = str(payload.get("error") or "unknown error")
                if error == "missing_scope":
                    log.info(
                        "Slack adapter: the token has no scope to list %s "
                        "conversations; the other types still import",
                        conv_type,
                    )
                else:
                    log.warning(
                        "Slack adapter: listing %s conversations failed (%s)",
                        conv_type,
                        error,
                    )
                break
            for channel in payload.get("channels") or []:
                if isinstance(channel, dict) and channel.get("id"):
                    found[str(channel["id"])] = channel
            cursor = _next_cursor(payload)
            if not cursor:
                break
        else:
            log.warning(
                "Slack adapter: %s listing exceeded the page cap; importing "
                "what was found",
                conv_type,
            )
    rows = list(found.values())
    rows.sort(key=lambda c: (float(c.get("created") or 0), str(c.get("id") or "")))
    return rows


def _ts_sort_key(message: dict[str, Any]) -> float:
    try:
        return float(str(message.get("ts") or "0"))
    except ValueError:
        return 0.0


async def _history(
    client: httpx.AsyncClient, token: str, channel_id: str, oldest: str
) -> list[dict[str, Any]] | None:
    """A conversation's full history, ascending. ``None`` = honestly skipped."""
    messages: list[dict[str, Any]] = []
    cursor = ""
    for _page in range(_MAX_HISTORY_PAGES):
        params: dict[str, Any] = {"channel": channel_id, "limit": _PAGE_LIMIT}
        if oldest:
            params["oldest"] = oldest
        if cursor:
            params["cursor"] = cursor
        payload = await _call(client, "conversations.history", token, params)
        if not payload.get("ok"):
            # not_in_channel and missing_scope are structural facts about the
            # token, not errors of ours: skip this conversation and say so.
            log.info(
                "Slack adapter: skipping conversation %s (%s)",
                channel_id,
                payload.get("error") or "unknown error",
            )
            return None
        messages.extend(m for m in payload.get("messages") or [] if isinstance(m, dict))
        cursor = _next_cursor(payload)
        if not cursor:
            break
    else:
        log.warning(
            "Slack adapter: conversation %s exceeded the history page cap; "
            "imported what was read",
            channel_id,
        )
    messages.sort(key=_ts_sort_key)
    return messages


async def _thread_messages(
    client: httpx.AsyncClient, token: str, channel_id: str, thread_ts: str
) -> list[dict[str, Any]]:
    """One thread — parent plus every reply — ascending and de-duplicated."""
    messages: list[dict[str, Any]] = []
    cursor = ""
    for _page in range(_MAX_THREAD_PAGES):
        params: dict[str, Any] = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": _PAGE_LIMIT,
        }
        if cursor:
            params["cursor"] = cursor
        payload = await _call(client, "conversations.replies", token, params)
        if not payload.get("ok"):
            log.debug(
                "Slack adapter: replies unavailable for %s/%s (%s)",
                channel_id,
                thread_ts,
                payload.get("error") or "unknown error",
            )
            break
        messages.extend(m for m in payload.get("messages") or [] if isinstance(m, dict))
        cursor = _next_cursor(payload)
        if not cursor:
            break
    else:
        log.warning(
            "Slack adapter: thread %s/%s exceeded the reply page cap; "
            "imported what was read",
            channel_id,
            thread_ts,
        )
    unique = {str(m.get("ts") or ""): m for m in messages if m.get("ts")}
    return sorted(unique.values(), key=_ts_sort_key)


def _to_ns(ts: str) -> int:
    """A Slack ``ts`` ("seconds.fraction") → nanoseconds; 0 when unparseable.

    Split-and-pad instead of ``float`` math: at epoch scale a float64 loses
    the sub-second digits that make two messages ordered.
    """
    text = str(ts or "").strip()
    if not text:
        return 0
    seconds, _, fraction = text.partition(".")
    try:
        whole = int(seconds)
    except ValueError:
        return 0
    try:
        frac_ns = int((fraction + "000000000")[:9]) if fraction else 0
    except ValueError:
        frac_ns = 0
    return whole * 1_000_000_000 + frac_ns


def _ts_to_iso(ts: str) -> str:
    ns = _to_ns(ts)
    if ns <= 0:
        return ""
    moment = datetime.fromtimestamp(ns // 1_000_000_000, tz=UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_of(ts: str) -> str:
    ns = _to_ns(ts)
    if ns <= 0:
        return ""
    return datetime.fromtimestamp(ns // 1_000_000_000, tz=UTC).strftime("%Y-%m-%d")


def _cursor_to_oldest(checkpoint: str | None) -> str:
    """The stored numeric cursor as a Slack ``oldest`` value. Empty when unset.

    The cursor is nanoseconds since the epoch (the sync runner's convention).
    History is re-asked from the START OF THE PREVIOUS UTC DAY: day-chunk
    items carry a per-day id, so a mid-day cut would rebuild a HALF day under
    the full day's id and silently shrink it. Re-yielded items upsert as
    ``unchanged``. A non-numeric checkpoint — the bridge passes the last
    item's id during a backfill — is read as "no cursor".
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
    day_start = datetime.fromordinal(moment.toordinal() - 1).replace(tzinfo=UTC)
    return f"{int(day_start.timestamp())}.000000"


def _permalink(team_url: str, channel_id: str, ts: str) -> str:
    """The canonical archive deep link ``chat.getPermalink`` would return."""
    return f"{team_url.rstrip('/')}/archives/{channel_id}/p{str(ts).replace('.', '')}"


def _clean_text(text: str, names: dict[str, str]) -> str:
    """Slack markup → readable prose: mentions named, links unwrapped."""
    if not text:
        return ""
    cleaned = _MENTION_RE.sub(lambda m: "@" + names.get(m.group(1), m.group(1)), text)
    cleaned = _CHANNEL_REF_RE.sub(lambda m: "#" + m.group(1), cleaned)
    cleaned = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), cleaned)
    return (
        cleaned.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()
    )


def _author_of(message: dict[str, Any], names: dict[str, str]) -> str:
    user = str(message.get("user") or "")
    if user:
        return names.get(user, user)
    profile = message.get("bot_profile") if isinstance(message.get("bot_profile"), dict) else {}
    return str(message.get("username") or profile.get("name") or "app")


def _file_name(file_info: dict[str, Any]) -> str:
    """What a person would call this upload."""
    return str(
        file_info.get("name") or file_info.get("title") or "attachment"
    ).strip()


def _readable_files(message: dict[str, Any]) -> list[dict[str, Any]]:
    """The uploads in one message whose text is worth fetching.

    A picture or a video is filtered out here, before any request: no OCR
    ships in this adapter, so downloading one would cost a request per
    message and import nothing.
    """
    found: list[dict[str, Any]] = []
    for file_info in message.get("files") or []:
        if not isinstance(file_info, dict):
            continue
        if not str(file_info.get("url_private_download") or "").strip():
            continue
        name = _file_name(file_info)
        suffix = PurePosixPath(name).suffix.lower()
        mime = str(file_info.get("mimetype") or "").lower()
        if (
            suffix in DOCUMENT_EXTENSIONS
            or suffix in TEXT_EXTENSIONS
            or mime.startswith("text/")
        ):
            found.append(file_info)
    return found


async def _file_item(
    client: httpx.AsyncClient,
    token: str,
    channel_id: str,
    label: str,
    message: dict[str, Any],
    file_info: dict[str, Any],
    names: dict[str, str],
    team_url: str,
) -> RawItem | None:
    """One shared file as its own item, tied to the message that shared it.

    Shared documents are where a channel's actual decisions live: the deck,
    the spec, the spreadsheet. Recording only that "a file was shared" makes
    the memory able to say a document exists and nothing about what it says.
    """
    name = _file_name(file_info)
    file_id = str(file_info.get("id") or "").strip()
    ts = str(message.get("ts") or "")
    if not file_id or not ts:
        return None

    reason = ""
    text = ""
    try:
        size = int(file_info.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > MAX_DOCUMENT_BYTES:
        reason = (
            f"the file is {size // (1024 * 1024)} MB, above the "
            f"{MAX_DOCUMENT_BYTES // (1024 * 1024)} MB import limit"
        )
    else:
        url = str(file_info.get("url_private_download") or "")
        try:
            response = await client.get(
                url, headers=_headers(token), follow_redirects=True
            )
        except httpx.HTTPError:
            # One unreachable upload is not the sync's problem to die of.
            reason = "the download could not be reached"
        else:
            if response.status_code >= 400:
                reason = f"the download failed (HTTP {response.status_code})"
            else:
                result = extract_text(
                    response.content,
                    filename=name,
                    mime=str(file_info.get("mimetype") or ""),
                )
                if result.ok:
                    text = result.text
                else:
                    reason = result.reason or "no text could be read from this file"

    author = _author_of(message, names)
    header = f"{label} · file shared by {author} · {name}"
    body = _cap_body(
        "\n\n".join([header, text or f"[no text imported: {reason}]"]).strip()
    )
    return RawItem(
        external_id=f"{channel_id}:{ts}#file-{file_id}",
        title=name,
        body=body,
        permalink=str(file_info.get("permalink") or "")
        or _permalink(team_url, channel_id, ts),
        timestamp_utc=_ts_to_iso(ts),
        thread_key=channel_id,
        author_raw=author,
        metadata={
            "mtime_ns": _to_ns(ts),
            "channel": channel_id,
            "channel_label": label,
            "kind": "file",
            "file_id": file_id,
            "filename": name,
            **({"content_missing": True, "content_missing_reason": reason} if reason else {}),
        },
    )


def _line(message: dict[str, Any], names: dict[str, str]) -> str:
    """One ``author: text`` line; empty when the message carries no words.

    Files become ``[file: <name>]`` markers in the conversation. The bytes of
    a readable one are pulled separately, into an item of its own, so the
    transcript stays a transcript.
    """
    parts: list[str] = []
    text = _clean_text(str(message.get("text") or ""), names)
    if text:
        parts.append(text)
    for file_info in message.get("files") or []:
        if isinstance(file_info, dict):
            name = str(file_info.get("title") or file_info.get("name") or "attachment")
            parts.append(f"[file: {name}]")
    if not parts:
        return ""
    return f"{_author_of(message, names)}: {' '.join(parts)}"


def _cap_body(body: str) -> str:
    encoded = body.encode("utf-8")
    if len(encoded) <= _MAX_BODY_BYTES:
        return body
    kept = encoded[:_MAX_BODY_BYTES].decode("utf-8", errors="ignore").rstrip()
    return kept + _TRUNCATION_MARKER


def _channel_label(channel: dict[str, Any], names: dict[str, str]) -> str:
    if channel.get("is_im"):
        user = str(channel.get("user") or "")
        return f"DM with @{names.get(user, user or 'unknown')}"
    name = str(channel.get("name") or channel.get("id") or "conversation")
    if channel.get("is_mpim"):
        return f"group DM {name}"
    return f"#{name}"


def _max_mtime_ns(messages: list[dict[str, Any]]) -> int:
    return max((_to_ns(str(m.get("ts") or "")) for m in messages), default=0)


def _thread_item(
    channel_id: str,
    label: str,
    messages: list[dict[str, Any]],
    names: dict[str, str],
    team_url: str,
) -> RawItem | None:
    """One thread as one item — parent and replies in spoken order."""
    lines = [line for message in messages if (line := _line(message, names))]
    parent_ts = str(messages[0].get("ts") or "") if messages else ""
    if not lines or not parent_ts:
        return None
    parent_text = _clean_text(str(messages[0].get("text") or ""), names)
    title = f"Thread in {label}"
    if parent_text:
        title += f": {parent_text[:80]}"
    header = f"{label} · thread · {len(lines)} message(s)"
    return RawItem(
        external_id=f"{channel_id}:{parent_ts}",
        title=title,
        body=_cap_body("\n".join([header, "", *lines])),
        permalink=_permalink(team_url, channel_id, parent_ts),
        timestamp_utc=_ts_to_iso(parent_ts),
        thread_key=channel_id,
        author_raw=_author_of(messages[0], names),
        metadata={
            "mtime_ns": _max_mtime_ns(messages),
            "channel": channel_id,
            "channel_label": label,
            "kind": "thread",
            "thread_ts": parent_ts,
            "message_count": len(lines),
        },
    )


def _day_item(
    channel_id: str,
    label: str,
    day: str,
    messages: list[dict[str, Any]],
    names: dict[str, str],
    team_url: str,
) -> RawItem | None:
    """One calendar day of a conversation's non-threaded messages as one item."""
    lines = [line for message in messages if (line := _line(message, names))]
    first_ts = str(messages[0].get("ts") or "") if messages else ""
    if not lines or not first_ts:
        return None
    header = f"{label} · {day} · {len(lines)} message(s)"
    return RawItem(
        external_id=f"{channel_id}:{day}",
        title=f"{label} on {day}",
        body=_cap_body("\n".join([header, "", *lines])),
        permalink=_permalink(team_url, channel_id, first_ts),
        timestamp_utc=_ts_to_iso(first_ts),
        thread_key=channel_id,
        metadata={
            "mtime_ns": _max_mtime_ns(messages),
            "channel": channel_id,
            "channel_label": label,
            "kind": "day",
            "day": day,
            "message_count": len(lines),
        },
    )


async def _conversation_items(
    client: httpx.AsyncClient,
    token: str,
    channel: dict[str, Any],
    names: dict[str, str],
    team_url: str,
    oldest: str,
) -> list[RawItem] | None:
    """Every item one conversation contributes, ascending. ``None`` = skipped."""
    channel_id = str(channel.get("id") or "")
    if not channel_id:
        return []
    history = await _history(client, token, channel_id, oldest)
    if history is None:
        return None
    label = _channel_label(channel, names)

    # Threads are pulled out of the day flow: a parent with replies becomes a
    # thread item, and a broadcast reply ("also sent to channel") marks its
    # thread for expansion instead of duplicating one line into a day chunk.
    thread_parents: dict[str, dict[str, Any] | None] = {}
    by_day: dict[str, list[dict[str, Any]]] = {}
    for message in history:
        ts = str(message.get("ts") or "")
        if not ts or str(message.get("subtype") or "") in _NOISE_SUBTYPES:
            continue
        thread_ts = str(message.get("thread_ts") or "")
        if thread_ts and thread_ts != ts:
            thread_parents.setdefault(thread_ts, None)
            continue
        if int(message.get("reply_count") or 0) > 0:
            thread_parents[ts] = message
            continue
        day = _day_of(ts)
        if day:
            by_day.setdefault(day, []).append(message)

    items: list[RawItem] = []
    shared: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for thread_ts in sorted(thread_parents, key=float):
        messages = await _thread_messages(client, token, channel_id, thread_ts)
        if not messages:
            parent = thread_parents[thread_ts]
            messages = [parent] if parent else []
        item = _thread_item(channel_id, label, messages, names, team_url)
        if item is not None:
            items.append(item)
        shared.extend(
            (message, file_info)
            for message in messages
            for file_info in _readable_files(message)
        )
    for day in sorted(by_day):
        day_item = _day_item(channel_id, label, day, by_day[day], names, team_url)
        if day_item is not None:
            items.append(day_item)
        shared.extend(
            (message, file_info)
            for message in by_day[day]
            for file_info in _readable_files(message)
        )
    # Downloads happen after the conversation is assembled, so a slow or dead
    # file server delays the uploads only — never the transcript itself.
    for message, file_info in shared:
        file_item = await _file_item(
            client, token, channel_id, label, message, file_info, names, team_url
        )
        if file_item is not None:
            items.append(file_item)
    items.sort(key=lambda item: (item.timestamp_utc, item.external_id))
    return items


async def slack_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every conversation the connected Slack token can read.

    ``checkpoint`` doubles as the incremental cursor: numeric means "only
    what changed since", anything else means a full backfill. ``transport``
    is a test seam; production leaves it ``None``.
    """
    token = _token()
    oldest = _cursor_to_oldest(checkpoint)
    yielded = 0
    skipped = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        team_url = await _team_url(client, token)
        names = await _display_names(client, token)
        conversations = await _conversations(client, token)
        for channel in conversations:
            items = await _conversation_items(
                client, token, channel, names, team_url, oldest
            )
            if items is None:
                skipped += 1
                continue
            for item in items:
                yielded += 1
                yield item

    log.info(
        "Slack adapter: yielded %d item(s) from %d conversation(s) for %s (%s%s)",
        yielded,
        len(conversations) - skipped,
        ctx.source_id,
        "incremental" if oldest else "backfill",
        f"; {skipped} unreadable conversation(s) skipped" if skipped else "",
    )
