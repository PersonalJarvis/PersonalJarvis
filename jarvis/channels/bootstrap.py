# === F-FRIENDS [F0] · feature/friends-section · ruben-2026-04-30 ===
"""Bootstrap helper for the channel system.

A single function (:func:`bootstrap_channels`) that the caller
(typically ``jarvis.ui.web.server``) invokes to:

1. Instantiate and open the :class:`FriendRegistry`.
2. Build the :class:`ChannelContext` with bus + friends + relevant configs.
3. Create the :class:`ChannelManager` and start all registered channels
   — fail-tolerant.

Returns the pair ``(manager, registry)`` so the caller can later invoke
``shutdown_channels`` cleanly (symmetric lifecycle).
"""
from __future__ import annotations

import logging
from pathlib import Path

from jarvis.channels.manager import ChannelContext, ChannelManager
from jarvis.core.bus import EventBus
from jarvis.core.config import DiscordConfig, TelegramConfig
from jarvis.friends.registry import FriendRegistry

log = logging.getLogger(__name__)


async def bootstrap_channels(
    *,
    bus: EventBus,
    telegram_config: TelegramConfig | None = None,
    discord_config: DiscordConfig | None = None,
    friends_db_path: str | Path = "data/friends.db",
    auto_start: bool = True,
) -> tuple[ChannelManager, FriendRegistry]:
    """Initializes FriendRegistry + ChannelManager and starts all channels."""
    registry = FriendRegistry(friends_db_path)
    await registry.open()

    cfg = telegram_config if telegram_config is not None else TelegramConfig()
    dc_cfg = discord_config if discord_config is not None else DiscordConfig()
    context = ChannelContext(
        bus=bus,
        friend_registry=registry,
        config={"telegram_config": cfg, "discord_config": dc_cfg},
    )
    manager = ChannelManager(context)

    if auto_start:
        errors = await manager.start_all()
        if errors:
            for name, err in errors.items():
                log.warning("Channel '%s' Start fehlgeschlagen: %s", name, err)
        log.info(
            "Channels initialisiert: started=%s failed_load=%s start_errors=%s",
            manager.started(),
            list(manager.failed()),
            list(errors),
        )

    return manager, registry


async def shutdown_channels(
    manager: ChannelManager, registry: FriendRegistry
) -> None:
    """Symmetric helper: stop all channels and close the registry."""
    try:
        await manager.stop_all()
    finally:
        await registry.close()
