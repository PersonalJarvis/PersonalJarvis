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


def on_telegram_connected(token: str) -> None:
    """Store the bot token + enable the channel. Raises on a secret-store error."""
    if not set_secret(_SECRET_KEY, token):
        raise RuntimeError("could not store telegram_bot_token in the credential store")
    _set_telegram_enabled(True)
    log.info("telegram connected via marketplace — channel enabled")


def on_telegram_disconnected() -> None:
    """Clear the bot token + disable the channel."""
    delete_secret(_SECRET_KEY)
    _set_telegram_enabled(False)
    log.info("telegram disconnected via marketplace — channel disabled")
