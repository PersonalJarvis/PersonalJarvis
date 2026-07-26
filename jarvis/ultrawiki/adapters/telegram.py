"""Telegram pull adapter — forward capture only, because the Bot API allows
nothing else.

THE STRUCTURAL LIMIT, UP FRONT: a Telegram BOT cannot read the past. The Bot
API has no history endpoint — ``getUpdates`` holds only the updates delivered
since the bot last confirmed them and Telegram keeps them for roughly 24
hours; anything said before the bot joined a chat, and anything older than
that window, is permanently out of reach for a bot token. "Everything the
token allows" therefore honestly means FORWARD CAPTURE: from the moment this
source syncs, every message the bot receives is imported and nothing that
arrives is lost between runs — it is not, and can never be, a backfill of old
conversations. (A full-history Telegram import would need a user-account MTProto
session, which is a different credential and a different consent conversation.)

Three more honest boundaries of that capture:

* Reading updates CONSUMES them: advancing the ``getUpdates`` offset confirms
  earlier pages server-side. That also means only ONE reader can exist — when
  the Telegram chat channel (or a webhook) is polling this bot, Telegram
  answers 409 and the sync surfaces that plainly instead of silently reading
  nothing. On installs where the chat channel is active, that channel eats
  the update stream first.
* In group chats, BotFather's default "privacy mode" delivers only commands
  and replies to the bot; capturing the full group conversation requires the
  user to disable it for their bot.
* Media is recorded as a text placeholder plus any caption; binaries are
  never downloaded.

Determinism and cursors: updates arrive ascending by ``update_id`` and are
emitted in that order, one :class:`RawItem` per message (an edit re-yields
the same ``external_id`` and upserts as changed). The cursor rides on
``metadata["max_rowid"]`` = the update id — the numeric key the sync runner
already advances (the jarvis-conversations connector uses the same one) — so
a numeric checkpoint becomes ``offset = cursor + 1``. A non-numeric backfill
checkpoint is not a position in a replayable stream (the stream is gone) and
degrades to "whatever Telegram still holds unconfirmed", which is exactly
the un-imported remainder. Bodies are capped near 1 MB with a truncation
marker, far above Telegram's own 4096-character message ceiling.

Credentials come from the marketplace token the user connected in the
Plugins store — this adapter never asks for a second one and never logs the
token. The token is part of every Bot-API URL, so error paths repeat only an
exception's TYPE, never its text, and never a URL.
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
    "TelegramAdapterError",
    "telegram_pull_adapter",
]

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:telegram"

_API = "https://api.telegram.org"

#: ``getUpdates`` returns at most 100 updates per call.
_PAGE_LIMIT = 100

#: Uniform adapter contract; Telegram's own 4096-char message cap sits far
#: below it, so this is a guard rail, not an expected path.
_MAX_BODY_CHARS = 1_000_000
_TRUNCATION_MARKER = "\n\n[truncated near 1 MB — the rest stays on Telegram]"

#: Bounded politeness for 429s (see the Discord adapter for the rationale).
_RETRY_MAX = 3
_RETRY_WAIT_CAP_S = 15.0

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

#: The update kinds that carry a message payload. ``allowed_updates`` is
#: deliberately NOT sent: it changes the bot's subscription persistently and
#: would strip the Telegram chat channel of the update kinds IT needs.
_MESSAGE_KEYS: tuple[tuple[str, bool], ...] = (
    ("message", False),
    ("edited_message", True),
    ("channel_post", False),
    ("edited_channel_post", True),
)


class TelegramAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def _token() -> str:
    """The marketplace Telegram bot token, or an honest refusal."""
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load("telegram")
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise TelegramAdapterError(
            "The Telegram connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise TelegramAdapterError(
            "Telegram is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise TelegramAdapterError(
            "The Telegram connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


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
            payload = response.json() or {}
            value = float((payload.get("parameters") or {}).get("retry_after"))
        except Exception:  # noqa: BLE001 — a non-JSON 429 body means "use the default"
            value = None
    if value is None:
        value = 1.0
    return min(max(value, 0.0), _RETRY_WAIT_CAP_S)


async def _call(
    client: httpx.AsyncClient,
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """One Bot-API call, every failure mapped to a sentence a user can act on.

    The token lives in the URL, so no branch below may repeat a URL or an
    exception's text — only types and Telegram's own ``description`` field
    (which never contains the token).
    """
    url = f"{_API}/bot{token}/{method}"
    response: httpx.Response | None = None
    for attempt in range(_RETRY_MAX + 1):
        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise TelegramAdapterError(
                f"Telegram was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if response.status_code == 429 and attempt < _RETRY_MAX:
            await asyncio.sleep(_retry_after_seconds(response))
            continue
        break
    assert response is not None  # the loop always runs at least once
    if response.status_code in (401, 403):
        raise TelegramAdapterError(
            "Telegram rejected the bot token. Reconnect it in the Plugins store."
        )
    if response.status_code == 409:
        raise TelegramAdapterError(
            "Another consumer is reading this bot's updates (the Telegram chat "
            "channel or a webhook). Telegram allows only one reader at a time — "
            "the import can only run while that consumer is idle."
        )
    if response.status_code == 429:
        raise TelegramAdapterError(
            "Telegram's rate limit is exhausted. The next sync continues where "
            "this one stopped."
        )
    try:
        payload = response.json() or {}
    except Exception:  # noqa: BLE001 — a non-JSON answer is an API failure
        payload = {}
    if response.status_code >= 400 or not payload.get("ok"):
        description = str(payload.get("description") or "").strip()
        raise TelegramAdapterError(
            f"Telegram answered: {description or f'HTTP {response.status_code}'}"
        )
    return payload.get("result")


def _checkpoint_to_offset(checkpoint: str | None) -> int | None:
    """A numeric checkpoint (the runner's ``max_rowid`` cursor) as the next
    ``getUpdates`` offset. Anything else — including a backfill resume
    ``external_id`` — means "no offset": Telegram then serves every update it
    still holds unconfirmed, which IS the un-imported remainder.
    """
    if not checkpoint:
        return None
    try:
        cursor = int(checkpoint)
    except ValueError:
        return None
    if cursor <= 0:
        return None
    return cursor + 1


def _iso_from_unix(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chat_label(chat: dict[str, Any]) -> str:
    title = str(chat.get("title") or "").strip()
    if title:
        return title
    name = " ".join(
        part
        for part in (
            str(chat.get("first_name") or "").strip(),
            str(chat.get("last_name") or "").strip(),
        )
        if part
    )
    if name:
        return name
    username = str(chat.get("username") or "").strip()
    if username:
        return f"@{username}"
    return f"chat {chat.get('id')}"


def _author_of(message: dict[str, Any], chat: dict[str, Any]) -> str:
    sender = message.get("from")
    if isinstance(sender, dict):
        name = " ".join(
            part
            for part in (
                str(sender.get("first_name") or "").strip(),
                str(sender.get("last_name") or "").strip(),
            )
            if part
        )
        if name:
            return name
        username = str(sender.get("username") or "").strip()
        if username:
            return f"@{username}"
    signature = str(message.get("author_signature") or "").strip()
    if signature:
        return signature
    return _chat_label(chat)  # channel posts carry no sender


def _permalink_for(chat: dict[str, Any], message_id: int) -> str:
    """The deepest link Telegram offers for this chat kind.

    Public chats get a real ``t.me`` URL; private supergroups/channels get
    the members-only ``t.me/c/`` form; private and small-group chats have no
    web URL at all, so the ``tg://`` app link is the honest deepest link —
    it resolves inside a logged-in Telegram app, nowhere else.
    """
    username = str(chat.get("username") or "").strip()
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = str(chat.get("id") or "")
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    if str(chat.get("type") or "") == "private":
        return f"tg://openmessage?user_id={chat_id}&message_id={message_id}"
    return f"tg://openmessage?chat_id={chat_id.lstrip('-')}&message_id={message_id}"


def _message_text(message: dict[str, Any]) -> str:
    """The message's prose plus honest placeholders for undownloaded media."""
    parts: list[str] = []
    text = str(message.get("text") or message.get("caption") or "").strip()
    if text:
        parts.append(text)
    if message.get("photo"):
        parts.append("[photo]")
    document = message.get("document")
    if isinstance(document, dict):
        name = str(document.get("file_name") or "").strip()
        parts.append(f"[file: {name}]" if name else "[file]")
    for kind in ("voice", "video", "audio", "video_note"):
        if message.get(kind):
            parts.append(f"[{kind.replace('_', ' ')}]")
    return " ".join(parts)


def _item_from_message(
    message: dict[str, Any], *, update_id: int, edited: bool
) -> RawItem | None:
    """One update's message as a ``RawItem``; ``None`` when it holds no text."""
    chat = message.get("chat")
    message_id = message.get("message_id")
    date = int(message.get("date") or 0)
    if not isinstance(chat, dict) or message_id is None or date <= 0:
        return None
    text = _message_text(message)
    if not text:
        return None  # stickers, joins, service messages — nothing to remember

    chat_id = str(chat.get("id") or "")
    label = _chat_label(chat)
    author = _author_of(message, chat)
    header = f"{label} · {author}" + (" · edited" if edited else "")
    body = f"{header}\n\n{text}"
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS] + _TRUNCATION_MARKER

    return RawItem(
        external_id=f"msg:{chat_id}:{message_id}",
        title=f"{author} in {label}",
        body=body,
        permalink=_permalink_for(chat, int(message_id)),
        # WHEN IT WAS SAID — an edit updates the body, not the moment.
        timestamp_utc=_iso_from_unix(date),
        thread_key=f"chat:{chat_id}",
        author_raw=author,
        metadata={
            # The runner advances the cursor on this key (max update id).
            "max_rowid": update_id,
            "chat_id": chat_id,
            "chat_type": str(chat.get("type") or ""),
            "chat_title": label,
            "message_id": int(message_id),
            "edited": edited,
        },
    )


