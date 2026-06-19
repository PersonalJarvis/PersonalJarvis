"""Bridge a Telegram marketplace connect into the existing TelegramChannel.

"Connecting Telegram" is not an MCP tool — it enables the in-repo bidirectional
channel (``jarvis/channels/telegram.py``). We mirror the validated bot token
into the canonical ``telegram_bot_token`` secret and flip
``[integrations.telegram].enabled`` so the channel boots. Disconnecting reverses
both. This reuses the channel's allowlist + voice-scrub conventions instead of
duplicating the token into a separate Node MCP server.
"""

from __future__ import annotations

import logging

from jarvis.core.config import delete_secret, set_secret

log = logging.getLogger(__name__)

_SECRET_KEY = "telegram_bot_token"  # noqa: S105 — keyring entry name, not a secret


def _set_telegram_enabled(on: bool) -> None:
    from jarvis.core.config_writer import set_telegram_enabled

    set_telegram_enabled(on)


def _add_telegram_allowed_user_id(user_id: int) -> None:
    from jarvis.core.config_writer import add_telegram_allowed_user_id

    add_telegram_allowed_user_id(user_id)


def _set_telegram_pairing(on: bool) -> None:
    from jarvis.core.config_writer import set_telegram_pairing

    set_telegram_pairing(on)


def on_telegram_connected(token: str, allowed_user_id: int | None = None) -> None:
    """Store the bot token + enable the channel. Raises on a secret-store error.

    When ``allowed_user_id`` is given, lock the bot to that owner: append the id
    to the allowlist and turn trust-on-first-private-message off so nobody else
    can claim it.
    """
    if not set_secret(_SECRET_KEY, token):
        raise RuntimeError("could not store telegram_bot_token in the credential store")
    # Owner-lock BEFORE enabling: the three writes are separate atomic TOML
    # writes, so enabling last guarantees a crash mid-sequence never leaves the
    # channel enabled with an open (pair-on-first-message) allowlist.
    if allowed_user_id is not None:
        _add_telegram_allowed_user_id(int(allowed_user_id))
        _set_telegram_pairing(False)
    _set_telegram_enabled(True)
    log.info("telegram connected via marketplace — channel enabled")


def on_telegram_disconnected() -> None:
    """Clear the bot token + disable the channel."""
    delete_secret(_SECRET_KEY)
    _set_telegram_enabled(False)
    log.info("telegram disconnected via marketplace — channel disabled")
