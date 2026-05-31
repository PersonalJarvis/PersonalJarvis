# === F-FRIENDS [F4] · feature/friends-section · alex-2026-05-01 ===
"""StatusPublisher: subscribes to the EventBus, filters per friend, dispatches.

Architecture (Plan F4):

- Wildcard subscriber on :class:`jarvis.core.bus.EventBus` (see the
  ``subscribe_all`` pattern in :class:`jarvis.channels.telegram.TelegramChannel`).
- Per event: hard-blacklist early-return (performance — we never want to
  iterate all friends when the event would never be dispatched anyway).
- Otherwise: read permission per friend, pass through :class:`StatusFilter`,
  dispatch via the primary channel.

Routing: The ``is_primary`` channel is determined per friend (if none is set,
the first linked channel is used). Telegram dispatches via
``send_status_card``; ``jarvis_pubkey`` is still a stub in F4 (federation
comes later, probably F5).

Lifecycle is symmetric to :class:`TelegramChannel`: ``start()`` registers
the wildcard handler, ``stop()`` removes it from
``EventBus._wildcard_subscribers`` (there is currently no public ``unsubscribe_all``).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jarvis.core.bus import EventBus
from jarvis.core.events import Event

from .models import Friend
from .schemas import StatusUpdate
from .status_filter import HARD_BLACKLIST, StatusFilter

if TYPE_CHECKING:  # pragma: no cover
    from jarvis.channels.manager import ChannelManager

    from .registry import FriendRegistry

log = logging.getLogger(__name__)


__all__ = ["StatusPublisher"]


class StatusPublisher:
    """Subscribes to the EventBus, filters per friend, dispatches to channels."""

    def __init__(
        self,
        bus: EventBus,
        friend_registry: "FriendRegistry",
        channel_manager: "ChannelManager | None" = None,
    ) -> None:
        self._bus = bus
        self._friends = friend_registry
        self._channels = channel_manager
        self._handler_ref: Any = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._handler_ref = self._on_event
        self._bus.subscribe_all(self._handler_ref)
        self._started = True
        log.info("StatusPublisher started")

    async def stop(self) -> None:
        if not self._started:
            return
        if self._handler_ref is not None:
            wildcards = getattr(self._bus, "_wildcard_subscribers", None)
            if wildcards is not None and self._handler_ref in wildcards:
                wildcards.remove(self._handler_ref)
            self._handler_ref = None
        self._started = False
        log.info("StatusPublisher stopped")

    # ------------------------------------------------------------------
    # Event-Handling
    # ------------------------------------------------------------------

    async def _on_event(self, event: Event) -> None:
        # Performance early-return: never evaluate the hard-blacklist per friend
        if type(event).__name__ in HARD_BLACKLIST:
            return

        try:
            friends = await self._friends.list_friends()
        except Exception as exc:  # noqa: BLE001
            log.warning("StatusPublisher: list_friends failed: %s", exc)
            return

        for friend in friends:
            try:
                permission = await self._friends.get_status_permission(friend.id)
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "StatusPublisher: get_status_permission(%s) failed: %s",
                    friend.id,
                    exc,
                )
                continue
            update = StatusFilter.filter(
                event, permission.profile, permission.custom_whitelist
            )
            if update is None:
                continue
            await self._dispatch_to_friend(friend, update)

    async def _dispatch_to_friend(
        self, friend: Friend, update: StatusUpdate
    ) -> None:
        """Determine the primary channel and dispatch to the appropriate adapter."""
        try:
            channels = await self._friends.channels_for_friend(friend.id)
        except Exception as exc:  # noqa: BLE001
            log.debug(
                "StatusPublisher: channels_for_friend(%s) failed: %s",
                friend.id,
                exc,
            )
            return

        if not channels:
            return

        primary = next((c for c in channels if c.is_primary), channels[0])

        if primary.channel == "telegram":
            if self._channels is None:
                return
            try:
                tg = self._channels.get("telegram")
            except (KeyError, AttributeError):
                return
            send_card = getattr(tg, "send_status_card", None)
            if send_card is None:
                return
            try:
                chat_id = int(primary.handle)
            except (TypeError, ValueError):
                log.debug(
                    "StatusPublisher: invalid telegram chat_id %r for friend %s",
                    primary.handle,
                    friend.id,
                )
                return
            try:
                await send_card(chat_id, update)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "StatusPublisher: send_status_card failed (friend=%s): %s",
                    friend.id,
                    exc,
                )
            return

        # jarvis_pubkey: Federation kommt spaeter (F5+); in F4 stub-only
        log.debug(
            "StatusPublisher: channel %r noch nicht implementiert (friend=%s)",
            primary.channel,
            friend.id,
        )