def _item_from_update(update: dict[str, Any]) -> RawItem | None:
    try:
        update_id = int(update.get("update_id") or 0)
    except (TypeError, ValueError):
        return None
    if update_id <= 0:
        return None
    for key, edited in _MESSAGE_KEYS:
        message = update.get(key)
        if isinstance(message, dict):
            return _item_from_message(message, update_id=update_id, edited=edited)
    return None  # callback queries, member events, ... — not memory material


async def telegram_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every message update the bot currently holds, oldest first.

    ``checkpoint`` numeric = the runner's update-id cursor (continue after
    it); anything else = read whatever Telegram still holds unconfirmed.
    ``transport`` is a test seam; production leaves it ``None``.
    """
    token = _token()
    offset = _checkpoint_to_offset(checkpoint)
    yielded = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        # Fail fast and honestly on a dead token, before touching the queue.
        await _call(client, token, "getMe")
        while True:
            params: dict[str, Any] = {"limit": _PAGE_LIMIT, "timeout": 0}
            if offset is not None:
                params["offset"] = offset
            updates = await _call(client, token, "getUpdates", params)
            if not isinstance(updates, list) or not updates:
                break
            highest = 0
            for update in updates:
                if not isinstance(update, dict):
                    continue
                try:
                    highest = max(highest, int(update.get("update_id") or 0))
                except (TypeError, ValueError):
                    pass
                item = _item_from_update(update)
                if item is not None:
                    yielded += 1
                    yield item
            if highest <= 0:
                break
            offset = highest + 1
            if len(updates) < _PAGE_LIMIT:
                break  # the queue is drained

    log.info(
        "Telegram adapter: yielded %d item(s) for %s (forward capture%s)",
        yielded,
        ctx.source_id,
        ", resumed from cursor" if offset is not None and checkpoint else "",
    )
