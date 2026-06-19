"""Bridge a Discord marketplace connect into the existing DiscordChannel.

"Connecting Discord" is not an MCP tool — it enables the in-repo bidirectional
channel (``jarvis/channels/discord.py``), exactly like Telegram. We mirror the
validated bot token into the canonical ``discord_bot_token`` secret and flip
``[integrations.discord].enabled`` so the channel boots. An explicit owner user
id locks the allowlist and turns trust-on-first-DM off. Disconnecting reverses
the secret + enable flag. This reuses the channel's allowlist + voice-scrub
conventions instead of running a separate Node MCP server.
"""

from __future__ import annotations

import logging

from jarvis.core.config import delete_secret, set_secret

log = logging.getLogger(__name__)

_SECRET_KEY = "discord_bot_token"  # noqa: S105 — keyring entry name, not a secret


def _set_discord_enabled(on: bool) -> None:
    from jarvis.core.config_writer import set_discord_enabled

    set_discord_enabled(on)


def _add_discord_allowed_user_id(user_id: int) -> None:
    from jarvis.core.config_writer import add_discord_allowed_user_id

    add_discord_allowed_user_id(user_id)


def _set_discord_pairing(on: bool) -> None:
    from jarvis.core.config_writer import set_discord_pairing

    set_discord_pairing(on)


def on_discord_connected(token: str, allowed_user_id: int | None = None) -> None:
    """Store the bot token + enable the channel. Raises on a secret-store error.

    When ``allowed_user_id`` is given, lock the bot to that owner: append the id
    to the allowlist and turn trust-on-first-DM off so nobody else can claim it.
    """
    if not set_secret(_SECRET_KEY, token):
        raise RuntimeError("could not store discord_bot_token in the credential store")
    # Owner-lock BEFORE enabling: the three writes are separate atomic TOML
    # writes, so enabling last guarantees a crash mid-sequence never leaves the
    # channel enabled with an open (pair-on-first-DM) allowlist.
    if allowed_user_id is not None:
        _add_discord_allowed_user_id(int(allowed_user_id))
        _set_discord_pairing(False)
    _set_discord_enabled(True)
    log.info("discord connected via marketplace — channel enabled")


def on_discord_disconnected() -> None:
    """Clear the bot token + disable the channel."""
    delete_secret(_SECRET_KEY)
    _set_discord_enabled(False)
    log.info("discord disconnected via marketplace — channel disabled")
