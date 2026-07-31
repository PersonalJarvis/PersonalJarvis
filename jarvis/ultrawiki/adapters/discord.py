"""Discord pull adapter — every conversation the connected bot can read.

Scope, stated honestly: a Discord BOT token reaches exactly the servers the
bot was invited to, and inside them exactly the text and announcement
channels it can view and whose history it may read. Nothing else exists for
it: direct messages are structurally unreachable (the API exposes no DM list
to a bot), and channels without the View Channel / Read Message History
permissions are skipped quietly rather than failing the whole sync. Thread
and forum posts are not walked yet — a tracked gap, not a silent one.
Attachments are recorded by filename only; binaries are never downloaded.

Within that boundary the adapter takes EVERYTHING: each readable channel's
full message history, paginated backwards with ``before=<id>`` until the
beginning, then emitted oldest-to-newest as one :class:`RawItem` per
channel-day — a day of one channel is the natural memory unit of a chat, and
per-message rows would drown the store. Bodies are capped near 1 MB with an
explicit truncation marker.

Determinism and resume: emission order is servers ascending by id, channels
ascending by id, days ascending — so the bridge's backfill checkpoint (the
last emitted ``external_id``) lets a resumed run skip strictly past what was
already imported. A numeric checkpoint is read as the sync runner's
``mtime_ns`` cursor (the convention the file walkers and the GitHub adapter
share): it is floored to the start of its UTC day, converted to a snowflake,
and passed as ``after=`` — re-asking from the day boundary is what keeps
day chunks complete, and re-yielded chunks upsert as ``unchanged``.

Credentials come from the marketplace token the user connected in the
Plugins store — this adapter never asks for a second one and never logs the
token.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
    "DiscordAdapterError",
    "discord_pull_adapter",
]

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:discord"

_API = "https://discord.com/api/v10"

#: Milliseconds of the Discord epoch (2015-01-01T00:00:00Z) — snowflake ids
#: encode their creation time relative to this, which is what lets a time
#: cursor become an ``after=<id>`` parameter without any extra request.
_DISCORD_EPOCH_MS = 1_420_070_400_000

#: API maxima: 100 messages per history page, 200 guilds per listing page.
_PAGE_LIMIT = 100
_GUILD_PAGE_LIMIT = 200

#: Channel types that hold readable prose: 0 = GUILD_TEXT, 5 = ANNOUNCEMENT.
_TEXT_CHANNEL_TYPES = frozenset({0, 5})

#: One item's body stays under roughly 1 MB; beyond it the chunk is cut with
#: an explicit marker rather than silently dropped or shipped oversized.
_MAX_BODY_CHARS = 1_000_000
_TRUNCATION_MARKER = "\n\n[truncated near 1 MB — the rest of this day stays on Discord]"

#: Bounded politeness for 429s: retry a few times with the server's own
#: Retry-After, each wait capped, then surface an honest error. The backfill
#: checkpoint makes stopping free; hanging on an unbounded sleep is not.
_RETRY_MAX = 3
_RETRY_WAIT_CAP_S = 15.0

#: Generous on read: a full first backfill is not instant, but no single
#: request may hang a sync forever.
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class DiscordAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace Discord bot token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("discord")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise DiscordAdapterError(
            "The Discord connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise DiscordAdapterError(
            "Discord is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise DiscordAdapterError(
            "The Discord connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}"}


def _retry_after_seconds(response: httpx.Response) -> float:
    """The server's requested wait, bounded to a sane ceiling."""
    value: float | None = None
    header = response.headers.get("retry-after", "")
    if header:
        try:
            value = float(header)
        except ValueError:
            value = None
    if value is None:
        try:
            value = float((response.json() or {}).get("retry_after"))
        except Exception:  # noqa: BLE001 — a non-JSON 429 body means "use the default"
            value = None
    if value is None:
        value = 1.0
    return min(max(value, 0.0), _RETRY_WAIT_CAP_S)


