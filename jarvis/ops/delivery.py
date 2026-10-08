"""Live switch for owner notifications: simulated unless the owner turned it on.

This is the ONE place that picks the live :class:`TelegramBotTransport`; every
other path asks :func:`select_transport` and gets the simulated transport
while the switch is off (the default).

- :func:`enable_live` refuses unless the bot token is stored and exactly one
  private owner chat is paired (``owner_chat``) — the same readiness facts
  ``GET /api/ops/notify/telegram`` reports.
- :func:`send_connection_test` sends ONE plain-text test message to that
  owner chat, and only while the switch is on. Its dedup key covers the text
  and the day, so repeating the call sends nothing twice.
- Switching off (:func:`disable_live`, or any other settings change) returns
  every path to simulated delivery.

No model is called; the Telegram Bot API is free. Errors are codes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date
from typing import Any, Final

from jarvis.ops.notify import (
    DeliveryOutcome,
    Notification,
    NotificationTransport,
    NotifySettings,
    NotifyStore,
    OwnerNotifier,
    SimulatedTelegramTransport,
)
from jarvis.ops.telegram_transport import OwnerChat, TelegramBotTransport, default_token, owner_chat

TEST_TEXT_MAX: Final = 300


class LiveNotReady(RuntimeError):
    """The live switch cannot be turned on: ``reason`` is a short code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LiveDisabled(RuntimeError):
    """A live-only action was asked for while the switch is off."""


def readiness(telegram_config: Any, token: Callable[[], str | None] = default_token) -> str:
    """``""`` when live delivery could run, else the first missing piece."""
    if not token():
        return "token_missing"
    owner = owner_chat(telegram_config)
    if owner.chat_id is None:
        return owner.reason or "owner_not_paired"
    return ""


def select_transport(
    settings: NotifySettings,
    telegram_config: Any,
    *,
    live_factory: Callable[[Callable[[], OwnerChat]], NotificationTransport] | None = None,
) -> NotificationTransport:
    """The live Telegram transport only while the switch is on."""
    if not settings.live:
        return SimulatedTelegramTransport()
    factory = live_factory or (lambda owner: TelegramBotTransport(owner=owner))
    return factory(lambda: owner_chat(telegram_config))


async def enable_live(
    store: NotifyStore,
    telegram_config: Any,
    *,
    token: Callable[[], str | None] = default_token,
) -> NotifySettings:
    reason = readiness(telegram_config, token)
    if reason:
        raise LiveNotReady(reason)
    return await store.set_live(True)


async def disable_live(store: NotifyStore) -> NotifySettings:
    return await store.set_live(False)


def connection_test_key(text: str, day: date) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"connection_test:{day.isoformat()}:{digest}"


async def send_connection_test(
    store: NotifyStore,
    transport: NotificationTransport,
    *,
    text: str,
    day: date,
) -> DeliveryOutcome:
    """ONE test message to the owner chat, only while live delivery is on."""
    text = text.strip()
    if not text or len(text) > TEST_TEXT_MAX:
        raise ValueError(f"test text must be 1-{TEST_TEXT_MAX} characters")
    settings = await store.settings()
    if not settings.live:
        raise LiveDisabled("live delivery is off")
    note = Notification("connection_test", connection_test_key(text, day), text, "high")
    report = await OwnerNotifier(store, transport, max_per_run=1).deliver([note], explicit=True)
    return report.outcomes[0]


__all__ = [
    "TEST_TEXT_MAX",
    "LiveDisabled",
    "LiveNotReady",
    "connection_test_key",
    "disable_live",
    "enable_live",
    "readiness",
    "select_transport",
    "send_connection_test",
]
