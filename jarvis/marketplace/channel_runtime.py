"""Apply a channel connect/disconnect to the LIVE ChannelManager — no restart.

The marketplace connect/disconnect handlers persist the channel config + secret
first; this seam then makes the change take effect immediately on the running
process. It rebuilds the manager context from the freshly written config,
reloads just the one channel, and rebinds the chat-bridge consumer to the new
instance (see ChannelManager.reload + ChannelChatBridge.refresh).

Best-effort by design: with no live manager (headless / early boot) it returns
``False`` and the persisted config simply takes effect on the next start.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.channels.manager import ChannelContext
from jarvis.core.config import load_config

log = logging.getLogger(__name__)


async def apply_channel_live(app_state: Any, name: str) -> bool:
    """Reload channel ``name`` against current config. Returns True if applied live.

    Returns ``False`` (no error) when there is no running ChannelManager, or
    when the live reload fails — the on-disk config still guarantees the change
    on the next restart.
    """
    manager = getattr(app_state, "channel_manager", None)
    if manager is None:
        log.info(
            "channel '%s' config persisted; no live ChannelManager — applies on next start",
            name,
        )
        return False

    try:
        # load_config reads + parses the TOML from disk; keep it off the event loop.
        cfg = await asyncio.to_thread(load_config)
        integrations = cfg.integrations
        fresh_ctx = ChannelContext(
            bus=manager.context.bus,
            friend_registry=manager.context.friend_registry,
            config={
                "telegram_config": integrations.telegram,
                "discord_config": integrations.discord,
            },
        )
        manager.set_context(fresh_ctx)
        await manager.reload(name)
        # The bridge consumer was bound to the OLD instance's messages() iterator;
        # rebind it to the new one, else inbound messages are silently dropped.
        bridge = getattr(app_state, "channel_chat_bridge", None)
        if bridge is not None:
            await bridge.refresh(name)
        else:
            log.warning(
                "channel '%s' reloaded but no ChannelChatBridge on app.state — "
                "inbound messages will not be consumed until the next full start",
                name,
            )
        log.info("channel '%s' reloaded live", name)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "live reload of channel '%s' failed (config persisted, will apply on next start): %s",
            name,
            exc,
        )
        return False