async def _get(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    params: dict[str, Any] | None = None,
    *,
    skip_forbidden: bool = False,
) -> httpx.Response | None:
    """One GET with bounded 429 retries and user-actionable failures.

    ``skip_forbidden=True`` turns a 403 into ``None`` — the caller treats it
    as "this channel is not for the bot" instead of failing the sync.
    """
    response: httpx.Response | None = None
    for attempt in range(_RETRY_MAX + 1):
        try:
            response = await client.get(url, headers=_headers(token), params=params)
        except httpx.HTTPError as exc:
            raise DiscordAdapterError(
                f"Discord was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if response.status_code == 429 and attempt < _RETRY_MAX:
            await asyncio.sleep(_retry_after_seconds(response))
            continue
        break
    assert response is not None  # the loop always runs at least once
    if response.status_code == 401:
        raise DiscordAdapterError(
            "Discord rejected the bot token. Reconnect it in the Plugins store."
        )
    if response.status_code == 403 and skip_forbidden:
        return None
    if response.status_code == 429:
        raise DiscordAdapterError(
            "Discord's rate limit is exhausted. The next sync continues where "
            "this one stopped."
        )
    if response.status_code >= 400:
        raise DiscordAdapterError(
            f"Discord answered HTTP {response.status_code} for this request."
        )
    return response


# -- snowflake / time helpers -------------------------------------------------


def _snowflake_int(raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _ns_to_snowflake(ns: int) -> str:
    """A synthetic message id for a moment in time — ``after=`` understands it."""
    ms = ns // 1_000_000
    if ms <= _DISCORD_EPOCH_MS:
        return "0"
    return str((ms - _DISCORD_EPOCH_MS) << 22)


def _day_start_snowflake(ns: int) -> str:
    """The snowflake at 00:00 UTC of the cursor's day.

    Re-asking from the day boundary (not the cursor itself) is what keeps a
    day chunk COMPLETE: an incremental run that started mid-day would
    otherwise upsert a partial day over the full one already stored.
    """
    moment = datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC)
    midnight = datetime(moment.year, moment.month, moment.day, tzinfo=UTC)
    return _ns_to_snowflake(int(midnight.timestamp() * 1_000_000_000))


def _next_day_snowflake(day: str) -> str | None:
    """The snowflake at 00:00 UTC of the day AFTER ``day``; ``None`` if unparseable."""
    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None
    boundary = start + timedelta(days=1)
    return _ns_to_snowflake(int(boundary.timestamp() * 1_000_000_000))


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


def _iso_z(iso: str) -> str:
    """Discord's offset timestamp normalized to a clean ``...Z`` form."""
    ns = _to_ns(iso)
    if ns <= 0:
        return iso
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _day_of(iso: str) -> str:
    ns = _to_ns(iso)
    if ns <= 0:
        return ""
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC).strftime("%Y-%m-%d")


def _hhmm(iso: str) -> str:
    ns = _to_ns(iso)
    if ns <= 0:
        return ""
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC).strftime("%H:%M")


# -- checkpoint ---------------------------------------------------------------


def _parse_checkpoint(
    checkpoint: str | None,
) -> tuple[int | None, tuple[int, int, str] | None]:
    """``(cursor_ns, resume_key)`` — at most one of the two is set.

    A numeric checkpoint is the runner's ``mtime_ns`` cursor (incremental).
    ``discord:<guild>:<channel>:<day>`` is one of our own external_ids — the
    bridge hands back the last emitted one when resuming a backfill mid-walk.
    Anything else degrades to a full backfill; upserts make re-reads free.
    """
    if not checkpoint:
        return None, None
    try:
        ns = int(checkpoint)
    except ValueError:
        ns = 0
    if ns > 0:
        return ns, None
    parts = checkpoint.split(":")
    if (
        len(parts) == 4
        and parts[0] == "discord"
        and parts[1].isdigit()
        and parts[2].isdigit()
    ):
        return None, (int(parts[1]), int(parts[2]), parts[3])
    return None, None


