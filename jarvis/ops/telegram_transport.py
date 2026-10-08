"""Live Telegram transport for the OwnerNotifier — PREPARED, NOT WIRED.

Nothing in the app selects this transport yet: every notification path still
uses :class:`~jarvis.ops.notify.SimulatedTelegramTransport`. Switching to live
delivery, and the first real test message, are a separate step that needs the
owner's explicit approval.

When it is used, it talks to the official, free Telegram Bot API
(``sendMessage``) and nothing else:

- **Credentials** come from the existing secret store under the same key the
  Telegram channel uses (``telegram_bot_token``: keyring, then environment),
  never from a route, a request body, ``jarvis.toml`` or the repository.
- **Owner only.** The chat is the ONE paired private user of the Telegram
  channel (``integrations.telegram.allowed_user_ids``; in a private chat the
  chat id is the user id). No pairing, several users, a group id or a
  ``chat_id`` setting that names someone else: nothing is sent.
- **No secrets anywhere.** The token is only ever part of the request URL;
  errors become short codes and never carry an exception text, a response body
  or the URL (AP-34). Logs say nothing about the token or the chat.
- **Rate limits.** At most one message per :data:`MIN_INTERVAL_S` to the chat;
  a 429 carries Telegram's ``retry_after`` back to the notifier, which waits
  or leaves the message for the next run. Network trouble and 5xx are
  retryable; a revoked token, a blocked bot or a bad request are not.
- Plain text only (no parse mode), so a title cannot inject markup.

The connection comes from the shared keep-alive pool (AP-33).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from jarvis.core.http_pool import HttpClientPool
from jarvis.ops.notify import TELEGRAM_MAX_CHARS, TransportError

API_ROOT: Final = "https://api.telegram.org"
TOKEN_SECRET: Final = "telegram_bot_token"  # noqa: S105 - the secret-store KEY, not a secret
MIN_INTERVAL_S: Final = 1.1
REQUEST_TIMEOUT_S: Final = 15.0


@dataclass(frozen=True, slots=True)
class OwnerChat:
    """The owner's private chat, or why there is none."""

    chat_id: int | None
    reason: str = ""


def owner_chat(telegram_config: Any) -> OwnerChat:
    """Resolve the ONE owner chat from the Telegram channel's pairing."""
    users = [int(u) for u in (getattr(telegram_config, "allowed_user_ids", None) or [])]
    if not users:
        return OwnerChat(None, "owner_not_paired")
    if len(set(users)) != 1:
        return OwnerChat(None, "owner_not_unique")
    owner = users[0]
    if owner <= 0:  # private chats (users) have positive ids; groups do not
        return OwnerChat(None, "owner_not_private")
    configured = str(getattr(telegram_config, "chat_id", "") or "").strip()
    if configured:
        try:
            if int(configured) != owner:
                return OwnerChat(None, "chat_id_mismatch")
        except ValueError:  # a non-numeric chat_id (e.g. @channel) is never the owner
            return OwnerChat(None, "chat_id_mismatch")
    return OwnerChat(owner)


def default_token() -> str | None:
    """The bot token from the existing secret store (keyring, then ENV)."""
    from jarvis.core.config import get_secret

    return get_secret(TOKEN_SECRET, env_fallback="TELEGRAM_BOT_TOKEN") or None


_POOL = HttpClientPool(timeout_s=REQUEST_TIMEOUT_S)


class TelegramBotTransport:
    """``sendMessage`` to the owner's private chat. Not selected anywhere yet."""

    name = "telegram"
    simulated = False

    def __init__(
        self,
        *,
        owner: Callable[[], OwnerChat],
        token: Callable[[], str | None] = default_token,
        pool: HttpClientPool | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        min_interval_s: float = MIN_INTERVAL_S,
    ) -> None:
        self._owner = owner
        self._token = token
        self._pool = pool or _POOL
        self._sleep = sleep
        self._monotonic = monotonic
        self._min_interval_s = min_interval_s
        self._last_send: float | None = None

    async def send(self, text: str) -> None:
        if not text.strip():
            raise TransportError("empty_text", retryable=False)
        if len(text) > TELEGRAM_MAX_CHARS:
            raise TransportError("text_too_long", retryable=False)
        owner = self._owner()
        if owner.chat_id is None:
            raise TransportError(owner.reason or "owner_not_paired", retryable=False)
        token = self._token()
        if not token:
            raise TransportError("token_missing", retryable=False)
        await self._pace()
        response = await self._post(token, owner.chat_id, text)
        self._last_send = self._monotonic()
        _raise_for(response)

    async def _pace(self) -> None:
        if self._last_send is None:
            return
        wait = self._min_interval_s - (self._monotonic() - self._last_send)
        if wait > 0:
            await self._sleep(wait)

    async def _post(self, token: str, chat_id: int, text: str) -> Any:
        import httpx

        url = f"{API_ROOT}/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        try:
            return await self._pool.client().post(url, json=payload)
        except httpx.TimeoutException:
            # No exception text: an httpx error can carry the URL, and the URL
            # carries the token.
            raise TransportError("timeout", retryable=True) from None
        except httpx.HTTPError:
            raise TransportError("network", retryable=True) from None


def _raise_for(response: Any) -> None:
    status = int(getattr(response, "status_code", 0) or 0)
    try:
        body = response.json()
    except ValueError:  # a non-JSON answer is judged by its status alone
        body = {}
    if not isinstance(body, dict):
        body = {}
    if status == 200 and body.get("ok") is True:
        return
    if status == 429:
        retry_after = None
        parameters = body.get("parameters")
        raw = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if isinstance(raw, int | float) and not isinstance(raw, bool) and raw >= 0:
            retry_after = float(raw)
        raise TransportError("rate_limited", retryable=True, retry_after=retry_after)
    if status >= 500 or status == 0:
        raise TransportError("server_error", retryable=True)
    if status in (401, 404):
        raise TransportError("invalid_token", retryable=False)
    if status == 403:
        raise TransportError("blocked_by_owner", retryable=False)
    if status == 400:
        raise TransportError("bad_request", retryable=False)
    raise TransportError(f"http_{status}", retryable=False)


__all__ = [
    "API_ROOT",
    "MIN_INTERVAL_S",
    "TOKEN_SECRET",
    "OwnerChat",
    "TelegramBotTransport",
    "default_token",
    "owner_chat",
]