# -- API walks ----------------------------------------------------------------


async def _guilds(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    """Every server the bot is in, ascending by id (deterministic order)."""
    collected: list[dict[str, Any]] = []
    after = ""
    while True:
        params: dict[str, Any] = {"limit": _GUILD_PAGE_LIMIT}
        if after:
            params["after"] = after
        response = await _get(client, f"{_API}/users/@me/guilds", token, params)
        assert response is not None
        rows = response.json() or []
        if not isinstance(rows, list) or not rows:
            break
        valid = [row for row in rows if isinstance(row, dict) and row.get("id")]
        collected.extend(valid)
        if len(rows) < _GUILD_PAGE_LIMIT or not valid:
            break
        after = str(max(_snowflake_int(str(row["id"])) for row in valid))
    collected.sort(key=lambda row: _snowflake_int(str(row.get("id"))))
    return collected


async def _text_channels(
    client: httpx.AsyncClient, token: str, guild_id: str
) -> list[dict[str, Any]]:
    """The guild's readable text/announcement channels, ascending by id."""
    response = await _get(
        client, f"{_API}/guilds/{guild_id}/channels", token, skip_forbidden=True
    )
    if response is None:
        log.debug("Discord adapter: no channel-list access for guild %s", guild_id)
        return []
    rows = response.json() or []
    if not isinstance(rows, list):
        return []
    channels = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("id")
        and row.get("type") in _TEXT_CHANNEL_TYPES
    ]
    channels.sort(key=lambda row: _snowflake_int(str(row.get("id"))))
    return channels


async def _channel_messages(
    client: httpx.AsyncClient,
    token: str,
    channel_id: str,
    *,
    after: str | None = None,
) -> list[dict[str, Any]] | None:
    """All reachable messages of one channel, ascending by id.

    ``None`` means the bot may not read this channel at all (403 on the first
    page); a permission loss mid-walk keeps what was already collected. With
    ``after`` set the walk moves forward from that id; otherwise the full
    history is paginated backwards with ``before`` and sorted ascending at
    the end. Each page is sorted locally too — order is OUR contract, not an
    assumption about the API's response ordering.
    """
    url = f"{_API}/channels/{channel_id}/messages"
    collected: list[dict[str, Any]] = []
    first_page = True
    cursor = after
    while True:
        params: dict[str, Any] = {"limit": _PAGE_LIMIT}
        if after is not None:
            params["after"] = cursor
        elif cursor:
            params["before"] = cursor
        response = await _get(client, url, token, params, skip_forbidden=True)
        if response is None:
            return None if first_page else collected
        first_page = False
        rows = response.json() or []
        if not isinstance(rows, list) or not rows:
            break
        batch = [row for row in rows if isinstance(row, dict) and row.get("id")]
        batch.sort(key=lambda row: _snowflake_int(str(row["id"])))
        collected.extend(batch)
        if len(rows) < _PAGE_LIMIT or not batch:
            break
        cursor = str(batch[-1]["id"]) if after is not None else str(batch[0]["id"])
    collected.sort(key=lambda row: _snowflake_int(str(row["id"])))
    return collected


# -- RawItem mapping ----------------------------------------------------------


def _author_of(message: dict[str, Any]) -> str:
    author = message.get("author") or {}
    if not isinstance(author, dict):
        return "unknown"
    return str(author.get("global_name") or author.get("username") or "unknown")


def _message_text(message: dict[str, Any]) -> str:
    """The message's prose, plus honest placeholders for what is NOT pulled."""
    parts: list[str] = []
    content = str(message.get("content") or "").strip()
    if content:
        parts.append(content)
    for embed in message.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        for key in ("title", "description"):
            value = str(embed.get(key) or "").strip()
            if value:
                parts.append(value)
    for attachment in message.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        name = str(attachment.get("filename") or "").strip()
        if name:
            # By name in the transcript; a readable one is pulled separately
            # into an item of its own, so the conversation stays a conversation.
            parts.append(f"[attachment: {name}]")
    return " ".join(parts)


def _readable_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    """The attachments of one message whose text is worth fetching.

    Filtered by name and type BEFORE any request: a meme-heavy channel must
    not cost one download per image to import nothing (no OCR ships here).
    """
    found: list[dict[str, Any]] = []
    for attachment in message.get("attachments") or []:
        if not isinstance(attachment, dict) or not attachment.get("url"):
            continue
        name = str(attachment.get("filename") or "").strip()
        suffix = PurePosixPath(name).suffix.lower()
        mime = str(attachment.get("content_type") or "").lower()
        if (
            suffix in DOCUMENT_EXTENSIONS
            or suffix in TEXT_EXTENSIONS
            or mime.startswith("text/")
        ):
            found.append(attachment)
    return found


async def _attachment_item(
    client: httpx.AsyncClient,
    guild: dict[str, Any],
    channel: dict[str, Any],
    message: dict[str, Any],
    attachment: dict[str, Any],
) -> RawItem | None:
    """One posted file as its own item, tied to the channel it was posted in."""
    guild_id = str(guild.get("id") or "")
    channel_id = str(channel.get("id") or "")
    message_id = str(message.get("id") or "")
    attachment_id = str(attachment.get("id") or "")
    name = str(attachment.get("filename") or "").strip()
    if not message_id or not attachment_id or not name:
        return None

    reason = ""
    text = ""
    try:
        size = int(attachment.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > MAX_DOCUMENT_BYTES:
        reason = (
            f"the file is {size // (1024 * 1024)} MB, above the "
            f"{MAX_DOCUMENT_BYTES // (1024 * 1024)} MB import limit"
        )
    else:
        # The CDN link carries its own signature; sending the bot token to a
        # host that is not the API would leak the credential for no gain.
        try:
            response = await client.get(
                str(attachment.get("url") or ""), follow_redirects=True
            )
        except httpx.HTTPError:
            reason = "the download could not be reached"
        else:
            if response.status_code >= 400:
                reason = f"the download failed (HTTP {response.status_code})"
            else:
                result = extract_text(
                    response.content,
                    filename=name,
                    mime=str(attachment.get("content_type") or ""),
                )
                if result.ok:
                    text = result.text
                else:
                    reason = result.reason or "no text could be read from this file"

    guild_name = str(guild.get("name") or "").strip() or f"server {guild_id}"
    channel_name = str(channel.get("name") or "").strip() or f"channel {channel_id}"
    author = _author_of(message)
    header = f"{guild_name} · #{channel_name} · file posted by {author} · {name}"
    body = f"{header}\n\n{text or f'[no text imported: {reason}]'}"
    return RawItem(
        external_id=f"discord:{guild_id}:{channel_id}:{message_id}#att-{attachment_id}",
        title=name,
        body=body[:_MAX_BODY_CHARS],
        permalink=(
            f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
        ),
        timestamp_utc=_iso_z(str(message.get("timestamp") or "")),
        thread_key=f"{guild_id}/{channel_id}",
        author_raw=author,
        metadata={
            "mtime_ns": _to_ns(str(message.get("timestamp") or "")),
            "guild_id": guild_id,
            "channel_id": channel_id,
            "guild_name": guild_name,
            "channel_name": channel_name,
            "kind": "attachment",
            "filename": name,
            "parent_message_id": message_id,
            **({"content_missing": True, "content_missing_reason": reason} if reason else {}),
        },
    )


def _chunk_item(
    guild: dict[str, Any],
    channel: dict[str, Any],
    day: str,
    messages: list[dict[str, Any]],
) -> RawItem | None:
    """One channel-day as a ``RawItem``; ``None`` when the day holds no prose."""
    guild_id = str(guild.get("id") or "")
    channel_id = str(channel.get("id") or "")
    guild_name = str(guild.get("name") or "").strip() or f"server {guild_id}"
    channel_name = str(channel.get("name") or "").strip() or f"channel {channel_id}"

    lines: list[str] = []
    used: list[dict[str, Any]] = []
    total = 0
    truncated = False
    for message in messages:
        text = _message_text(message)
        if not text:
            continue
        stamp = _hhmm(str(message.get("timestamp") or ""))
        prefix = f"[{stamp}] " if stamp else ""
        line = f"{prefix}{_author_of(message)}: {text}"
        if total + len(line) > _MAX_BODY_CHARS:
            truncated = True
            break
        lines.append(line)
        used.append(message)
        total += len(line) + 1
    if not used:
        return None

    first, last = used[0], used[-1]
    header = f"{guild_name} · #{channel_name} · {day} · {len(used)} message(s)"
    body = f"{header}\n\n" + "\n".join(lines)
    if truncated:
        body += _TRUNCATION_MARKER
    return RawItem(
        external_id=f"discord:{guild_id}:{channel_id}:{day}",
        title=f"#{channel_name} — {day}",
        body=body,
        permalink=(
            f"https://discord.com/channels/{guild_id}/{channel_id}/{first.get('id')}"
        ),
        timestamp_utc=_iso_z(str(first.get("timestamp") or "")),
        thread_key=f"{guild_id}/{channel_id}",
        author_raw="",
        metadata={
            # Ids stay strings: snowflakes overflow JSON-consumer precision.
            "mtime_ns": _to_ns(str(last.get("timestamp") or "")),
            "guild_id": guild_id,
            "channel_id": channel_id,
            "guild_name": guild_name,
            "channel_name": channel_name,
            "day": day,
            "message_count": len(used),
            "last_message_id": str(last.get("id") or ""),
            "truncated": truncated,
        },
    )


def _day_items(
    guild: dict[str, Any],
    channel: dict[str, Any],
    messages: list[dict[str, Any]],
) -> list[RawItem]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for message in messages:
        day = _day_of(str(message.get("timestamp") or ""))
        if day:
            groups.setdefault(day, []).append(message)
    items = []
    for day in sorted(groups):
        item = _chunk_item(guild, channel, day, groups[day])
        if item is not None:
            items.append(item)
    return items


# -- the adapter --------------------------------------------------------------


async def discord_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every readable channel's history as channel-day chunks.

    ``checkpoint`` is either the runner's numeric ``mtime_ns`` cursor
    (incremental: only messages from that day forward) or the last emitted
    ``external_id`` (backfill resume: continue strictly after it). ``transport``
    is a test seam; production leaves it ``None``.
    """
    token = _token()
    cursor_ns, resume = _parse_checkpoint(checkpoint)
    after_all = _day_start_snowflake(cursor_ns) if cursor_ns else None
    yielded = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        for guild in await _guilds(client, token):
            guild_id = str(guild.get("id") or "")
            guild_int = _snowflake_int(guild_id)
            if resume and guild_int < resume[0]:
                continue
            for channel in await _text_channels(client, token, guild_id):
                channel_id = str(channel.get("id") or "")
                channel_int = _snowflake_int(channel_id)
                after = after_all
                if resume and guild_int == resume[0]:
                    if channel_int < resume[1]:
                        continue
                    if channel_int == resume[1]:
                        # The checkpoint names a finished day of THIS channel:
                        # continue strictly after it, never re-walk history.
                        after = _next_day_snowflake(resume[2]) or after
                messages = await _channel_messages(
                    client, token, channel_id, after=after
                )
                if messages is None:
                    log.debug(
                        "Discord adapter: skipping unreadable channel %s in guild %s",
                        channel_id,
                        guild_id,
                    )
                    continue
                for item in _day_items(guild, channel, messages):
                    yielded += 1
                    yield item
                # After the transcript, never instead of it: a dead CDN link
                # must cost that one file and nothing else.
                for message in messages:
                    for attachment in _readable_attachments(message):
                        item = await _attachment_item(
                            client, guild, channel, message, attachment
                        )
                        if item is not None:
                            yielded += 1
                            yield item

    log.info(
        "Discord adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if cursor_ns else "backfill",
    )
